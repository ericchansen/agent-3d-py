import json
from pathlib import Path
from unittest.mock import patch

import jsonschema
import pytest
from pydantic import ValidationError

from agent_3d.models import Edit, EditRequest, SceneSnapshot
from agent_3d.scene import SceneStore

CONTRACTS = Path(__file__).parent.parent / "agent_3d" / "contracts"


def move() -> EditRequest:
    return EditRequest.model_validate_json(
        (CONTRACTS / "fixtures" / "move.request.json").read_text()
    )


async def test_shared_move_atomic_receipt_replay_and_restore(tmp_path: Path) -> None:
    store = SceneStore(tmp_path / "scene.json")
    await store.edit(move(), "native-call-1")
    assert store.scene.revision == 1
    box = next(obj for obj in store.scene.objects if obj.id == "box-01")
    assert box.position.model_dump() == {"x": -4, "y": 3, "z": -3}
    jsonschema.validate(
        store.state, json.loads((CONTRACTS / "application.schema.json").read_text())
    )
    before = store.path.read_bytes()
    await store.edit(move(), "native-call-1")
    assert store.path.read_bytes() == before
    restored = SceneStore(store.path)
    assert restored.state == store.state


async def test_failed_save_does_not_publish_a_commit(tmp_path: Path) -> None:
    store = SceneStore(tmp_path / "scene.json")
    original = store.path.read_bytes()
    with patch("agent_3d.scene.os.replace", side_effect=OSError("Disk unavailable")):
        result = await store.edit(move(), "native-call-1")
    assert store.scene.revision == 0
    assert store.path.read_bytes() == original
    assert result.text and "could not be saved" in result.text


@pytest.mark.parametrize(
    "edit",
    [
        {},
        {"color": None},
        {"color": None, "position": None},
        {"color": "green"},
        {"position": {"x": float("nan"), "y": 0, "z": 0}},
        {"position": {"x": 1001, "y": 0, "z": 0}},
        {"position": {"x": True, "y": 0, "z": 0}},
        {"position": {"x": "5", "y": 0, "z": 0}},
        {"dimensions": {"x": 1, "y": 1, "z": 1}},
    ],
)
def test_invalid_edits_are_rejected(edit: dict) -> None:
    with pytest.raises(ValidationError):
        Edit.model_validate(edit)


async def test_invalid_geometry_and_foreign_selection_are_rejected(tmp_path: Path) -> None:
    store = SceneStore(tmp_path / "scene.json")
    scene = store.scene.model_dump()
    scene["objects"][4]["dimensions"]["x"] = 5
    with pytest.raises(ValidationError):
        SceneSnapshot.model_validate(scene)
    with pytest.raises(ValidationError):
        await store.select(["foreign-object"])


async def test_nullable_edit_preserves_unchanged_property_and_binds_provider(
    tmp_path: Path,
) -> None:
    store = SceneStore(tmp_path / "scene.json")
    await store.bind_provider("provider-a", "local", "test")
    before = store.scene.objects[0].position
    await store.edit(
        EditRequest(ids=["box-01"], expectedRevision=0, edit=Edit(color="#16A34A", position=None)),
        "native-call",
    )
    assert store.scene.objects[0].position == before
    assert store.scene.objects[0].color == "#16A34A"
    restored = SceneStore(store.path)
    with pytest.raises(ValueError, match="Provider configuration changed"):
        await restored.bind_provider("provider-b", "local", "test")
