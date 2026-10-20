import hashlib
import json
import logging
import os
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_framework import Content, FunctionInvocationContext, tool
from agent_framework.ag_ui import AGUIThreadSnapshot, state_update
from opentelemetry import metrics, trace

from agent_3d.models import Edit, EditRequest, Ids, OperationReceipt, SceneSnapshot, Shape

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)
outcomes = metrics.get_meter(__name__).create_counter("scene.edit.outcomes")
SEED = Path(__file__).parent / "contracts" / "fixtures" / "seed.scene.json"


class SceneStore:
    """One atomic scene document at the native AG-UI snapshot storage boundary."""

    def __init__(self, path: Path) -> None:
        self.path = path
        if path.exists():
            self.snapshot = AGUIThreadSnapshot(**json.loads(path.read_text(encoding="utf-8")))
            SceneSnapshot.model_validate(self.state["scene"])
            if self.snapshot.interrupt:
                self.snapshot = AGUIThreadSnapshot(
                    state=self.state,
                    session_state={
                        "scene_provider": (self.snapshot.session_state or {}).get("scene_provider")
                    },
                )
                self.state["error"] = self.error(
                    "SESSION_RESET", "Pending approval expired after restart. Please ask again."
                )
                self._write(self.snapshot)
        else:
            self.snapshot = AGUIThreadSnapshot(
                state={"scene": SceneSnapshot.model_validate_json(SEED.read_text()).model_dump()}
            )
            self._write(self.snapshot)

    @property
    def state(self) -> dict[str, Any]:
        if self.snapshot.state is None:
            raise RuntimeError("Stored scene state is missing.")
        return self.snapshot.state

    @property
    def scene(self) -> SceneSnapshot:
        return SceneSnapshot.model_validate(self.state["scene"])

    def _write(self, snapshot: AGUIThreadSnapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f".{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as output:
                json.dump(asdict(snapshot), output, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self.snapshot = snapshot

    async def persist(self, snapshot: AGUIThreadSnapshot) -> None:
        self._write(snapshot)

    async def get(self, *, scope: str, thread_id: str) -> AGUIThreadSnapshot:
        return self.snapshot

    async def save(self, *, scope: str, thread_id: str, snapshot: AGUIThreadSnapshot) -> None:
        # Native snapshots are emitted after tools: never overwrite the durable scene.
        private = dict(snapshot.session_state or {})
        private["scene_provider"] = (self.snapshot.session_state or {}).get("scene_provider")
        await self.persist(
            replace(snapshot, state={**(snapshot.state or {}), **self.state}, session_state=private)
        )

    async def bind_provider(self, fingerprint: str, name: str, model: str) -> None:
        private = dict(self.snapshot.session_state or {})
        previous = private.get("scene_provider")
        if previous is not None and previous != fingerprint:
            raise ValueError("Provider configuration changed; use a new thread or data directory.")
        private["scene_provider"] = fingerprint
        state = {**self.state, "provider": {"name": name, "model": model}}
        await self.persist(replace(self.snapshot, state=state, session_state=private))

    async def delete(self, *, scope: str, thread_id: str) -> bool:
        raise NotImplementedError("Create a new thread instead of deleting scene history.")

    async def clear(self, *, scope: str | None = None) -> None:
        raise NotImplementedError("Scene history cannot be cleared through the transport.")

    @staticmethod
    def error(code: str, message: str) -> dict[str, Any]:
        return {
            "code": code,
            "message": message,
            "retryable": code in {"BUSY", "SAVE_FAILED"},
            "correlationId": uuid4().hex,
        }

    def result(self, message: str) -> Content:
        span = trace.get_current_span().get_span_context()
        state = dict(self.state)
        if span.is_valid:
            state["traceId"] = format(span.trace_id, "032x")
        return state_update(message, state=state)

    async def select(self, ids: list[str]) -> None:
        scene = self.scene.model_dump()
        scene["selectedIds"] = ids
        validated = SceneSnapshot.model_validate(scene)
        await self.persist(
            replace(self.snapshot, state={**self.state, "scene": validated.model_dump()})
        )

    def tools(self) -> list[Any]:
        @tool
        def list_objects(shape: Shape | None = None) -> Content:
            """Read objects, optionally by shape; inspect returned names, colors and tags."""
            found = [
                obj.model_dump()
                for obj in self.scene.objects
                if (shape is None or obj.shape == shape)
            ]
            return Content.from_text(
                json.dumps(
                    {
                        "revision": self.scene.revision,
                        "objects": found,
                        "selectedIds": self.scene.selectedIds,
                        "highlightedIds": self.scene.highlightedIds,
                    }
                ),
            )

        @tool
        def get_objects(ids: Ids) -> Content:
            """Read existing objects by id, with the authoritative scene and revision."""
            lookup = {obj.id: obj for obj in self.scene.objects}
            if not set(ids) <= lookup.keys():
                raise ValueError("One or more object ids do not exist.")
            return Content.from_text(
                json.dumps(
                    {
                        "revision": self.scene.revision,
                        "objects": [lookup[id].model_dump() for id in ids],
                    }
                ),
            )

        @tool
        async def highlight_objects(ids: Ids) -> Content:
            """Set reversible view highlights; never change object material colors."""
            scene = self.scene.model_dump()
            scene["highlightedIds"] = ids
            updated = SceneSnapshot.model_validate(scene)
            await self.persist(
                replace(self.snapshot, state={**self.state, "scene": updated.model_dump()})
            )
            return self.result(json.dumps({"highlightedIds": ids}))

        @tool(approval_mode="always_require", schema=EditRequest)
        async def edit_objects(
            ids: list[str], expectedRevision: int, edit: Edit, context: FunctionInvocationContext
        ) -> Content:
            """Edit existing objects after approval; positions are absolute, not offsets."""
            request = EditRequest(ids=ids, expectedRevision=expectedRevision, edit=edit)
            call_id = context.metadata.get("function_call_occurrence_id") or context.metadata.get(
                "call_id"
            )
            if not isinstance(call_id, str) or not call_id:
                raise RuntimeError("Native invocation identity is required.")
            return await self.edit(request, call_id)

        return [list_objects, get_objects, highlight_objects, edit_objects]

    async def edit(self, request: EditRequest, call_id: str) -> Content:
        with tracer.start_as_current_span("scene.edit"):
            current = self.scene
            if request.expectedRevision != current.revision:
                outcomes.add(1, {"outcome": "stale"})
                return state_update(
                    "Scene revision conflict; read the scene before preparing a new request.",
                    state={
                        **self.state,
                        "error": self.error("STALE_REVISION", "Scene revision changed."),
                    },
                )
            if not set(request.ids) <= {obj.id for obj in current.objects}:
                return state_update(
                    "Unknown object id.",
                    state={**self.state, "error": self.error("NOT_FOUND", "Unknown object id.")},
                )
            proposed = current.model_dump()
            for obj in proposed["objects"]:
                if obj["id"] in request.ids:
                    obj.update(request.edit.model_dump(exclude_none=True))
            proposed["revision"] += 1
            updated = SceneSnapshot.model_validate(proposed)
            receipt = OperationReceipt(
                operationId="op-" + hashlib.sha256(call_id.encode()).hexdigest()[:32],
                affectedIds=request.ids,
                oldRevision=current.revision,
                newRevision=updated.revision,
                outcome="committed",
            )
            candidate = replace(
                self.snapshot,
                state={
                    **self.state,
                    "scene": updated.model_dump(),
                    "receipt": receipt.model_dump(),
                    "error": None,
                },
            )
            try:
                with tracer.start_as_current_span("scene.save"):
                    await self.persist(candidate)
            except OSError:
                logger.exception("Scene snapshot save failed; edit was not committed.")
                outcomes.add(1, {"outcome": "save_failed"})
                return state_update(
                    "Edit could not be saved.",
                    state={
                        **self.state,
                        "error": self.error("SAVE_FAILED", "Edit could not be saved."),
                    },
                )
            outcomes.add(1, {"outcome": "committed"})
            return self.result(json.dumps(receipt.model_dump()))
