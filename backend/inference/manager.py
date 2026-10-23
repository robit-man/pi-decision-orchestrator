import asyncio
import json
import platform
import re
import struct
import time

import httpx

from backend.errors import DomainError
from backend.inference.catalog import CATALOG, canonical, catalog_entry
from backend.inference.ollama_chat import OllamaChat
from backend.inference.ollama_decision import OllamaDecision


def check_model_name(name: str):
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", name)
        or ".." in name
        or name.endswith(":cloud")
    ):
        raise DomainError("invalid_model_name", "Use a valid local model name. Cloud models are disabled.")


class ModelManager:
    def __init__(self, settings, store, client, monitor, lock):
        self.settings, self.store, self.client, self.monitor, self.lock = (
            settings,
            store,
            client,
            monitor,
            lock,
        )
        self.job_task = None
        self.cache, self.cache_at = {}, 0.0

    async def local(self, refresh=False):
        if not refresh and time.monotonic() - self.cache_at < 10:
            return self.cache
        try:
            tags, running, version = await asyncio.gather(
                self.client.get(self.settings.ollama_url + "/api/tags"),
                self.client.get(self.settings.ollama_url + "/api/ps"),
                self.client.get(self.settings.ollama_url + "/api/version"),
            )
            tags.raise_for_status()
            version.raise_for_status()
            number = tuple(int(part) for part in re.findall(r"\d+", version.json()["version"])[:3])
            self.cache = {
                "installed": tags.json().get("models", []),
                "loaded": running.json().get("models", []) if running.status_code == 200 else [],
                "version": version.json()["version"],
                "compatible": number >= (0, 40, 0),
                "online": True,
            }
        except (httpx.HTTPError, KeyError, ValueError):
            self.cache = {
                "installed": [],
                "loaded": [],
                "version": None,
                "compatible": False,
                "online": False,
            }
        self.cache_at = time.monotonic()
        return self.cache

    async def list(self):
        local = await self.local()
        entries = {canonical(item["name"]): dict(item) for item in CATALOG}
        for installed in local["installed"]:
            entries.setdefault(canonical(installed["name"]), catalog_entry(installed["name"]))
        for key, item in entries.items():
            installed = next((model for model in local["installed"] if canonical(model["name"]) == key), None)
            item.update(
                {
                    "installed": bool(installed),
                    "size": installed.get("size") if installed else None,
                    "digest": installed.get("digest") if installed else None,
                    "loaded": any(canonical(model["name"]) == key for model in local["loaded"]),
                    "compatibility": self.store.one(
                        "SELECT payload FROM objects WHERE kind='compatibility' AND id=?", (key,)
                    ),
                }
            )
            item["compatibility"] = (
                json.loads(item["compatibility"]["payload"])
                if item["compatibility"]
                else {"status": "unverified"}
            )
        return {
            **local,
            "models": list(entries.values()),
            "default_model": self.store.setting("default_model", "laya"),
            "jobs": self.store.list("model_job", 10)["items"],
            "benchmarks": self.store.list("benchmark", 50)["items"],
        }

    async def digest(self, model):
        local = await self.local()
        match = next(
            (item for item in local["installed"] if canonical(item["name"]) == canonical(model)), None
        )
        if not match:
            raise DomainError(
                "model_not_installed", "Install this model in Model Manager before running the decision.", 409
            )
        return match["digest"], match.get("size", 0)

    async def pull(self, name: str) -> dict:
        check_model_name(name)
        if self.job_task and not self.job_task.done():
            raise DomainError("model_operation_busy", "A model download is already running.", 409)
        if not (await self.local())["compatible"]:
            raise DomainError(
                "ollama_version",
                "Ollama 0.40.0 or newer must be available before installing decision models.",
                409,
            )
        estimated = (catalog_entry(name).get("download_mb") or 1000) * 1_000_000
        if self.monitor.sample()["disk"]["free"] < estimated + 512 * 1024**2:
            raise DomainError(
                "disk_pressure", "There is not enough free storage for this model and the disk reserve.", 409
            )
        job = self.store.put("model_job", {"name": name, "status": "queued", "progress": 0})
        self.job_task = asyncio.create_task(self._pull(job["id"], name), name="model-pull")
        return job

    async def _pull(self, job_id, name):
        try:
            async with self.lock:
                self.monitor.permit()
                async with self.client.stream(
                    "POST",
                    self.settings.ollama_url + "/api/pull",
                    json={"model": name, "stream": True},
                    timeout=1800,
                ) as response:
                    response.raise_for_status()
                    last = None
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if len(line) > 32768:
                            raise ValueError("oversize model status")
                        item = json.loads(line)
                        if item.get("error"):
                            raise DomainError(
                                "model_download_failed",
                                "Ollama could not download this model. Check the model name, network, and available disk.",
                                502,
                            )
                        percent = (
                            int(item.get("completed", 0) * 100 / item["total"]) if item.get("total") else 0
                        )
                        progress = (item.get("status"), percent // 5)
                        if progress != last:
                            status = {
                                "name": name,
                                "status": "downloading",
                                "stage": item.get("status", "Downloading"),
                                "progress": percent,
                                "completed": item.get("completed"),
                                "total": item.get("total"),
                            }
                            self.store.put("model_job", status, job_id)
                            self.store.event("model", {"job_id": job_id, **status})
                            last = progress
            await self.local(refresh=True)
            self.store.put("model_job", {"name": name, "status": "completed", "progress": 100}, job_id)
        except asyncio.CancelledError:
            self.store.put("model_job", {"name": name, "status": "cancelled", "progress": 0}, job_id)
            raise
        except (DomainError, httpx.HTTPError, ValueError, KeyError) as error:
            self.store.put(
                "model_job",
                {
                    "name": name,
                    "status": "failed",
                    "error": error.message
                    if isinstance(error, DomainError)
                    else "Download interrupted. Check network and storage, then retry.",
                },
                job_id,
            )
        finally:
            self.store.event("models_changed", {"job_id": job_id})

    async def unload(self, name):
        check_model_name(name)
        async with self.lock:
            response = await self.client.post(
                self.settings.ollama_url + "/api/generate", json={"model": name, "keep_alive": 0}, timeout=30
            )
            if response.status_code >= 400:
                raise DomainError("model_unload", "The provider could not unload the model.", 502)
        await self.local(refresh=True)

    async def delete(self, name):
        check_model_name(name)
        if self.job_task and not self.job_task.done():
            raise DomainError("model_operation_busy", "Wait for the current model operation to finish.", 409)
        for workflow in self.store.list("decision", 200)["items"]:
            if workflow.get("enabled") and canonical(workflow["model"]) == canonical(name):
                raise DomainError(
                    "model_in_use", "Disable or reassign workflows using this model before removing it.", 409
                )
        async with self.lock:
            response = await self.client.request(
                "DELETE", self.settings.ollama_url + "/api/delete", json={"model": name}
            )
            if response.status_code not in {200, 404}:
                raise DomainError("model_delete", "The provider could not remove the model.", 502)
        await self.local(refresh=True)

    async def verify(self, name, warm=False):
        entry = catalog_entry(name)
        questions = {
            "team": {
                "type": "choice",
                "instructions": "Choose the responsible team.",
                "criteria": {"billing": "Payments and refunds", "technical": "Software failures and outages"},
            },
            "urgent": {"type": "noul", "instructions": "Is this an urgent outage?"},
            "severity": {
                "type": "score",
                "instructions": "Rate operational severity.",
                "criteria": ["Minor inconvenience", "Partial disruption", "Complete outage"],
            },
        }
        digest, size = await self.digest(name)
        result = {
            "name": name,
            "digest": digest,
            "architecture": platform.machine(),
            "userspace_bits": struct.calcsize("P") * 8,
            "tested_at": time.time(),
            "adapter": entry["adapter"],
        }
        try:
            async with self.lock:
                self.monitor.permit(size)
                adapter = (
                    OllamaDecision(self.client, self.settings.ollama_url)
                    if entry["adapter"] == "decision"
                    else OllamaChat(self.client, self.settings.ollama_url, tools=entry["adapter"] == "tools")
                )
                started = time.monotonic()
                inferred = await adapter.infer(
                    name,
                    {"text": "The checkout service is down for every customer."},
                    questions,
                    keep_alive=self.store.setting("keep_alive", "5m"),
                )
                result.update(
                    {
                        "status": "supported",
                        "latency_ms": (time.monotonic() - started) * 1000,
                        "answers": inferred["answers"],
                        "system": self.monitor.sample(),
                    }
                )
        except (DomainError, httpx.HTTPError) as error:
            result.update(
                {
                    "status": "unsupported",
                    "error_code": error.code if isinstance(error, DomainError) else "provider_unavailable",
                    "error": error.message
                    if isinstance(error, DomainError)
                    else "The provider is unavailable.",
                }
            )
        self.store.put("compatibility", result, canonical(name))
        self.store.event("compatibility", {"name": name, "status": result["status"]})
        return result

    async def close(self):
        if self.job_task and not self.job_task.done():
            self.job_task.cancel()
            await asyncio.gather(self.job_task, return_exceptions=True)
