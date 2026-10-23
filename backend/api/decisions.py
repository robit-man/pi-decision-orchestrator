import copy
import json

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError

from backend.api.deps import operator, viewer
from backend.decisions.schema import Invocation, Workflow
from backend.decisions.templates import templates
from backend.decisions.validation import validate_workflow
from backend.errors import DomainError

router = APIRouter(prefix="/api/v1/decisions", tags=["Decisions"])


def validation_report(data):
    try:
        _, warnings = validate_workflow(data, publish=True)
        return {"valid": True, "errors": [], "warnings": warnings}
    except (ValidationError, DomainError) as error:
        messages = (
            [f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in error.errors()]
            if isinstance(error, ValidationError)
            else [error.message]
        )
        return {"valid": False, "errors": messages, "warnings": []}


def save_draft(store, payload, decision_id=None):
    current = store.get("decision", decision_id) if decision_id else {}
    allowed = set(Workflow.model_fields) - {"published_version", "version", "revision"}
    clean = {
        **{key: value for key, value in current.items() if key in allowed},
        **{key: value for key, value in payload.items() if key in allowed},
    }
    name = clean.get("name", "")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
        raise DomainError("decision_name", "Enter a decision name between 1 and 120 characters.")
    try:
        if len(json.dumps(clean, allow_nan=False)) > 262144:
            raise DomainError("workflow_size", "Workflow definitions are limited to 256 KiB.", 413)
    except (ValueError, TypeError):
        raise DomainError("workflow_json", "Workflow values must be valid finite JSON.") from None
    if decision_id and payload.get("revision") is None:
        raise DomainError("revision_required", "Reload the current draft before saving it.", 409)
    clean.update(
        {"version": current.get("version", 1), "published_version": current.get("published_version")}
    )
    if clean.get("enabled") and not clean["published_version"]:
        clean["enabled"] = False
    result = store.put("decision", clean, decision_id, payload.get("revision"))
    return {**result, "validation": validation_report(result)}


@router.get("")
async def list_decisions(
    request: Request,
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: str = Query("", max_length=120),
    user=Depends(viewer),
):
    return request.app.state.store.list("decision", limit, offset, search)


@router.get("/templates")
async def built_in_templates(user=Depends(viewer)):
    return {"items": templates()}


@router.post("/validate")
async def validate_decision(data: dict, user=Depends(operator)):
    return validation_report(data)


@router.post("/import", status_code=201)
async def import_decision(data: dict, request: Request, user=Depends(operator)):
    if (
        data.get("format") != "pi-decision"
        or data.get("format_version") != 1
        or not isinstance(data.get("workflow"), dict)
    ):
        raise DomainError("import_format", "Use a version 1 Pi Decision workflow export.")
    workflow = copy.deepcopy(data["workflow"])
    workflow["enabled"] = False
    for action in workflow.get("actions", []):
        action["authorized"] = False
    result = save_draft(request.app.state.store, workflow)
    request.app.state.store.audit(user["username"], "decision.import", result["id"])
    return {**result, "notice": "Imported as a disabled draft. Action authorizations must be approved again."}


@router.post("", status_code=201)
async def create_decision(data: dict, request: Request, user=Depends(operator)):
    result = save_draft(request.app.state.store, data)
    request.app.state.store.audit(user["username"], "decision.create", result["id"])
    request.app.state.store.event("decisions_changed", {"id": result["id"]})
    return result


@router.get("/{decision_id}")
async def get_decision(decision_id: str, request: Request, user=Depends(viewer)):
    result = request.app.state.store.get("decision", decision_id)
    return {**result, "validation": validation_report(result)}


@router.patch("/{decision_id}")
async def update_decision(decision_id: str, data: dict, request: Request, user=Depends(operator)):
    result = save_draft(request.app.state.store, data, decision_id)
    request.app.state.store.audit(user["username"], "decision.update", decision_id)
    request.app.state.store.event("decisions_changed", {"id": decision_id})
    return result


@router.post("/{decision_id}/publish")
async def publish(decision_id: str, data: dict, request: Request, user=Depends(operator)):
    store = request.app.state.store
    current = store.get("decision", decision_id)
    if data.get("revision") != current["revision"]:
        raise DomainError("revision_conflict", "Reload the current draft before publishing.", 409)
    workflow, _ = validate_workflow(current, publish=True)
    workflow.version = (current.get("published_version") or 0) + 1
    workflow.published_version = workflow.version
    workflow.enabled = bool(data.get("enabled", current.get("enabled", False)))
    config = workflow.model_dump(exclude={"revision"})
    with store.transaction():
        store.put(
            "version",
            {
                "name": current["name"],
                "decision_id": decision_id,
                "version": workflow.version,
                "config": config,
            },
            f"{decision_id}:{workflow.version}",
        )
        result = store.put("decision", config, decision_id, current["revision"])
    store.audit(user["username"], "decision.publish", f"{decision_id}:{workflow.version}")
    store.event("decisions_changed", {"id": decision_id})
    return {**result, "validation": {"valid": True, "errors": [], "warnings": []}}


@router.get("/{decision_id}/versions")
async def versions(decision_id: str, request: Request, user=Depends(viewer)):
    request.app.state.store.get("decision", decision_id)
    return {
        "items": [
            item
            for item in request.app.state.store.find("version", decision_id=decision_id)
            if item["decision_id"] == decision_id
        ]
    }


@router.get("/{decision_id}/export")
async def export_decision(decision_id: str, request: Request, user=Depends(viewer)):
    current = request.app.state.store.get("decision", decision_id)
    workflow = {key: value for key, value in current.items() if key in Workflow.model_fields}
    return {"format": "pi-decision", "format_version": 1, "workflow": workflow}


@router.post("/{decision_id}/duplicate", status_code=201)
async def duplicate(decision_id: str, request: Request, user=Depends(operator)):
    workflow = request.app.state.store.get("decision", decision_id)
    workflow["name"] = (workflow["name"] + " copy")[:120]
    workflow["enabled"] = False
    for action in workflow.get("actions", []):
        action["authorized"] = False
    result = save_draft(request.app.state.store, workflow)
    request.app.state.store.audit(user["username"], "decision.duplicate", result["id"])
    return result


@router.delete("/{decision_id}")
async def delete_decision(decision_id: str, request: Request, user=Depends(operator)):
    store = request.app.state.store
    if store.one(
        "SELECT id FROM executions WHERE decision_id=? AND status IN ('running','queued')", (decision_id,)
    ):
        raise DomainError("workflow_busy", "Cancel pending executions before deleting this decision.", 409)
    store.delete("decision", decision_id)
    for version in store.find("version", decision_id=decision_id):
        if version["decision_id"] == decision_id:
            store.delete("version", version["id"])
    for connection in store.find("connection"):
        if decision_id in connection["decision_ids"]:
            connection["decision_ids"].remove(decision_id)
            store.put("connection", connection, connection["id"])
    store.audit(user["username"], "decision.delete", decision_id)
    return {"success": True}


@router.post("/{decision_id}/test")
async def test_decision(decision_id: str, data: Invocation, request: Request, user=Depends(operator)):
    data.dry_run = True
    return await request.app.state.engine.submit(decision_id, data, actor=user["username"], draft=True)


@router.post("/{decision_id}/execute")
async def execute_decision(decision_id: str, data: Invocation, request: Request, user=Depends(operator)):
    return await request.app.state.engine.submit(decision_id, data, actor=user["username"])
