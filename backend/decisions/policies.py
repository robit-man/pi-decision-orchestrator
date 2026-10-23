from backend.decisions.schema import Policy
from backend.decisions.validation import condition_matches


def evaluate_policy(
    policy: Policy, answers: dict, context: dict, agreement: list[dict] | None = None, calibration=None
) -> dict:
    reasons = []
    for name, item in answers.items():
        probability = item.get("selected_probability")
        margin = item.get("margin")
        if probability is None:
            if policy.require_probabilities:
                reasons.append(f"{name}: this adapter does not provide native probabilities")
        elif probability < policy.min_probability:
            reasons.append(f"{name}: selected outcome probability is below the policy threshold")
        if margin is not None and margin < policy.min_margin:
            reasons.append(f"{name}: the leading outcomes are too close")
    if not answers:
        reasons.append("No valid model answers")
    if not all(condition_matches(condition, context) for condition in policy.conditions):
        reasons.append("A deterministic acceptance condition did not match")
    if policy.require_agreement:
        if not agreement or len(agreement) < 2:
            reasons.append("Model agreement requires at least two inference nodes")
        elif any(
            {name: item["selected"] for name, item in result.items()}
            != {name: item["selected"] for name, item in answers.items()}
            for result in agreement
        ):
            reasons.append("Models disagree")
    if policy.min_calibration_accuracy:
        if (
            not calibration
            or calibration.get("accuracy") is None
            or calibration["accuracy"] < policy.min_calibration_accuracy
        ):
            reasons.append("Measured evaluation accuracy does not meet policy")
    return {
        "accepted": not reasons,
        "reasons": reasons,
        "fallback": policy.fallback if reasons else None,
        "probability_notice": "Model probabilities are not a calibrated guarantee of correctness.",
    }
