import asyncio
from contextlib import asynccontextmanager
import logging
import os
import secrets
import socket
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psutil
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from backend.api import auth, connections, datasets, decisions, executions, models, system
from backend.config import Settings
from backend.connections.manager import ConnectionManager
from backend.decisions.engine import ExecutionEngine
from backend.decisions.evaluation import EvaluationManager
from backend.errors import DomainError
from backend.inference.manager import ModelManager
from backend.monitoring.system import Monitor
from backend.security.auth import RateLimiter, create_user, hash_password, principal
from backend.security.vault import Vault
from backend.storage.db import Store

LOGGER = logging.getLogger("pi-decision")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        os.umask(0o077)
        settings.config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        store = Store(settings.database)
        app.state.settings, app.state.store = settings, store
        app.state.vault = Vault(store, settings.config_dir / "secrets.key")
        app.state.limiter, app.state.dummy_password = RateLimiter(), hash_password(secrets.token_urlsafe(24))
        if not store.one("SELECT id FROM users LIMIT 1"):
            password = os.getenv("PI_ADMIN_PASSWORD") or secrets.token_urlsafe(20)
            username = os.getenv("PI_ADMIN_USER", "admin")
            create_user(store, username, password)
            credentials_file = settings.config_dir / "initial-admin.txt"
            fd = os.open(credentials_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as output:
                output.write(f"Username: {username}\nPassword: {password}\n")
            LOGGER.info(
                "Administrator initialized; initial credentials are in the protected configuration directory."
            )
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=10),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            trust_env=False,
            follow_redirects=False,
        )
        model_lock = asyncio.Lock()
        app.state.client = client
        app.state.monitor = Monitor(settings, store)
        app.state.models = ModelManager(settings, store, client, app.state.monitor, model_lock)
        app.state.connections = ConnectionManager(store, app.state.vault, client)
        app.state.engine = ExecutionEngine(
            settings, store, app.state.models, app.state.connections, app.state.monitor, client, model_lock
        )
        app.state.connections.engine = app.state.engine
        app.state.evaluations = EvaluationManager(
            store, app.state.engine, app.state.models, app.state.monitor
        )
        app.state.started_at, app.state.sse_clients = time.time(), 0
        app.state.engine.start()
        app.state.monitor.sample()
        telemetry = asyncio.create_task(app.state.monitor.run(), name="telemetry")
        await app.state.connections.reload()

        async def intervals():
            last = {}
            while True:
                for workflow in store.find("decision"):
                    if workflow.get("enabled") and workflow.get("published_version"):
                        published = store.get("version", f"{workflow['id']}:{workflow['published_version']}")[
                            "config"
                        ]
                        workflow = {**published, "id": workflow["id"], "enabled": workflow["enabled"]}
                    if (
                        workflow.get("enabled")
                        and workflow.get("input_source") == "interval"
                        and time.monotonic() - last.get(workflow["id"], 0) >= workflow.get("interval_s", 60)
                    ):
                        last[workflow["id"]] = time.monotonic()
                        try:
                            value = await app.state.connections.read(workflow["input_connection_id"])
                            from backend.decisions.schema import Invocation

                            await app.state.engine.submit(
                                workflow["id"],
                                Invocation(state=value, wait=False, dry_run=False),
                                actor="interval",
                            )
                        except DomainError as error:
                            store.event(
                                "workflow_alert",
                                {"id": workflow["id"], "code": error.code, "message": error.message},
                            )
                await asyncio.sleep(5)

        interval_task = asyncio.create_task(intervals(), name="workflow-intervals")
        try:
            yield
        finally:
            telemetry.cancel()
            interval_task.cancel()
            await asyncio.gather(telemetry, interval_task, return_exceptions=True)
            await app.state.connections.close()
            await app.state.evaluations.close()
            await app.state.models.close()
            await app.state.engine.close()
            await client.aclose()
            store.close()

    app = FastAPI(
        title="Pi Decision Orchestrator",
        version="1.0.0",
        description="Authenticated local-first workflow and decision API. Browser sessions require X-CSRF-Token on writes; agents use Bearer tokens.",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def boundaries(request, call_next):
        try:
            origin = request.headers.get("origin")
            if origin:
                parsed = urlsplit(origin)
                addresses = {
                    "localhost",
                    "127.0.0.1",
                    "::1",
                    socket.gethostname(),
                    socket.gethostname() + ".local",
                }
                for interface in psutil.net_if_addrs().values():
                    addresses.update(
                        item.address.split("%", 1)[0]
                        for item in interface
                        if item.family in {socket.AF_INET, socket.AF_INET6}
                    )
                same_origin = (
                    parsed.scheme in {"http", "https"}
                    and parsed.netloc == request.headers.get("host")
                    and parsed.hostname in addresses
                )
                if not same_origin and origin not in settings.allowed_origins:
                    raise DomainError("origin_rejected", "This browser origin is not permitted.", 403)
            if request.url.path.startswith("/api/") and request.method in {"POST", "PATCH", "PUT"}:
                length = request.headers.get("content-length")
                if length and (not length.isdecimal() or int(length) > settings.max_body):
                    raise DomainError(
                        "request_too_large", "The request body exceeds the configured size limit.", 413
                    )
                buffer = bytearray()
                async for chunk in request.stream():
                    if len(buffer) + len(chunk) > settings.max_body:
                        raise DomainError(
                            "request_too_large", "The request body exceeds the configured size limit.", 413
                        )
                    buffer.extend(chunk)
                request._body = bytes(buffer)
            response = await call_next(request)
        except DomainError as error:
            response = JSONResponse(
                {"error": {"code": error.code, "message": error.message}}, status_code=error.status
            )
        response.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "same-origin",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            }
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, error):
        return JSONResponse(
            {"error": {"code": error.code, "message": error.message}}, status_code=error.status
        )

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def invalid_request(request, error):
        # Do not serialize Pydantic's input field: it can contain passwords or connection secrets.
        fields = [
            {"field": ".".join(map(str, item["loc"])), "message": item["msg"]} for item in error.errors()
        ]
        return JSONResponse(
            {
                "error": {
                    "code": "validation_error",
                    "message": "Correct the highlighted fields.",
                    "fields": fields,
                }
            },
            status_code=422,
        )

    @app.exception_handler(sqlite3.Error)
    async def database_error(request, error):
        return JSONResponse(
            {
                "error": {
                    "code": "storage_unavailable",
                    "message": "Storage is unavailable or full. Free disk space and retry.",
                }
            },
            status_code=503,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request, error):
        LOGGER.error("Request failed safely (%s)", type(error).__name__)
        return JSONResponse(
            {
                "error": {
                    "code": "internal_error",
                    "message": "The operation failed safely. Check service status and retry.",
                }
            },
            status_code=500,
        )

    for module in (auth, system, models, connections, decisions, executions, datasets):
        app.include_router(module.router)

    @app.get("/api/v1/openapi.json", include_in_schema=False)
    async def openapi(request: Request):
        principal(request)
        return app.openapi()

    static = Path(__file__).parent / "static"
    if (static / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def dashboard(path: str):
        if path.startswith("api/"):
            raise DomainError("not_found", "API route not found", 404)
        if (static / "index.html").exists():
            return FileResponse(static / "index.html", headers={"Cache-Control": "no-cache"})
        return HTMLResponse(
            "<h1>Pi Decision Orchestrator</h1><p>Build the frontend production assets before starting the dashboard.</p>",
            status_code=503,
        )

    return app


def run():
    import uvicorn

    settings = Settings()
    host, port = settings.host, settings.port
    if settings.database.exists():
        store = Store(settings.database)
        host, port = store.setting("listen_host", host), int(store.setting("listen_port", port))
        store.close()
    uvicorn.run(
        create_app(settings),
        host=host,
        port=port,
        workers=1,
        limit_concurrency=32,
        timeout_keep_alive=10,
        access_log=False,
    )


if __name__ == "__main__":
    run()
