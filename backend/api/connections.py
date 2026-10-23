import hmac

from fastapi import APIRouter, Depends, Query, Request

from backend.api.deps import admin, operator, viewer
from backend.errors import DomainError

router = APIRouter(prefix="/api/v1", tags=["Connections"])


def public_connection(app, connection):
    result = app.connections.get(connection["id"])
    try:
        result["health"] = app.store.get("connection_health", connection["id"])
    except DomainError:
        result["health"] = {"status": "untested", "last_success": None, "last_error": None}
    return result


@router.get("/connections")
async def connections(
    request: Request,
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: str = Query("", max_length=120),
    user=Depends(viewer),
):
    app = request.app.state
    result = app.store.list("connection", limit, offset, search)
    result["items"] = [public_connection(app, item) for item in result["items"]]
    return result


@router.post("/connections", status_code=201)
async def create(data: dict, request: Request, user=Depends(admin)):
    credentials = data.pop("credentials", None)
    result = request.app.state.connections.save(data, credentials=credentials)
    await request.app.state.connections.reload()
    request.app.state.store.audit(user["username"], "connection.create", result["id"])
    return result


@router.patch("/connections/{connection_id}")
async def update(connection_id: str, data: dict, request: Request, user=Depends(admin)):
    app = request.app.state
    credentials = data.pop("credentials", None)
    current = app.store.get("connection", connection_id)
    if data.get("revision") is None:
        raise DomainError("revision_required", "Reload this connection before saving.", 409)
    merged = {
        **{key: value for key, value in current.items() if key not in {"id", "created_at", "updated_at"}},
        **data,
    }
    result = app.connections.save(merged, connection_id, credentials)
    await app.connections.reload()
    app.store.audit(user["username"], "connection.update", connection_id)
    return result


@router.delete("/connections/{connection_id}")
async def delete(connection_id: str, request: Request, user=Depends(admin)):
    app = request.app.state
    for workflow in app.store.find("decision"):
        if (
            workflow.get("input_connection_id") == connection_id
            or workflow.get("provider") == connection_id
            or any(action.get("connection_id") == connection_id for action in workflow.get("actions", []))
        ):
            raise DomainError(
                "connection_in_use", "Remove this connection from its workflows before deleting it.", 409
            )
    app.store.delete("connection", connection_id)
    app.vault.delete(connection_id)
    await app.connections.reload()
    app.store.audit(user["username"], "connection.delete", connection_id)
    return {"success": True}


@router.post("/connections/{connection_id}/test")
async def test(connection_id: str, request: Request, user=Depends(admin)):
    request.app.state.store.audit(user["username"], "connection.test", connection_id)
    return await request.app.state.connections.test(connection_id)


@router.post("/connections/{connection_id}/read")
async def read(connection_id: str, request: Request, user=Depends(operator)):
    from backend.security.vault import redact

    return {"state": redact(await request.app.state.connections.read(connection_id))}


@router.post("/hooks/{connection_id}", status_code=202)
async def webhook(connection_id: str, data: dict, request: Request):
    app = request.app.state
    app.limiter.check("hook:" + connection_id, 60)
    connection = app.connections.get(connection_id)
    if connection["type"] != "webhook":
        raise DomainError("not_found", "Webhook not found", 404)
    secret = app.vault.get(connection_id).get("webhook_key", "")
    supplied = request.headers.get("x-webhook-key", "")
    if not secret or not hmac.compare_digest(secret.encode(), supplied.encode()):
        raise DomainError("webhook_authentication", "Webhook authentication failed.", 401)
    executions = await app.connections.ingest(connection_id, data, request.headers.get("idempotency-key"))
    return {"execution_ids": [execution["id"] for execution in executions]}
