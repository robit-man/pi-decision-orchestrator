from dataclasses import dataclass, field
from pathlib import Path
import os


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("PI_DATA_DIR", "/var/lib/pi-decision")))
    config_dir: Path = field(default_factory=lambda: Path(os.getenv("PI_CONFIG_DIR", "/etc/pi-decision")))
    ollama_url: str = field(default_factory=lambda: os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"))
    host: str = field(default_factory=lambda: os.getenv("PI_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("PI_PORT", "8765")))
    secure_cookie: bool = field(
        default_factory=lambda: os.getenv("PI_SECURE_COOKIE", "false").lower() == "true"
    )
    allowed_origins: list[str] = field(
        default_factory=lambda: [x for x in os.getenv("PI_ALLOWED_ORIGINS", "").split(",") if x]
    )
    queue_size: int = 16
    max_body: int = 4_194_304
    session_hours: int = 12
    history_limit: int = 10_000
    min_available_mb: int = 400
    max_temperature: float = 80.0
    telemetry_seconds: float = 3.0
    testing: bool = False

    @property
    def database(self) -> Path:
        return self.data_dir / "pi-decision.db"
