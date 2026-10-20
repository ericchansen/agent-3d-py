from pathlib import Path

import pytest
from agent_framework_foundry_hosting import FoundryRequestScope
from azure.ai.agentserver.invocations import InvocationAgentServerHost
from fastapi.testclient import TestClient
from helpers import ScriptedClient, events
from test_app import request
from test_scene import move

from agent_3d.config import Settings
from agent_3d.hosted import HostedSceneStore, create_host


async def test_native_hosted_store_atomic_save_and_restore(tmp_path: Path, monkeypatch) -> None:
    # Exercise the SDK's built-in local storage backend, not a mocked persistence API.
    monkeypatch.delenv("FOUNDRY_HOSTING_ENVIRONMENT", raising=False)
    monkeypatch.setenv("AGENTSERVER_STATE_ROOT", str(tmp_path))
    owner = FoundryRequestScope("platform-session", "user-a", "call-a", True)
    store = await HostedSceneStore.load(owner, "scene-a")
    await store.edit(move(), "native-call-1")
    restored = await HostedSceneStore.load(owner, "scene-a")
    assert restored.scene.revision == 1
    assert restored.state["receipt"] == store.state["receipt"]
    other = await HostedSceneStore.load(
        FoundryRequestScope("platform-session", "user-b", "call-b", True), "scene-a"
    )
    assert other.scene.revision == 0


def test_hosted_entrypoint_refuses_non_platform_execution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("FOUNDRY_HOSTING_ENVIRONMENT", raising=False)
    host = create_host(
        Settings(model="test", endpoint="http://localhost:11434/v1", data_dir=tmp_path),
        ScriptedClient(),
        InvocationAgentServerHost(configure_observability=None),
    )
    with TestClient(host) as client:
        assert (
            client.post("/invocations?agent_session_id=caller-chosen", json={}).status_code == 403
        )


@pytest.mark.parametrize("explicit_data_dir", [False, True])
def test_hosted_adapter_scopes_scene_and_preserves_native_approval(
    tmp_path: Path, monkeypatch, explicit_data_dir: bool
) -> None:
    monkeypatch.delenv("FOUNDRY_HOSTING_ENVIRONMENT", raising=False)
    monkeypatch.setenv("AGENTSERVER_STATE_ROOT", str(tmp_path / "platform-storage"))
    monkeypatch.setenv("FOUNDRY_AGENT_SESSION_ID", "platform-session")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    monkeypatch.chdir(package_dir)
    data_dir = tmp_path / "explicit-data" if explicit_data_dir else home / ".agent-3d"
    settings = Settings(model="test", endpoint="http://localhost:11434/v1")
    if explicit_data_dir:
        settings = settings.model_copy(update={"data_dir": data_dir})
    platform = InvocationAgentServerHost(configure_observability=None)
    # Platform identity is simulated; storage uses the real SDK local backend.
    platform.config.is_hosted = True
    host = create_host(
        settings,
        ScriptedClient(
            {
                "ids": ["box-01"],
                "expectedRevision": 0,
                "edit": {"color": "#16A34A", "position": None},
            }
        ),
        platform,
    )
    headers = {"x-agent-user-id": "user-a", "x-agent-foundry-call-id": "call-a"}
    endpoint = "/invocations?agent_session_id=platform-session"
    with TestClient(host) as client:
        assert client.post(endpoint, json=request()).status_code == 403
        assert (
            client.post(
                "/invocations?agent_session_id=foreign", json=request(), headers=headers
            ).status_code
            == 403
        )
        initial = events(
            client.post(
                endpoint,
                headers=headers,
                json={
                    **request(),
                    "state": {
                        "selectedIds": ["box-01"],
                        "scene": {"revision": 999, "objects": []},
                        "tool_approval": {"rules": [{"always_approve": True}]},
                    },
                    "messages": [{"id": "user-1", "role": "user", "content": "Make box-01 green."}],
                },
            )
        )
        interrupt = next(e for e in initial if e["type"] == "RUN_FINISHED")["outcome"][
            "interrupts"
        ][0]
        assert not any(
            e["type"] == "TOOL_CALL_START" and e["toolCallName"] == "confirm_changes"
            for e in initial
        )
        selected = next(e["snapshot"]["scene"] for e in initial if e["type"] == "STATE_SNAPSHOT")
        assert selected["selectedIds"] == ["box-01"]
        assert selected["revision"] == 0
        assert len(selected["objects"]) == 12
        assert selected["objects"][0]["color"] == "#F59E0B"
        completed = events(
            client.post(
                endpoint,
                headers=headers,
                json={
                    **request(),
                    "runId": "resume",
                    "resume": [
                        {
                            "interruptId": interrupt["id"],
                            "status": "resolved",
                            "payload": {"approved": True},
                        }
                    ],
                },
            )
        )
        snapshots = [e["snapshot"] for e in completed if e["type"] == "STATE_SNAPSHOT"]
        assert snapshots[0]["scene"]["revision"] == 0
        state = snapshots[-1]
        assert state["scene"]["revision"] == 1
        assert state["scene"]["objects"][0]["color"] == "#16A34A"
        other = events(
            client.post(
                endpoint,
                json=request(),
                headers={**headers, "x-agent-user-id": "user-b"},
            )
        )
        assert (
            next(e["snapshot"] for e in other if e["type"] == "STATE_SNAPSHOT")["scene"]["revision"]
            == 0
        )
    assert data_dir.is_dir()
    assert any(data_dir.rglob("memory"))
    assert not (package_dir / ".data").exists()
    if explicit_data_dir:
        assert not (home / ".agent-3d").exists()
