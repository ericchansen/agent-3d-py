import ipaddress
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


def is_loopback_url(value: str) -> bool:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    if parsed.hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["local", "openai", "foundry"] = "local"
    model: str = Field(min_length=1)
    endpoint: str
    data_dir: Path = Path(".data")
    context_tokens: int = Field(default=8192, gt=0)
    output_tokens: int = Field(default=1024, gt=0)
    otlp_endpoint: str | None = None

    @model_validator(mode="after")
    def validate_profile(self) -> "Settings":
        if self.output_tokens >= self.context_tokens:
            raise ValueError("Output token limit must be smaller than context token limit.")
        if self.provider == "local":
            if not is_loopback_url(self.endpoint):
                raise ValueError("The local profile requires a loopback model endpoint.")
            if self.otlp_endpoint and not is_loopback_url(self.otlp_endpoint):
                raise ValueError("The local profile requires a loopback telemetry endpoint.")
        elif urlsplit(self.endpoint).scheme != "https":
            raise ValueError("Connected model endpoints require HTTPS.")
        return self

    @classmethod
    def from_env(cls) -> "Settings":
        names = {
            "provider": "AGENT_PROVIDER",
            "model": "AGENT_MODEL",
            "endpoint": "AGENT_ENDPOINT",
            "data_dir": "AGENT_DATA_DIR",
            "context_tokens": "AGENT_CONTEXT_TOKENS",
            "output_tokens": "AGENT_OUTPUT_TOKENS",
            "otlp_endpoint": "OTEL_EXPORTER_OTLP_ENDPOINT",
        }
        return cls.model_validate(
            {field: os.environ[name] for field, name in names.items() if name in os.environ}
        )
