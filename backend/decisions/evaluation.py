import asyncio
import math
import statistics
import time
import hashlib
import json

from backend.inference.catalog import catalog_entry

from backend.decisions.schema import Invocation
from backend.errors import DomainError


def label(value):
    return str(value).lower() if isinstance(value, bool) else str(value)


def quality_metrics(samples):
    matrix, positives, negatives = {}, {}, {}
    correct, complete, abstained, errors = 0, 0, 0, 0
    probability_samples, brier = [], []
    for sample in samples:
        expected, answers = sample["expected"], sample.get("answers", {})
        errors += sample["status"] in {"failed", "timed_out", "cancelled"}
        abstained += sample["status"] != "completed"
        matched = []
        for question, truth in expected.items():
            item = answers.get(question, {})
            actual, target = label(item.get("selected", "[missing]")), label(truth)
            bucket = matrix.setdefault(question, {}).setdefault(target, {})
            bucket[actual] = bucket.get(actual, 0) + 1
            hit = actual == target
            matched.append(hit)
            if target in {"true", "false"}:
                positives[question] = positives.get(question, 0) + int(actual == "true" and target == "false")
                negatives[question] = negatives.get(question, 0) + int(actual == "false" and target == "true")
            probabilities = item.get("probabilities")
            if probabilities and target in probabilities:
                brier.append(
                    sum((value - int(option == target)) ** 2 for option, value in probabilities.items())
                )
                probability_samples.append((item["selected_probability"], int(hit)))
        if matched:
            complete += 1
            correct += all(matched)
    bins = []
    for index in range(10):
        rows = [
            (probability, hit)
            for probability, hit in probability_samples
            if index / 10 <= probability <= 1 and (probability < (index + 1) / 10 or index == 9)
        ]
        if rows:
            bins.append(
                {
                    "lower": index / 10,
                    "upper": (index + 1) / 10,
                    "count": len(rows),
                    "mean_probability": statistics.mean(row[0] for row in rows),
                    "observed_accuracy": statistics.mean(row[1] for row in rows),
                }
            )
    count = len(samples)
    return {
        "accuracy": correct / complete if complete else None,
        "labeled_samples": complete,
        "confusion_matrix": matrix,
        "false_positives": positives,
        "false_negatives": negatives,
        "abstention_rate": abstained / count if count else None,
        "error_rate": errors / count if count else None,
        "brier_score": statistics.mean(brier) if brier else None,
        "calibration": bins,
        "expected_calibration_error": sum(
            row["count"] * abs(row["mean_probability"] - row["observed_accuracy"]) for row in bins
        )
        / len(probability_samples)
        if probability_samples
        else None,
        "notice": "Accuracy and calibration describe this dataset and version, not general reliability.",
    }


def configuration_digest(config):
    from backend.decisions.validation import validate_workflow

    workflow, _ = validate_workflow(config)
    data = workflow.model_dump(exclude={"revision", "version", "published_version", "enabled"})
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class EvaluationManager:
    def __init__(self, store, engine, models, monitor):
        self.store, self.engine, self.models, self.monitor = store, engine, models, monitor
        self.task = None

    async def start(self, data):
        if self.task and not self.task.done():
            raise DomainError("benchmark_busy", "A benchmark is already running.", 409)
        if (
            self.engine.running_id
            or not self.engine.queue.empty()
            or (self.models.job_task and not self.models.job_task.done())
        ):
            raise DomainError(
                "runtime_busy", "Wait for executions and downloads to finish before benchmarking.", 409
            )
        workflow_id, dataset_id = str(data.get("workflow_id", "")), str(data.get("dataset_id", ""))
        workflow = self.store.get("decision", workflow_id)
        models = data.get("models") or [data.get("model") or workflow.get("model", "laya")]
        trials = int(data.get("trials", 1))
        if not 1 <= len(models) <= 4 or not 1 <= trials <= 5:
            raise DomainError("benchmark_limit", "Choose 1–4 models and 1–5 trials.")
        dataset = self.store.get("dataset", dataset_id)
        cases = self.store.find("case", dataset_id=dataset_id)
        if not cases or len(cases) * len(models) * trials > 500:
            raise DomainError(
                "benchmark_cases", "A benchmark needs saved cases and is limited to 500 total inferences."
            )
        for model in models:
            await self.models.digest(model)
        job = self.store.put(
            "benchmark",
            {
                "name": workflow["name"],
                "workflow_id": workflow_id,
                "workflow_version": workflow.get("version", 1),
                "dataset_id": dataset_id,
                "dataset_revision": dataset["revision"],
                "snapshot": workflow,
                "models": models,
                "trials": trials,
                "status": "queued",
                "progress": 0,
                "results": [],
            },
        )
        self.engine.benchmarking = True
        self.task = asyncio.create_task(self._run(job, cases), name="benchmark")
        return job

    async def _run(self, job, cases):
        results, completed = [], 0
        total = len(cases) * len(job["models"]) * job["trials"]
        try:
            for model in job["models"]:
                await self.models.unload(model)
                observations, stop = [], asyncio.Event()

                async def sample(stop=stop, observations=observations):
                    while not stop.is_set():
                        status = self.monitor.sample()
                        from backend.monitoring.processes import ollama_rss

                        rss = ollama_rss()
                        observations.append(
                            {"cpu": status["cpu_percent"], "rss": rss, "temperature": status["temperature_c"]}
                        )
                        if len(observations) > 10000:
                            observations[:] = observations[::2]
                        await asyncio.sleep(0.1)

                sampler = asyncio.create_task(sample())
                samples = []
                try:
                    for trial in range(job["trials"]):
                        for case in cases:
                            execution = await self.engine.submit(
                                job["workflow_id"],
                                Invocation(
                                    state=case["input"],
                                    images=case.get("images", []),
                                    model=model,
                                    wait=True,
                                    dry_run=True,
                                ),
                                actor="benchmark",
                                draft=True,
                                snapshot=job["snapshot"],
                            )
                            samples.append(
                                {
                                    "trial": trial,
                                    "case_id": case["id"],
                                    "execution_id": execution["id"],
                                    "expected": case.get("expected", {}),
                                    "answers": (execution.get("result") or {}).get("answers", {}),
                                    "status": execution["status"],
                                    "latency_ms": execution.get("latency_ms"),
                                    "inference_ms": execution.get("inference_ms"),
                                    "model_digest": execution.get("model_digest"),
                                    "cold": not samples,
                                }
                            )
                            completed += 1
                            self.store.put(
                                "benchmark",
                                {
                                    **job,
                                    "status": "running",
                                    "progress": round(completed * 100 / total),
                                    "results": results,
                                    "current_model": model,
                                },
                                job["id"],
                            )
                            self.store.event(
                                "benchmark", {"id": job["id"], "progress": round(completed * 100 / total)}
                            )
                finally:
                    stop.set()
                    await sampler
                warm = [item["latency_ms"] for item in samples[1:] if item["latency_ms"] is not None]
                latencies = [item["latency_ms"] for item in samples if item["latency_ms"] is not None]
                result = {
                    "name": model,
                    "model": model,
                    "workflow_id": job["workflow_id"],
                    "workflow_version": job["workflow_version"],
                    "dataset_id": job["dataset_id"],
                    "dataset_revision": job["dataset_revision"],
                    "configuration_digest": configuration_digest(
                        {**job["snapshot"], "model": model, "adapter": catalog_entry(model)["adapter"]}
                    ),
                    "model_digest": samples[0].get("model_digest") if samples else None,
                    "samples": samples,
                    **quality_metrics(samples),
                    "cold_start_ms": samples[0]["latency_ms"] if samples else None,
                    "warm_mean_ms": statistics.mean(warm) if warm else None,
                    "average_ms": statistics.mean(latencies) if latencies else None,
                    "p95_ms": sorted(latencies)[max(0, math.ceil(len(latencies) * 0.95) - 1)]
                    if latencies
                    else None,
                    "observed_peak_runner_rss_bytes": max((item["rss"] for item in observations), default=0),
                    "cpu_mean_percent": statistics.mean(item["cpu"] for item in observations)
                    if observations
                    else None,
                    "peak_temperature_c": max(
                        (item["temperature"] for item in observations if item["temperature"] is not None),
                        default=None,
                    ),
                    "sampling_interval_s": 0.1,
                    "tested_at": time.time(),
                }
                self.store.put("evaluation", result)
                results.append(result)
            self.store.put(
                "benchmark", {**job, "status": "completed", "progress": 100, "results": results}, job["id"]
            )
        except asyncio.CancelledError:
            self.store.put("benchmark", {**job, "status": "cancelled", "results": results}, job["id"])
            if self.engine.running_id:
                self.engine.cancel(self.engine.running_id)
            raise
        except Exception:
            self.store.put(
                "benchmark",
                {
                    **job,
                    "status": "failed",
                    "error": "Benchmark interrupted. Inspect execution history and model status.",
                    "results": results,
                },
                job["id"],
            )
        finally:
            self.engine.benchmarking = False
            self.store.event("benchmark", {"id": job["id"], "finished": True})

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
