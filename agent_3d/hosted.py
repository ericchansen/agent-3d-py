"""Foundry-only entrypoint; importing the local application never imports this module."""

import hashlib
import logging
from dataclasses import asdict
from pathlib import Path

from agent_framework import SupportsChatGetResponse
from agent_framework_ag_ui import AGUIRequest, AGUIThreadSnapshot
from agent_framework_foundry_hosting import FoundryRequestScope
from azure.ai.agentserver.core import get_request_context
from azure.ai.agentserver.core.storage import FoundryStateStore
from azure.ai.agentserver.invocations import InvocationAgentServerHost
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from agent_3d.app import SceneGateway, create_model_client
from agent_3d.config import Settings
from agent_3d.models import SceneSnapshot
from agent_3d.scene import SEED, SceneStore

logger = logging.getLogger(__name__)


class HostedSceneStore(SceneStore):
    def __init__(
        self, owner: FoundryRequestScope, key: str, snapshot: AGUIThreadSnapshot, etag: str | None
    ) -> None:
        self.owner = owner
        self.key = key
        self.snapshot = snapshot
        self.etag = etag

    @classmethod
    async def load(cls, scope: FoundryRequestScope, thread_id: str) -> "HostedSceneStore":
        storage = await FoundryStateStore.get_or_create(
            f"scene/v1/{scope.storage_key}", user_isolation=True, user_id=scope.user_id
        )
        key = hashlib.sha256(thread_id.encode()).hexdigest()
        async with storage:
            item = await storage.get_item(key)
        snapshot = (
            AGUIThreadSnapshot(**item.value)
            if item
            else AGUIThreadSnapshot(
                state={"scene": SceneSnapshot.model_validate_json(SEED.read_text()).model_dump()}
            )
        )
        store = cls(scope, key, snapshot, item.etag if item else None)
        SceneSnapshot.model_validate(store.state["scene"])
        if snapshot.interrupt:
            store.snapshot = AGUIThreadSnapshot(
                state={
                    **store.state,
                    "error": store.error(
                        "SESSION_RESET", "Pending approval expired after restart. Please ask again."
                    ),
                },
                session_state={
                    "scene_provider": (snapshot.session_state or {}).get("scene_provider")
                },
            )
        if item is None or snapshot.interrupt:
            await store.persist(store.snapshot)
        return store

    async def persist(self, snapshot: AGUIThreadSnapshot) -> None:
        storage = await FoundryStateStore.get_or_create(
            f"scene/v1/{self.owner.storage_key}",
            user_isolation=True,
            user_id=self.owner.user_id,
        )
        async with storage:
            reference = (
                await storage.create_item(self.key, asdict(snapshot))
                if self.etag is None
                else await storage.set_item(self.key, asdict(snapshot), if_match=self.etag)
            )
        self.etag = reference.etag
        self.snapshot = snapshot


def create_host(
    settings: Settings,
    client: SupportsChatGetResponse,
    host: InvocationAgentServerHost | None = None,
) -> InvocationAgentServerHost:
    if "data_dir" not in settings.model_fields_set:
        settings = settings.model_copy(update={"data_dir": Path.home() / ".agent-3d"})
    host = host if host is not None else InvocationAgentServerHost()
    gateways: dict[str, SceneGateway] = {}

    @host.invoke_handler
    async def invoke(request: Request) -> Response:
        try:
            context = get_request_context()
            owner = FoundryRequestScope.from_context(host.config, context)
            if not owner.is_hosted:
                return JSONResponse(
                    {"detail": "This entrypoint requires the Foundry authenticated platform."},
                    status_code=403,
                )
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 262144:
                    raise ValueError("Request exceeds 256 KiB.")
            data = AGUIRequest.model_validate_json(body)
            if owner.storage_key not in gateways:
                if len(gateways) >= 64:
                    return JSONResponse({"detail": "Session capacity reached."}, status_code=503)

                async def load(thread_id: str) -> SceneStore:
                    return await HostedSceneStore.load(owner, thread_id)

                gateways[owner.storage_key] = SceneGateway(
                    settings.model_copy(update={"data_dir": settings.data_dir / owner.storage_key}),
                    client,
                    owner_scope=owner.storage_key,
                    store_factory=load,
                )
            # Reuse the registered native FastAPI endpoint and its unmodified SSE response.
            return await gateways[owner.storage_key].respond(data)
        except (ValueError, RuntimeError):
            logger.exception("Rejected hosted identity or request.")
            return JSONResponse({"detail": "Invalid hosted request."}, status_code=403)

    return host


def main() -> None:
    settings = Settings.from_env()
    if settings.provider != "foundry":
        raise ValueError("The hosted entrypoint requires AGENT_PROVIDER=foundry.")
    logging.getLogger("agent_framework_ag_ui").setLevel(logging.WARNING)
    client, close = create_model_client(settings, hosted=True)
    host = create_host(settings, client)

    async def cleanup() -> None:
        for callback in close:
            await callback()

    host.shutdown_handler(cleanup)
    host.run()


if __name__ == "__main__":
    main()
