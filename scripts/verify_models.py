#!/usr/bin/env python3
"""Explicit, serial hardware compatibility tests. Never downloads models without --pull."""

import argparse
import json
import platform
import statistics
import struct
import threading
import time
from pathlib import Path

import httpx
import psutil


QUESTIONS = {
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


def temperature():
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
    except (OSError, ValueError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", default=["laya"])
    parser.add_argument("--pull", action="store_true")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {
        "architecture": platform.machine(),
        "userspace_bits": struct.calcsize("P") * 8,
        "ram_bytes": psutil.virtual_memory().total,
        "tested_at": time.time(),
        "models": [],
    }
    with httpx.Client(base_url=args.endpoint, timeout=600, trust_env=False) as client:
        report["ollama_version"] = client.get("/api/version").json()["version"]
        for name in args.models:
            record = {"name": name, "status": "testing", "samples": []}
            report["models"].append(record)
            try:
                if args.pull:
                    print("Downloading", name, flush=True)
                    with client.stream(
                        "POST", "/api/pull", json={"model": name, "stream": True}, timeout=1800
                    ) as response:
                        response.raise_for_status()
                        previous = None
                        for line in response.iter_lines():
                            item = json.loads(line)
                            if item.get("error"):
                                raise RuntimeError(item["error"])
                            percent = (
                                int(item.get("completed", 0) * 100 / item["total"]) // 10 * 10
                                if item.get("total")
                                else None
                            )
                            state = (item.get("status"), percent)
                            if state != previous:
                                print(name, state, flush=True)
                                previous = state
                installed = client.get("/api/tags").json()["models"]
                record["digest"] = next(
                    item["digest"] for item in installed if item["name"] in {name, name + ":latest"}
                )
                client.post("/api/generate", json={"model": name, "keep_alive": 0})
                stopped, telemetry = threading.Event(), []

                def sample(stopped=stopped, telemetry=telemetry):
                    psutil.cpu_percent()
                    while not stopped.wait(0.1):
                        rss = 0
                        for process in psutil.process_iter(["name", "memory_info"]):
                            try:
                                if "ollama" in (process.info["name"] or "").lower():
                                    rss += process.info["memory_info"].rss
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass
                        telemetry.append(
                            {
                                "cpu_percent": psutil.cpu_percent(),
                                "runner_rss_bytes": rss,
                                "temperature_c": temperature(),
                            }
                        )

                sampler = threading.Thread(target=sample, daemon=True)
                sampler.start()
                try:
                    for trial in range(4):
                        start = time.monotonic()
                        if name.startswith("functiongemma"):
                            payload = {
                                "model": name,
                                "stream": False,
                                "messages": [
                                    {
                                        "role": "user",
                                        "content": "The checkout service is down for every customer. Record team technical, urgent true, severity 2.",
                                    }
                                ],
                                "tools": [
                                    {
                                        "type": "function",
                                        "function": {
                                            "name": "record_decision",
                                            "description": "Record the incident classification.",
                                            "parameters": {
                                                "type": "object",
                                                "properties": {
                                                    "team": {
                                                        "type": "string",
                                                        "enum": ["billing", "technical"],
                                                    },
                                                    "urgent": {"type": "boolean"},
                                                    "severity": {
                                                        "type": "integer",
                                                        "minimum": 0,
                                                        "maximum": 2,
                                                    },
                                                },
                                                "required": ["team", "urgent", "severity"],
                                            },
                                        },
                                    }
                                ],
                                "options": {"num_ctx": 2048, "num_predict": 160, "temperature": 0},
                                "keep_alive": "5m",
                            }
                            response = client.post("/api/chat", json=payload)
                        else:
                            response = client.post(
                                "/v1/systemone",
                                json={
                                    "model": name,
                                    "state": {"text": "The checkout service is down for every customer."},
                                    "questions": QUESTIONS,
                                    "keep_alive": "5m",
                                },
                            )
                        elapsed = (time.monotonic() - start) * 1000
                        result = response.json()
                        if response.status_code != 200:
                            raise RuntimeError(str(result.get("error", response.status_code))[:1000])
                        if name.startswith("functiongemma"):
                            calls = result.get("message", {}).get("tool_calls", [])
                            if len(calls) != 1 or calls[0]["function"]["name"] != "record_decision":
                                raise RuntimeError("Invalid or absent configured tool call")
                        elif set(result.get("answers", {})) != set(QUESTIONS):
                            raise RuntimeError("Typed answers missing")
                        record["samples"].append(
                            {"trial": trial, "cold": trial == 0, "latency_ms": elapsed, "response": result}
                        )
                finally:
                    stopped.set()
                    sampler.join(timeout=2)
                warm = [item["latency_ms"] for item in record["samples"][1:]]
                record.update(
                    {
                        "status": "supported",
                        "cold_start_ms": record["samples"][0]["latency_ms"],
                        "warm_mean_ms": statistics.mean(warm),
                        "warm_p95_ms": sorted(warm)[-1],
                        "observed_peak_runner_rss_bytes": max(
                            (item["runner_rss_bytes"] for item in telemetry), default=0
                        ),
                        "cpu_mean_percent": statistics.mean(item["cpu_percent"] for item in telemetry)
                        if telemetry
                        else None,
                        "peak_temperature_c": max(
                            (
                                item["temperature_c"]
                                for item in telemetry
                                if item["temperature_c"] is not None
                            ),
                            default=None,
                        ),
                        "sampling_interval_s": 0.1,
                        "quality_notice": "Compatibility, not a decision-accuracy evaluation.",
                    }
                )
            except Exception as error:
                record.update({"status": "unsupported", "error": str(error)[:1000]})
            finally:
                client.post("/api/generate", json={"model": name, "keep_alive": 0})
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, indent=2) + "\n")
                print(name, record["status"], record.get("warm_mean_ms", record.get("error")), flush=True)


if __name__ == "__main__":
    main()
