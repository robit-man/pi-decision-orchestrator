import math
import time

import httpx

from backend.errors import DomainError
from backend.inference.base import Adapter, answer, distribution


class OllamaDecision(Adapter):
    def __init__(self, client: httpx.AsyncClient, url: str, transport=None):
        self.client, self.url, self.transport = client, url.rstrip("/"), transport

    async def infer(self, model, state, questions, *, images=None, keep_alive="5m"):
        payload = {"model": model, "state": state, "questions": questions, "keep_alive": keep_alive}
        if images:
            payload["images"] = images
        start = time.monotonic()
        try:
            response = (
                await self.transport("POST", "/v1/systemone", payload)
                if self.transport
                else await self.client.post(self.url + "/v1/systemone", json=payload, timeout=300)
            )
        except (httpx.HTTPError, OSError):
            raise DomainError(
                "provider_unavailable", "The decision provider is unavailable or timed out.", 503
            ) from None
        if response.status_code != 200:
            code = "model_unsupported" if response.status_code in {400, 404, 422} else "inference_failed"
            raise DomainError(
                code,
                "Native decision inference failed. Check the model compatibility record and provider status.",
                502,
            )
        try:
            raw = response.json()
            returned = raw["answers"]
            if set(returned) != set(questions):
                raise ValueError("question mismatch")
            answers = {}
            for name, question in questions.items():
                item = returned[name]
                if question["type"] == "noul":
                    probability = float(item["noul"])
                    if not math.isfinite(probability) or not 0 <= probability <= 1:
                        raise ValueError("invalid Boolean probability")
                    answers[name] = answer(
                        probability >= 0.5, {"true": probability, "false": 1 - probability}
                    )
                elif question["type"] == "score":
                    criteria = question["criteria"]
                    probabilities = distribution(item["probabilities"], set(map(str, range(len(criteria)))))
                    score = float(item["score"])
                    if not math.isfinite(score) or not 0 <= score <= len(criteria) - 1:
                        raise ValueError("invalid score")
                    selected = max(probabilities, key=probabilities.get)
                    answers[name] = answer(
                        selected,
                        probabilities,
                        concentration=item.get("confidence"),
                        score=score,
                        legend=criteria,
                    )
                else:
                    probabilities = distribution(item["probabilities"], set(question["criteria"]))
                    selected = item["choice"]
                    if selected not in probabilities:
                        raise ValueError("unknown choice")
                    answers[name] = answer(selected, probabilities, concentration=item.get("confidence"))
        except (KeyError, ValueError, TypeError):
            raise DomainError(
                "invalid_model_output", "The decision provider returned an invalid typed answer.", 502
            ) from None
        return {
            "answers": answers,
            "model": model,
            "model_digest": raw.get("model_digest", ""),
            "inference_ms": (time.monotonic() - start) * 1000,
            "raw_metadata": {key: raw[key] for key in ("total_duration", "load_duration") if key in raw},
        }
