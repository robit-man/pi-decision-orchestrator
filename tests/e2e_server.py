"""Explicit local browser-test provider. Never imported by the production application."""

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import json

from backend.config import Settings
from backend.main import create_app

os.environ.setdefault("PI_ADMIN_PASSWORD", "browser-test-password")
settings = Settings(
    data_dir=Path("/tmp/pi-decision-e2e/data"),
    config_dir=Path("/tmp/pi-decision-e2e/config"),
    ollama_url="http://127.0.0.1:8766/fixture-provider",
    testing=True,
)
app = create_app(settings)
provider = FastAPI()
installed = {"laya:latest", "tev1:0.8b", "qwen3:0.6b"}


@provider.get("/api/version")
async def version():
    return {"version": "0.40.8"}


@provider.get("/api/tags")
async def tags():
    return {
        "models": [{"name": name, "digest": "fixture:" + name, "size": 1000} for name in sorted(installed)]
    }


@provider.get("/api/ps")
async def loaded():
    return {"models": []}


@provider.post("/api/generate")
async def generate():
    return {"done": True}


@provider.post("/v1/systemone")
async def infer(request: Request):
    data = await request.json()
    await asyncio.sleep(0.1)
    answers = {}
    for name, question in data["questions"].items():
        if question["type"] == "choice":
            choices = list(question["criteria"])
            selected = "reroute" if "reroute" in choices else choices[0]
            answers[name] = {
                "type": "choice",
                "choice": selected,
                "probabilities": {
                    key: 0.9 if key == selected else 0.1 / (len(choices) - 1) for key in choices
                },
            }
        elif question["type"] == "noul":
            answers[name] = {"type": "noul", "noul": 0.9}
        else:
            n = len(question["criteria"])
            answers[name] = {
                "type": "score",
                "score": n - 1.1,
                "probabilities": {str(i): 0.9 if i == n - 1 else 0.1 / (n - 1) for i in range(n)},
                "legend": question["criteria"],
            }
    return {"answers": answers}


# Insert ahead of the SPA fallback only in this explicit test harness.
app.router.routes.insert(
    0, __import__("starlette.routing", fromlist=["Mount"]).Mount("/fixture-provider", provider)
)


@provider.post("/api/pull")
async def pull(request: Request):
    data = await request.json()
    name = data["model"]
    installed.add(name if ":" in name else name + ":latest")

    async def progress():
        for value in [0, 50, 100]:
            yield (
                json.dumps(
                    {
                        "status": "success" if value == 100 else "pulling fixture",
                        "completed": value,
                        "total": 100,
                    }
                )
                + "\n"
            )
            await asyncio.sleep(0.1)

    return StreamingResponse(progress(), media_type="application/x-ndjson")
