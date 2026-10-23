import copy

import httpx


def test_authenticated_native_decision_and_persistence(runtime, workflow):
    client, app, provider, route = runtime
    saved, sample = workflow
    result = client.post(f"/api/v1/decisions/{saved['id']}/test", json={"state": sample})
    assert result.status_code == 200, result.text
    execution = result.json()
    assert execution["status"] == "completed"
    assert execution["model_digest"] == "sha256:fixture-model"
    answer = execution["result"]["answers"]["next_action"]
    assert answer["selected"] == "reroute"
    assert set(answer["probabilities"]) == {"continue", "reroute", "stop", "inspect"}
    assert execution["result"]["actions"][0]["status"] == "proposed"
    assert client.get(f"/api/v1/executions/{execution['id']}").json()["result"] == execution["result"]
    assert route.call_count == 1


def test_publish_versions_are_immutable_and_disabled_workflows_reject_execution(runtime, workflow):
    client, _, _, _ = runtime
    saved, sample = workflow
    path = f"/api/v1/decisions/{saved['id']}"
    assert client.post(path + "/execute", json={"state": sample}).status_code == 409
    published = client.post(path + "/publish", json={"revision": saved["revision"], "enabled": True})
    assert published.status_code == 200, published.text
    draft = published.json()
    draft["questions"]["next_action"]["instructions"] = "Edited draft"
    update = client.patch(path, json=draft)
    assert update.status_code == 200, update.text
    versions = client.get(path + "/versions").json()["items"]
    assert versions[0]["config"]["questions"]["next_action"]["instructions"] != "Edited draft"
    response = client.post(path + "/execute", json={"state": sample, "dry_run": True})
    assert response.json()["status"] == "completed", response.text


def test_invalid_input_never_reaches_model(runtime, workflow):
    client, _, _, route = runtime
    saved, sample = workflow
    sample["battery_percent"] = 1000
    result = client.post(f"/api/v1/decisions/{saved['id']}/test", json={"state": sample})
    assert result.status_code == 422, result.text
    assert route.call_count == 0


def test_failed_probability_policy_rejects_actions(runtime, workflow):
    client, _, _, _ = runtime
    saved, sample = workflow
    saved["policy"]["min_probability"] = 0.99
    saved["actions"][0]["authorized"] = True
    client.patch(f"/api/v1/decisions/{saved['id']}", json=saved)
    result = client.post(f"/api/v1/decisions/{saved['id']}/test", json={"state": sample}).json()
    assert result["status"] == "abstained"
    assert result["result"]["policy"]["accepted"] is False


def test_unavailable_provider_fails_safely(runtime, workflow):
    client, _, provider, _ = runtime
    saved, sample = workflow
    provider.post("http://ollama.test/v1/systemone").mock(
        return_value=httpx.Response(500, json={"error": "Model failed"})
    )
    result = client.post(f"/api/v1/decisions/{saved['id']}/test", json={"state": sample}).json()
    assert result["status"] == "failed"
    assert result["result"]["actions"] == []
    assert result["result"]["policy"]["accepted"] is False


def test_cycle_validation_and_unreachable_nodes(runtime, workflow):
    client, _, _, _ = runtime
    saved, _ = workflow
    validated = client.post("/api/v1/decisions/validate", json=saved).json()
    assert validated["valid"]
    graph = copy.deepcopy(saved)
    graph["nodes"] = [
        {"id": "a", "name": "Input", "type": "input"},
        {"id": "b", "name": "Inference", "type": "inference"},
    ]
    graph["edges"] = [{"id": "ab", "source": "a", "target": "b"}, {"id": "ba", "source": "b", "target": "a"}]
    assert "cycles" in str(client.post("/api/v1/decisions/validate", json=graph).json())
    graph["edges"] = []
    assert "Unreachable" in str(client.post("/api/v1/decisions/validate", json=graph).json())


def test_optimistic_revision_conflict(runtime, workflow):
    client, _, _, _ = runtime
    saved, _ = workflow
    path = f"/api/v1/decisions/{saved['id']}"
    assert client.patch(path, json=saved).status_code == 200
    assert client.patch(path, json=saved).status_code == 409


def test_export_import_resets_action_authorization(runtime, workflow):
    client, _, _, _ = runtime
    saved, _ = workflow
    exported = client.get(f"/api/v1/decisions/{saved['id']}/export").json()
    exported["workflow"]["actions"][0]["authorized"] = True
    imported = client.post("/api/v1/decisions/import", json=exported)
    assert imported.status_code == 201, imported.text
    assert imported.json()["enabled"] is False
    assert imported.json()["actions"][0]["authorized"] is False


def test_history_pagination_and_literal_filters(runtime, workflow):
    client, _, _, _ = runtime
    response = client.get("/api/v1/executions", params={"decision_id": "' OR 1=1--", "status": "completed"})
    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert client.get("/api/v1/decisions", params={"search": "%"}).json()["total"] == 0
