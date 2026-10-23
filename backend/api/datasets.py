import csv
import io
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from backend.api.deps import operator, viewer
from backend.decisions.validation import validate_data
from backend.errors import DomainError

router = APIRouter(prefix="/api/v1", tags=["Evaluation datasets"])


@router.get("/datasets")
async def datasets(request: Request, user=Depends(viewer)):
    return request.app.state.store.list("dataset", 200)


@router.post("/datasets", status_code=201)
async def create(data: dict, request: Request, user=Depends(operator)):
    name = str(data.get("name", "")).strip()
    if not 1 <= len(name) <= 120:
        raise DomainError("dataset_name", "Enter a dataset name between 1 and 120 characters.")
    return request.app.state.store.put(
        "dataset", {"name": name, "description": str(data.get("description", ""))[:2000]}
    )


@router.delete("/datasets/{dataset_id}")
async def delete(dataset_id: str, request: Request, user=Depends(operator)):
    store = request.app.state.store
    if request.app.state.engine.benchmarking:
        raise DomainError("benchmark_busy", "Wait for the benchmark to finish before deleting datasets.", 409)
    store.delete("dataset", dataset_id)
    for case in store.find("case", dataset_id=dataset_id):
        if case["dataset_id"] == dataset_id:
            store.delete("case", case["id"])
    return {"success": True}


@router.get("/datasets/{dataset_id}/cases")
async def cases(dataset_id: str, request: Request, user=Depends(viewer)):
    request.app.state.store.get("dataset", dataset_id)
    return {
        "items": [
            item
            for item in request.app.state.store.find("case", dataset_id=dataset_id)
            if item["dataset_id"] == dataset_id
        ]
    }


def save_case(store, dataset_id, data):
    store.get("dataset", dataset_id)
    if "input" not in data or not isinstance(data.get("expected", {}), dict):
        raise DomainError("case_format", "Each case needs an input and an expected-outcomes object.")
    validate_data(data["input"], {})
    validate_data(data.get("expected", {}), {})
    from backend.decisions.media import validate_media

    validate_media(data.get("images", []))
    dataset = store.get("dataset", dataset_id)
    store.put("dataset", dataset, dataset_id)
    return store.put(
        "case",
        {
            "name": str(data.get("name", "Saved case"))[:120],
            "dataset_id": dataset_id,
            "input": data["input"],
            "expected": data.get("expected", {}),
            "images": data.get("images", []),
        },
    )


@router.post("/datasets/{dataset_id}/cases", status_code=201)
async def create_case(dataset_id: str, data: dict, request: Request, user=Depends(operator)):
    return save_case(request.app.state.store, dataset_id, data)


@router.delete("/datasets/{dataset_id}/cases/{case_id}")
async def delete_case(dataset_id: str, case_id: str, request: Request, user=Depends(operator)):
    store = request.app.state.store
    if store.get("case", case_id)["dataset_id"] != dataset_id:
        raise DomainError("not_found", "Test case not found", 404)
    store.delete("case", case_id)
    store.put("dataset", store.get("dataset", dataset_id), dataset_id)
    return {"success": True}


@router.post("/datasets/{dataset_id}/import")
async def import_cases(dataset_id: str, data: dict, request: Request, user=Depends(operator)):
    if data.get("format") == "csv":
        try:
            rows = [
                {
                    "name": row.get("name", "Imported case"),
                    "input": json.loads(row["input"]),
                    "expected": json.loads(row.get("expected") or "{}"),
                }
                for row in csv.DictReader(io.StringIO(str(data.get("content", ""))))
            ]
        except (ValueError, KeyError, csv.Error):
            raise DomainError(
                "csv_format",
                "CSV needs name, input, and expected columns; input and expected contain JSON values.",
            ) from None
    else:
        rows = data.get("cases", [])
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise DomainError("import_limit", "Import between 1 and 100 cases at a time.")
    # Validate every case before writing any, so malformed imports are not partially applied.
    for row in rows:
        if not isinstance(row, dict) or "input" not in row or not isinstance(row.get("expected", {}), dict):
            raise DomainError("case_format", "Every case requires an input and expected-outcomes object.")
        validate_data(row["input"], {})
        validate_data(row.get("expected", {}), {})
    with request.app.state.store.transaction():
        return {"items": [save_case(request.app.state.store, dataset_id, row) for row in rows]}


@router.get("/datasets/{dataset_id}/export")
async def export_cases(dataset_id: str, request: Request, format: str = "json", user=Depends(viewer)):
    request.app.state.store.get("dataset", dataset_id)
    items = [
        item
        for item in request.app.state.store.find("case", dataset_id=dataset_id)
        if item["dataset_id"] == dataset_id
    ]
    if format == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["name", "input", "expected"])
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    "name": item["name"],
                    "input": json.dumps(item["input"]),
                    "expected": json.dumps(item["expected"]),
                }
            )
        return Response(
            output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="decision-cases.csv"'},
        )
    return {
        "format": "pi-decision-cases",
        "cases": [{key: item.get(key) for key in ("name", "input", "expected", "images")} for item in items],
    }


@router.get("/evaluations")
async def evaluations(request: Request, user=Depends(viewer)):
    return {
        "jobs": request.app.state.store.list("benchmark", 100)["items"],
        "results": request.app.state.store.list("evaluation", 100)["items"],
    }


@router.post("/evaluations", status_code=202)
async def evaluate(data: dict, request: Request, user=Depends(operator)):
    return await request.app.state.evaluations.start(data)


@router.post("/evaluations/cancel")
async def cancel(request: Request, user=Depends(operator)):
    await request.app.state.evaluations.close()
    return {"success": True}
