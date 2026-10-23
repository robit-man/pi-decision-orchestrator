from typing import Any, Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import Field, model_validator

from backend.decisions.schema import StrictModel
from backend.decisions.validation import check_schema
from backend.security.destinations import parse_destination


class Connection(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    type: Literal[
        "http",
        "poll",
        "webhook",
        "mqtt",
        "manual",
        "json",
        "agent",
        "provider_ollama",
        "provider_openai",
        "provider_llamacpp",
    ] = "http"
    direction: Literal["input", "output", "both"] = "input"
    endpoint: str = Field(default="", max_length=2048)
    enabled: bool = False
    allowed_hosts: list[str] = Field(default_factory=list, max_length=20)
    allowed_cidrs: list[str] = Field(default_factory=list, max_length=20)
    allow_private: bool = False
    allow_output: bool = False
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"] = "GET"
    timeout_s: float = Field(default=10, ge=0.5, le=60)
    retries: int = Field(default=0, ge=0, le=3)
    idempotency_supported: bool = False
    poll_interval_s: float = Field(default=60, ge=5, le=86400)
    topic: str = Field(default="", max_length=256)
    qos: int = Field(default=1, ge=0, le=2)
    tls: bool = True
    auth_type: Literal["none", "bearer", "basic", "header"] = "none"
    auth_header: str = Field(default="X-API-Key", pattern=r"^[A-Za-z0-9-]{1,80}$")
    input_schema: dict = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)
    input_mapping: dict[str, str] = Field(default_factory=dict, max_length=50)
    decision_ids: list[str] = Field(default_factory=list, max_length=50)
    data: Any = Field(default_factory=dict)
    revision: int | None = None

    @model_validator(mode="after")
    def validate_connection(self):
        if any(
            any(
                part in key.lower()
                for part in (
                    "token",
                    "secret",
                    "password",
                    "api_key",
                    "apikey",
                    "credential",
                    "authorization",
                )
            )
            for key, _ in parse_qsl(urlsplit(self.endpoint).query)
        ):
            raise ValueError("Store credentials in authentication fields, not endpoint query parameters.")
        if self.type not in {"manual", "json", "webhook"}:
            parse_destination(self.endpoint, ("mqtt", "mqtts") if self.type == "mqtt" else ("http", "https"))
        if self.type == "mqtt" and not self.topic:
            raise ValueError("MQTT connections require a topic.")
        if self.type == "mqtt" and self.tls and not self.endpoint.startswith("mqtts://"):
            raise ValueError("Use mqtts:// for a TLS-enabled MQTT connection.")
        if (
            self.type == "mqtt"
            and self.direction in {"output", "both"}
            and ("#" in self.topic or "+" in self.topic)
        ):
            raise ValueError("MQTT publications require an exact topic without wildcards.")
        if self.method not in {"GET", "HEAD"} and self.retries and not self.idempotency_supported:
            raise ValueError("Retries for non-read requests require destination idempotency support.")
        check_schema(self.input_schema)
        check_schema(self.output_schema)
        return self
