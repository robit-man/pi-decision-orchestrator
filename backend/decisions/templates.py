from backend.decisions.validation import default_graph


def templates():
    nodes, edges = default_graph()
    action_selector = {
        "name": "Autonomous System Action Selector",
        "description": "Supervisory routing for a simulated system. Does not directly control safety-critical actuators.",
        "version": 1,
        "enabled": False,
        "model": "tev1:0.8b",
        "provider": "ollama",
        "adapter": "decision",
        "input_source": "manual",
        "input_schema": {
            "type": "object",
            "properties": {
                "objective": {"type": "string", "default": "reach_target"},
                "distance_m": {"type": "number", "minimum": 0, "default": 1.2},
                "obstacle_detected": {"type": "boolean", "default": True},
                "battery_percent": {"type": "number", "minimum": 0, "maximum": 100, "default": 78},
                "system_healthy": {"type": "boolean", "default": True},
            },
            "required": ["objective", "distance_m", "obstacle_detected", "battery_percent", "system_healthy"],
            "additionalProperties": False,
        },
        "questions": {
            "next_action": {
                "type": "choice",
                "instructions": "Select the safest appropriate next action.",
                "criteria": {
                    "continue": "Continue toward the target",
                    "reroute": "Find an alternative path",
                    "stop": "Stop movement",
                    "inspect": "Request additional observations",
                },
            }
        },
        "policy": {
            "min_probability": 0.7,
            "min_margin": 0.1,
            "require_probabilities": True,
            "fallback": "inspect",
        },
        "actions": [
            {
                "id": "notify",
                "name": "Notify dashboard",
                "type": "notify",
                "authorized": False,
                "payload": {
                    "next_action": "$.answers.next_action.selected",
                    "execution_id": "$.execution.id",
                },
            }
        ],
        "nodes": nodes,
        "edges": edges,
        "timeout_s": 60,
        "keep_alive": "5m",
    }
    ticket = {
        **action_selector,
        "name": "Support ticket router",
        "description": "Route a support request and separately estimate urgency.",
        "model": "laya",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "maxLength": 4000,
                    "default": "The checkout service is down for every customer.",
                }
            },
            "required": ["text"],
            "additionalProperties": False,
        },
        "questions": {
            "team": {
                "type": "choice",
                "instructions": "Which team should handle this request?",
                "criteria": {
                    "technical": "Software failures and outages",
                    "billing": "Payments, refunds, and invoices",
                    "sales": "Plans and purchases",
                },
            },
            "urgent": {"type": "noul", "instructions": "Does this request describe an urgent outage?"},
        },
        "actions": [],
        "policy": {
            "min_probability": 0.7,
            "min_margin": 0.1,
            "require_probabilities": True,
            "fallback": "manual_review",
        },
    }
    return [
        {
            "id": "action-selector",
            "name": action_selector["name"],
            "workflow": action_selector,
            "sample": {
                "objective": "reach_target",
                "distance_m": 1.2,
                "obstacle_detected": True,
                "battery_percent": 78,
                "system_healthy": True,
            },
        },
        {
            "id": "support-router",
            "name": ticket["name"],
            "workflow": ticket,
            "sample": {"text": "The checkout service is down for every customer."},
        },
    ]
