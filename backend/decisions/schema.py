from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Question(StrictModel):
    type: Literal["choice", "noul", "score"] = "choice"
    instructions: str = Field(min_length=1, max_length=4000)
    criteria: dict[str, str | None] | list[str] | None = None

    @model_validator(mode="after")
    def validate_criteria(self):
        if self.type == "choice" and (
            not isinstance(self.criteria, dict) or not 2 <= len(self.criteria) <= 26
        ):
            raise ValueError("Choice questions need 2–26 outcomes.")
        if self.type == "score" and (
            not isinstance(self.criteria, list) or not 2 <= len(self.criteria) <= 10
        ):
            raise ValueError("Score questions need 2–10 ordered levels.")
        if (
            self.type == "noul"
            and self.criteria is not None
            and (not isinstance(self.criteria, dict) or set(self.criteria) != {"true", "false"})
        ):
            raise ValueError("Boolean descriptions must define true and false.")
        return self


class Condition(StrictModel):
    path: str = Field(min_length=1, max_length=128)
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "exists", "in"] = "eq"
    value: Any = None


class Transform(StrictModel):
    op: Literal["set", "copy", "rename", "remove", "number", "string", "boolean", "pick"]
    path: str = Field(min_length=1, max_length=128)
    source: str = Field(default="", max_length=128)
    value: Any = None


class Policy(StrictModel):
    min_probability: float = Field(default=0.7, ge=0, le=1)
    min_margin: float = Field(default=0.1, ge=0, le=1)
    require_probabilities: bool = True
    require_agreement: bool = False
    conditions: list[Condition] = Field(default_factory=list, max_length=20)
    fallback: str = Field(default="abstain", max_length=100)
    calibration_dataset_id: str = ""
    min_calibration_accuracy: float = Field(default=0, ge=0, le=1)


class Action(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(default="Action", max_length=100)
    type: Literal["http", "webhook", "mqtt", "event", "notify", "decision", "response"] = "event"
    connection_id: str = ""
    decision_id: str = ""
    authorized: bool = False
    question: str = ""
    outcome: str = ""
    payload: Any = Field(default_factory=dict)
    input_schema: dict = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict = Field(default_factory=dict)
    timeout_s: float = Field(default=10, ge=0.5, le=60)
    retries: int = Field(default=0, ge=0, le=3)
    failure_policy: Literal["stop", "continue"] = "stop"


class Node(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    type: Literal[
        "input",
        "transform",
        "condition",
        "inference",
        "evaluate",
        "gate",
        "branch",
        "action",
        "delay",
        "log",
        "error",
    ]
    name: str = Field(default="Node", max_length=100)
    x: float = 0
    y: float = 0
    config: dict = Field(default_factory=dict)


class Edge(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    source: str
    target: str
    condition: str = Field(default="", max_length=100)


class Workflow(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    version: int = Field(default=1, ge=1)
    enabled: bool = False
    published_version: int | None = None
    input_source: Literal["manual", "connection", "api", "interval"] = "manual"
    input_connection_id: str = ""
    input_schema: dict = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict = Field(default_factory=dict)
    transforms: list[Transform] = Field(default_factory=list, max_length=30)
    conditions: list[Condition] = Field(default_factory=list, max_length=30)
    model: str = Field(default="laya", max_length=128)
    provider: str = Field(default="ollama", max_length=80)
    adapter: Literal["decision", "chat", "tools"] = "decision"
    questions: dict[str, Question] = Field(min_length=1, max_length=64)
    policy: Policy = Field(default_factory=Policy)
    actions: list[Action] = Field(default_factory=list, max_length=20)
    nodes: list[Node] = Field(default_factory=list, max_length=64)
    edges: list[Edge] = Field(default_factory=list, max_length=128)
    timeout_s: float = Field(default=60, ge=1, le=300)
    keep_alive: str = Field(default="5m", pattern=r"^(0|[1-9][0-9]{0,3}[smh])$")
    interval_s: float = Field(default=60, ge=5, le=86400)
    logging: bool = True
    revision: int | None = None


class Invocation(StrictModel):
    state: Any = Field(default_factory=dict)
    images: list[str] = Field(default_factory=list, max_length=4)
    dry_run: bool = True
    wait: bool = True
    model: str | None = Field(default=None, max_length=128)
    version: int | None = Field(default=None, ge=1)
    idempotency_key: str | None = Field(default=None, max_length=128)
