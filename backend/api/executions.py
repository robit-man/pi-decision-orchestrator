import json

from fastapi import APIRouter, Depends, Query, Request

from backend.api.deps import operator, viewer

router = APIRouter(prefix="/api/v1/executions", tags=["Executions"])


@router.get("")
async def executions(
    request: Request,
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
    decision_id: str = "",
    status: str = "",
    user=Depends(viewer),
):
    clauses, values = [], []
    if decision_id:
        clauses.append("decision_id=?")
        values.append(decision_id)
    if status:
        clauses.append("status=?")
        values.append(status)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    app = request.app.state
    total = app.store.one("SELECT COUNT(*) AS n FROM executions" + where, values)["n"]  # noqa: S608 -- SQL fragments are constants; all request values are bound parameters.
    rows = app.store.rows(
        "SELECT id,decision_id,version,status,dry_run,created,latency_ms,inference_ms,model,model_digest,result,error FROM executions"  # noqa: S608 -- SQL fragments are constants; all request values are bound parameters.
        + where
        + " ORDER BY created DESC LIMIT ? OFFSET ?",
        (*values, limit, offset),
    )
    for row in rows:
        result = json.loads(row.pop("result")) if row["result"] else {}
        row["answers"] = result.get("answers", {})
        row["accepted"] = result.get("policy", {}).get("accepted", False)
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/{execution_id}")
async def execution(execution_id: str, request: Request, user=Depends(viewer)):
    return request.app.state.engine.execution(execution_id)


@router.post("/{execution_id}/cancel")
async def cancel(execution_id: str, request: Request, user=Depends(operator)):
    request.app.state.store.audit(user["username"], "execution.cancel", execution_id)
    return request.app.state.engine.cancel(execution_id)
