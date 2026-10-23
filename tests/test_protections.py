import copy
import json
import sqlite3
import time
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.decisions.evaluation import configuration_digest, quality_metrics
from backend.decisions.schema import Action
from backend.decisions.validation import check_schema, validate_data
from backend.errors import DomainError


def test_policy_is_rechecked_after_later_inference(runtime, workflow, monkeypatch):
    client, app, _, route = runtime
    saved, sample = workflow
    saved["actions"][0]["authorized"] = True
    saved["nodes"] = [
        {"id": str(i), "type": t, "name": t, "config": {}}
        for i, t in enumerate(["input", "inference", "evaluate", "inference", "action"])
    ]
    saved["edges"] = [{"id": str(i), "source": str(i), "target": str(i + 1)} for i in range(4)]
    saved = client.patch("/api/v1/decisions/" + saved["id"], json=saved).json()
    pub = client.post(
        f"/api/v1/decisions/{saved['id']}/publish", json={"revision": saved["revision"], "enabled": True}
    )
    assert pub.status_code == 200, pub.text
    calls = []

    async def infer(*args, **kwargs):
        p = 0.99 if not calls else 0.51
        calls.append(p)
        return {
            "model": "laya",
            "model_digest": "fixture",
            "inference_ms": 1,
            "answers": {
                "next_action": {
                    "selected": "reroute",
                    "probabilities": {"reroute": p, "stop": 1 - p},
                    "selected_probability": p,
                    "margin": 2 * p - 1,
                }
            },
        }

    dispatch = AsyncMock(return_value={"status": "executed"})
    monkeypatch.setattr(app.state.engine, "_infer", infer)
    monkeypatch.setattr(app.state.connections, "execute_action", dispatch)
    response = client.post(
        f"/api/v1/decisions/{saved['id']}/execute", json={"state": sample, "dry_run": False}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "abstained"
    dispatch.assert_not_called()


def test_output_contract_precedes_action_dispatch(runtime, workflow, monkeypatch):
    client, app, _, _ = runtime
    saved, sample = workflow
    saved["actions"][0]["authorized"] = True
    saved["output_schema"] = {"type": "object", "required": ["impossible_output"]}
    saved = client.patch("/api/v1/decisions/" + saved["id"], json=saved).json()
    client.post(
        f"/api/v1/decisions/{saved['id']}/publish", json={"revision": saved["revision"], "enabled": True}
    )
    dispatch = AsyncMock(return_value={"status": "executed"})
    monkeypatch.setattr(app.state.connections, "execute_action", dispatch)
    result = client.post(
        f"/api/v1/decisions/{saved['id']}/execute", json={"state": sample, "dry_run": False}
    ).json()
    assert result["status"] == "failed"
    dispatch.assert_not_called()


@pytest.mark.parametrize("failure", ["running", "result", "event"])
def test_storage_failure_does_not_kill_worker(runtime, workflow, monkeypatch, failure):
    client, app, _, _ = runtime
    saved, sample = workflow
    execute = app.state.store.execute
    failed = False

    def full(sql, args=()):
        nonlocal failed
        trigger = (
            "status='running'" in sql
            if failure == "running"
            else "model_digest=?,result=?" in sql
            if failure == "result"
            else sql.startswith("INSERT INTO events") and args[0] == "execution" and "running" in args[1]
        )
        if trigger and not failed:
            failed = True
            raise sqlite3.OperationalError("database or disk is full")
        return execute(sql, args)

    monkeypatch.setattr(app.state.store, "execute", full)
    client.post(f"/api/v1/decisions/{saved['id']}/test", json={"state": sample})
    assert failed
    assert not app.state.engine.worker.done()
    assert app.state.engine.queue._unfinished_tasks == 0
    monkeypatch.setattr(app.state.store, "execute", execute)
    response = client.post(f"/api/v1/decisions/{saved['id']}/test", json={"state": sample})
    assert response.json()["status"] == "completed", response.text


def test_connection_save_and_secret_update_are_atomic(runtime):
    client, app, _, _ = runtime
    c = client.post("/api/v1/connections", json={"name": "Local", "type": "json", "enabled": True}).json()
    response = client.patch(
        "/api/v1/connections/" + c["id"],
        json={
            "revision": c["revision"],
            "name": "Should roll back",
            "credentials": {"unrecognized": "secret"},
        },
    )
    assert response.status_code == 400
    assert app.state.store.get("connection", c["id"])["name"] == "Local"


def test_credentials_do_not_escape_via_schema_errors_or_endpoints(runtime):
    client, app, _, _ = runtime
    with pytest.raises(DomainError) as rejected:
        validate_data(
            {"account": "fixture-credential-123"},
            {"type": "object", "properties": {"account": {"type": "integer"}}},
        )
    assert "fixture-credential" not in rejected.value.message
    response = client.post(
        "/api/v1/connections",
        json={
            "name": "Bad URL",
            "endpoint": "https://example.com/?api_key=fixture-secret",
            "allowed_hosts": ["example.com"],
        },
    )
    assert response.status_code == 422
    c = client.post(
        "/api/v1/connections",
        json={"name": "Encrypted", "type": "json", "credentials": {"token": "fixture-hidden-token"}},
    ).json()
    assert "fixture-hidden-token" not in json.dumps(c)
    assert (
        b"fixture-hidden-token"
        not in app.state.store.one("SELECT encrypted FROM secrets WHERE owner=?", (c["id"],))["encrypted"]
    )
    assert app.state.vault.scrub(c["id"], {"arbitrary": "echo fixture-hidden-token"}) == {
        "arbitrary": "echo [redacted]"
    }


def test_mqtt_connection_contract_precedes_publication(runtime, monkeypatch):
    client, app, _, _ = runtime
    c = client.post(
        "/api/v1/connections",
        json={
            "name": "MQTT output",
            "type": "mqtt",
            "direction": "output",
            "endpoint": "mqtts://broker.example:8883",
            "topic": "decisions/action",
            "allowed_hosts": ["broker.example"],
            "enabled": True,
            "allow_output": True,
            "output_schema": {"type": "object", "required": ["required_field"]},
        },
    )
    assert c.status_code == 201, c.text
    publish = AsyncMock()
    monkeypatch.setattr("backend.connections.manager.MQTTLink.publish", publish)
    action = Action(
        id="mqtt", name="MQTT", type="mqtt", connection_id=c.json()["id"], authorized=True, payload={}
    )

    async def send():
        with pytest.raises(DomainError):
            await app.state.connections.execute_action(
                action, {"execution": {"id": "test"}, "state": {}, "answers": {}}, []
            )

    client.portal.call(send)
    publish.assert_not_called()


def test_calibration_identity_changes_when_draft_content_changes(runtime, workflow):
    client, app, _, _ = runtime
    saved, _ = workflow
    original = configuration_digest(saved)
    changed = copy.deepcopy(saved)
    changed["questions"]["next_action"]["instructions"] += " Different behavior."
    assert configuration_digest(changed) != original
    changed = copy.deepcopy(saved)
    changed["revision"] += 1
    changed["enabled"] = True
    assert configuration_digest(changed) == original


def test_regex_contracts_are_rejected_before_execution():
    with pytest.raises(DomainError, match="Regular expressions"):
        check_schema({"type": "string", "pattern": "^(a+)+$"})


def test_dataset_import_is_atomic_and_queries_are_dataset_scoped(runtime):
    client, app, _, _ = runtime
    a = client.post("/api/v1/datasets", json={"name": "A"}).json()
    b = client.post("/api/v1/datasets", json={"name": "B"}).json()
    for index in range(205):
        app.state.store.put("case", {"name": str(index), "dataset_id": b["id"], "input": {}, "expected": {}})
    client.post(f"/api/v1/datasets/{a['id']}/cases", json={"name": "Visible", "input": {}, "expected": {}})
    assert len(client.get(f"/api/v1/datasets/{b['id']}/cases").json()["items"]) == 205
    invalid = client.post(
        f"/api/v1/datasets/{a['id']}/import",
        json={"cases": [{"input": {}, "expected": {}}, {"input": {}, "images": ["invalid-base64"]}]},
    )
    assert invalid.status_code == 400
    assert len(client.get(f"/api/v1/datasets/{a['id']}/cases").json()["items"]) == 1


def test_quality_metrics_separate_correctness_from_acceptance():
    result = quality_metrics(
        [
            {
                "status": "abstained",
                "expected": {"urgent": True},
                "answers": {
                    "urgent": {
                        "selected": True,
                        "selected_probability": 0.6,
                        "probabilities": {"true": 0.6, "false": 0.4},
                    }
                },
            }
        ]
    )
    assert result["accuracy"] == 1
    assert result["abstention_rate"] == 1
    assert result["brier_score"] == pytest.approx(0.32)


def test_csrf_roles_and_api_token_rate_limits(runtime):
    client, app, _, _ = runtime
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/v1/decisions", json={"name": "Rejected"}).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    token = client.post("/api/v1/auth/tokens", json={"name": "Viewer", "role": "viewer", "rate": 3}).json()[
        "token"
    ]
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/api/v1/models", headers=headers).status_code == 200
    assert client.post("/api/v1/decisions", json={"name": "Forbidden"}, headers=headers).status_code == 403
    assert client.get("/api/v1/system/status", headers=headers).status_code == 200
    assert client.get("/api/v1/system/status", headers=headers).status_code == 429


def test_cross_origin_and_unauthenticated_requests_are_rejected(runtime):
    client, _, _, _ = runtime
    assert client.get("/api/v1/models", headers={"Origin": "http://attacker.example"}).status_code == 403
    client.cookies.clear()
    assert client.get("/api/v1/models").status_code == 401
    assert client.get("/api/v1/health").status_code == 200
