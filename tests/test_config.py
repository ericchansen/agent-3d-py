import pytest
from pydantic import ValidationError

from agent_3d.config import Settings


@pytest.mark.parametrize(
    "endpoint", ["https://example.com/v1", "file:///model", "http://user:password@localhost/v1"]
)
def test_local_profile_rejects_remote_or_credential_bearing_endpoints(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        Settings(model="local-model", endpoint=endpoint)


def test_local_profile_allows_explicit_loopback_services() -> None:
    settings = Settings(
        model="local-model",
        endpoint="http://127.0.0.1:11434/v1",
        otlp_endpoint="http://127.0.0.1:4318",
    )
    assert settings.provider == "local"


def test_local_profile_rejects_cloud_telemetry_and_invalid_token_budget() -> None:
    with pytest.raises(ValidationError):
        Settings(
            model="local-model",
            endpoint="http://localhost:11434/v1",
            otlp_endpoint="https://example.com",
        )
    with pytest.raises(ValidationError):
        Settings(model="local-model", endpoint="http://localhost:11434/v1", output_tokens=8192)
