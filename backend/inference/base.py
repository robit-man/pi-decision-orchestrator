from abc import ABC, abstractmethod
import math

from backend.errors import DomainError


class Adapter(ABC):
    @abstractmethod
    async def infer(self, model: str, state, questions: dict, *, images=None, keep_alive="5m") -> dict:
        """Return {answers, model, model_digest, inference_ms, raw_metadata}."""


def distribution(values: dict, allowed: set[str]) -> dict[str, float]:
    if set(values) != allowed:
        raise DomainError("invalid_model_output", "The model did not return every configured outcome.", 502)
    try:
        result = {str(key): float(value) for key, value in values.items()}
    except (ValueError, TypeError):
        raise DomainError("invalid_model_output", "The model returned invalid probabilities.", 502) from None
    if (
        any(not math.isfinite(value) or not 0 <= value <= 1 for value in result.values())
        or abs(sum(result.values()) - 1) > 0.01
    ):
        raise DomainError(
            "invalid_model_output", "The model returned an invalid probability distribution.", 502
        )
    return result


def answer(selected, probabilities=None, *, source="native", concentration=None, score=None, legend=None):
    ranked = sorted((probabilities or {}).values(), reverse=True)
    selected_probability = (probabilities or {}).get(
        str(selected).lower() if isinstance(selected, bool) else str(selected)
    )
    return {
        "selected": selected,
        "probabilities": probabilities,
        "selected_probability": selected_probability,
        "margin": ranked[0] - ranked[1] if len(ranked) > 1 else None,
        "source": source,
        "concentration": concentration,
        "score": score,
        "legend": legend,
    }
