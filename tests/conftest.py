import copy
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app


@pytest.fixture
def runtime(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
        ollama_url="http://ollama.test",
        testing=True,
        telemetry_seconds=60,
    )
    app = create_app(settings)
    with respx.mock(assert_all_called=False) as provider:
        provider.get("http://ollama.test/api/version").mock(
            return_value=httpx.Response(200, json={"version": "0.40.8"})
        )
        provider.get("http://ollama.test/api/tags").mock(
            return_value=httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "laya:latest", "digest": "sha256:fixture-model", "size": 1000},
                        {"name": "qwen3:0.6b", "digest": "sha256:chat-fixture", "size": 1000},
                    ]
                },
            )
        )
        provider.get("http://ollama.test/api/ps").mock(return_value=httpx.Response(200, json={"models": []}))
        provider.post("http://ollama.test/api/generate").mock(
            return_value=httpx.Response(200, json={"done": True})
        )

        def infer(request):
            questions = json.loads(request.content)["questions"]
            answers = {}
            for name, question in questions.items():
                if question["type"] == "choice":
                    choices = list(question["criteria"])
                    selected = "reroute" if "reroute" in choices else choices[0]
                    answers[name] = {
                        "type": "choice",
                        "choice": selected,
                        "probabilities": {
                            choice: 0.9 if choice == selected else 0.1 / (len(choices) - 1)
                            for choice in choices
                        },
                        "confidence": 0.7,
                    }
                elif question["type"] == "noul":
                    answers[name] = {"type": "noul", "noul": 0.92}
                else:
                    count = len(question["criteria"])
                    answers[name] = {
                        "type": "score",
                        "score": count - 1.2,
                        "probabilities": {
                            str(index): 0.9 if index == count - 1 else 0.1 / (count - 1)
                            for index in range(count)
                        },
                        "legend": question["criteria"],
                        "confidence": 0.7,
                    }
            return httpx.Response(200, json={"answers": answers})

        decision_route = provider.post("http://ollama.test/v1/systemone").mock(side_effect=infer)
        with TestClient(app, raise_server_exceptions=True) as client:
            initial = (settings.config_dir / "initial-admin.txt").read_text().splitlines()
            password = initial[1].split(": ", 1)[1]
            response = client.post("/api/v1/auth/login", json={"username": "admin", "password": password})
            assert response.status_code == 200, response.text
            client.headers["X-CSRF-Token"] = response.json()["csrf"]
            yield client, app, provider, decision_route


@pytest.fixture
def workflow(runtime):
    client, _, _, _ = runtime
    template = copy.deepcopy(client.get("/api/v1/decisions/templates").json()["items"][0])
    template["workflow"]["model"] = "laya"
    response = client.post("/api/v1/decisions", json=template["workflow"])
    assert response.status_code == 201, response.text
    return response.json(), template["sample"]
