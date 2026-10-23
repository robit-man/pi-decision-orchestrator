import asyncio
import json
import os
import signal
import statistics
import time

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from backend.api.deps import admin, operator, viewer
from backend.errors import DomainError

router = APIRouter(prefix="/api/v1", tags=["System"])


def runtime_status(app):
    recent = app.store.rows(
        "SELECT latency_ms,status FROM executions WHERE created>? ORDER BY created DESC LIMIT 1000",
        (time.time() - 3600,),
    )
    latencies = sorted(row["latency_ms"] for row in recent if row["latency_ms"] is not None)
    return {
        "queue": {
            "depth": app.engine.queue.qsize(),
            "capacity": app.settings.queue_size,
            "active_execution_id": app.engine.running_id,
            "concurrency": 1,
            "paused": app.engine.paused,
            "benchmarking": app.engine.benchmarking,
            "worker_healthy": bool(
                app.engine.worker and not app.engine.worker.done() and not app.engine.storage_error
            ),
        },
        "metrics": {
            "executions_last_hour": len(recent),
            "throughput_per_minute": app.store.one(
                "SELECT COUNT(*) AS n FROM executions WHERE finished>?", (time.time() - 60,)
            )["n"],
            "average_latency_ms": statistics.mean(latencies) if latencies else None,
            "p95_latency_ms": latencies[max(0, int(len(latencies) * 0.95 + 0.999) - 1)]
            if latencies
            else None,
        },
        "enabled_workflows": app.store.one(
            "SELECT COUNT(*) AS n FROM objects WHERE kind='decision' AND json_extract(payload,'$.enabled')=1"
        )["n"],
        "enabled_connections": app.store.one(
            "SELECT COUNT(*) AS n FROM objects WHERE kind='connection' AND json_extract(payload,'$.enabled')=1"
        )["n"],
    }


@router.get("/health")
async def health(request: Request):
    engine = request.app.state.engine
    if engine.storage_error or not engine.worker or engine.worker.done():
        raise DomainError("runtime_unhealthy", "The execution worker or storage is unavailable.", 503)
    return {"status": "ok", "service": "pi-decision", "version": "1.0.0"}


@router.get("/system/status")
async def status(request: Request, user=Depends(viewer)):
    app = request.app.state
    local = await app.models.local()
    return {
        **app.monitor.latest,
        **runtime_status(app),
        "ollama": {key: local.get(key) for key in ("online", "version", "compatible", "loaded")},
        "service_started_at": app.started_at,
    }


@router.post("/system/workflows")
async def workflow_control(data: dict, request: Request, user=Depends(operator)):
    action = data.get("action")
    if action not in {"start", "stop", "reload"}:
        raise DomainError("invalid_control", "Choose start, stop, or reload.")
    app = request.app.state
    if action == "stop":
        app.engine.paused = True
        if app.engine.running_id:
            app.engine.cancel(app.engine.running_id)
        for row in app.store.rows("SELECT id FROM executions WHERE status='queued'"):
            app.engine.cancel(row["id"])
    elif action == "start":
        app.engine.paused = False
    else:
        await app.connections.reload()
    app.store.audit(user["username"], "workflows." + action)
    return {"paused": app.engine.paused, "action": action}


@router.get("/settings")
async def settings(request: Request, user=Depends(viewer)):
    app = request.app.state
    return {
        "default_model": app.store.setting("default_model", "laya"),
        "keep_alive": app.store.setting("keep_alive", "5m"),
        "history_limit": app.store.setting("history_limit", app.settings.history_limit),
        "listen_host": app.store.setting("listen_host", app.settings.host),
        "listen_port": app.store.setting("listen_port", app.settings.port),
        "queue_size": app.settings.queue_size,
        "concurrency": 1,
        "ollama_url": app.settings.ollama_url,
        "secure_cookie": app.settings.secure_cookie,
        "onboarding_complete": app.store.setting("onboarding_complete", False),
        "data_directory": str(app.settings.data_dir),
        "configuration_directory": str(app.settings.config_dir),
    }


@router.patch("/settings")
async def update_settings(data: dict, request: Request, user=Depends(admin)):
    import ipaddress
    import re

    app = request.app.state
    allowed = {
        "default_model",
        "keep_alive",
        "history_limit",
        "listen_host",
        "listen_port",
        "onboarding_complete",
    }
    if set(data) - allowed:
        raise DomainError("invalid_setting", "This setting cannot be changed through the dashboard.")
    if "history_limit" in data and not 100 <= int(data["history_limit"]) <= 100000:
        raise DomainError("history_limit", "Retain between 100 and 100,000 executions.")
    if "listen_port" in data and not 1024 <= int(data["listen_port"]) <= 65535:
        raise DomainError("listen_port", "Use an unprivileged port from 1024 to 65535.")
    if "listen_host" in data:
        try:
            ipaddress.ip_address(str(data["listen_host"]))
        except ValueError:
            raise DomainError("listen_host", "Enter an IPv4 or IPv6 listening address.") from None
    if "keep_alive" in data and not re.fullmatch(r"0|[1-9][0-9]{0,3}[smh]", str(data["keep_alive"])):
        raise DomainError("keep_alive", "Use a keep-alive duration such as 5m, 30s, or 0.")
    for key, value in data.items():
        app.store.set_setting(key, value)
    app.store.audit(user["username"], "settings.update")
    return {"success": True, "restart_required": bool({"listen_host", "listen_port"} & set(data))}


@router.post("/system/restart")
async def restart(request: Request, user=Depends(admin)):
    app = request.app.state
    if app.engine.running_id or not app.engine.queue.empty():
        raise DomainError("runtime_busy", "Stop pending executions before restarting the service.", 409)
    app.store.audit(user["username"], "system.restart")
    if not app.settings.testing:
        asyncio.get_running_loop().call_later(1, os.kill, os.getpid(), signal.SIGTERM)
    return {
        "success": True,
        "message": "Service restart requested. Reconnect using the configured address and port.",
    }


@router.get("/audit")
async def audit(
    request: Request, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), user=Depends(admin)
):
    return {
        "items": request.app.state.store.rows(
            "SELECT * FROM audit ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        ),
        "total": request.app.state.store.one("SELECT COUNT(*) AS n FROM audit")["n"],
    }


@router.get("/events/stream")
async def events(request: Request, user=Depends(viewer)):
    app = request.app.state
    if app.sse_clients >= 20:
        raise DomainError("stream_limit", "The live-connection limit has been reached.", 429)
    app.sse_clients += 1
    try:
        last_id = int(request.headers.get("last-event-id", "0"))
    except ValueError:
        last_id = 0
    if not last_id:
        last_id = app.store.one("SELECT COALESCE(MAX(id),0) AS n FROM events")["n"]

    async def stream():
        nonlocal last_id
        try:
            while not await request.is_disconnected():
                from backend.security.auth import digest

                if user["method"] == "token":
                    valid = app.store.one("SELECT id FROM tokens WHERE id=?", (user["id"],))
                else:
                    valid = app.store.one(
                        "SELECT token_hash FROM sessions WHERE token_hash=?",
                        (digest(request.cookies.get("pi_session", "")),),
                    )
                # Expired browser sessions cannot keep an authenticated stream alive indefinitely.
                if not valid or (user.get("expires") and time.time() > user["expires"]):
                    yield "event: session_expired\ndata: {}\n\n"
                    break
                for item in app.store.rows(
                    "SELECT id,type,payload FROM events WHERE id>? ORDER BY id LIMIT 100", (last_id,)
                ):
                    last_id = item["id"]
                    yield f"id: {last_id}\nevent: change\ndata: {json.dumps({'type': item['type'], 'data': json.loads(item['payload'])})}\n\n"
                data = {**app.monitor.latest, **runtime_status(app)}
                yield "event: telemetry\ndata: " + json.dumps(data) + "\n\n"
                await asyncio.sleep(app.settings.telemetry_seconds)
        finally:
            app.sse_clients -= 1

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
    )
