import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from agent_framework import (
    FileSystemAgentFileStore,
    FunctionInvocationConfiguration,
    SupportsChatGetResponse,
    create_harness_agent,
)
from agent_framework.observability import configure_otel_providers
from agent_framework_ag_ui import (
    AgentFrameworkAgent,
    AGUIRequest,
    add_agent_framework_fastapi_endpoint,
)
from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import TypeAdapter, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.types import Receive, Scope, Send

from agent_3d.config import Settings, is_loopback_url
from agent_3d.models import Id
from agent_3d.scene import SceneStore

logger = logging.getLogger(__name__)
SKILLS = Path(__file__).parent / "skills"
NativeEndpoint = Callable[[AGUIRequest], Awaitable[Response]]


class SceneGateway:
    """Choose an owned scene, then delegate the entire stream to the native endpoint."""

    def __init__(
        self,
        settings: Settings,
        client: SupportsChatGetResponse,
        *,
        owner_scope: str = "loopback-demo",
        store_factory: Callable[[str], Awaitable[SceneStore]] | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.owner_scope = owner_scope
        self.store_factory = store_factory
        self.creation_lock = asyncio.Lock()
        self.sessions: dict[str, tuple[NativeEndpoint, SceneStore, asyncio.Lock]] = {}

    async def session(self, thread_id: str) -> tuple[NativeEndpoint, SceneStore, asyncio.Lock]:
        if thread_id not in self.sessions:
            if len(self.sessions) >= 64:
                raise ValueError("This demo supports at most 64 loaded scenes per process.")
            folder = self.settings.data_dir / hashlib.sha256(thread_id.encode()).hexdigest()
            store = (
                await self.store_factory(thread_id)
                if self.store_factory
                else SceneStore(folder / "snapshot.json")
            )
            fingerprint = hashlib.sha256(
                json.dumps(
                    [self.settings.provider, self.settings.model, self.settings.endpoint]
                ).encode()
            ).hexdigest()
            await store.bind_provider(fingerprint, self.settings.provider, self.settings.model)
            agent = create_harness_agent(
                self.client,
                name="scene-agent",
                agent_instructions=(
                    "Inspect and edit the synthetic scene using the bundled skills and tools. "
                    "Call list_objects or get_objects before proposing an edit. "
                    "Wait for read results before highlighting or editing; never guess object ids. "
                    "For a color name such as blue, read objects first; "
                    "use their actual names and hex colors, not a guessed CSS color. "
                    "State returned by scene tools is authoritative. Never add or delete objects. "
                    "edit_objects uses absolute positions and the current expectedRevision. "
                    "Call edit_objects to produce the native approval request; "
                    "do not substitute a text-only request for approval. "
                    "Native approval is required; never claim a proposal was committed."
                ),
                tools=store.tools(),
                skills_paths=SKILLS,
                disable_web_search=True,
                # shortcut: restore auto-allow when AG-UI clears settled approval budgets.
                disable_tool_auto_approval=True,
                file_memory_store=FileSystemAgentFileStore(folder / "memory"),
                max_context_window_tokens=self.settings.context_tokens,
                max_output_tokens=self.settings.output_tokens,
                default_options={"parallel_tool_calls": False},
            )
            inner = FastAPI()
            runner = AgentFrameworkAgent(
                agent,
                state_schema={"scene": {"type": "object"}},
                snapshot_store=store,
                require_confirmation=False,
            )
            add_agent_framework_fastapi_endpoint(
                inner,
                runner,
                "/",
                snapshot_store=store,
                snapshot_scope_resolver=lambda _: self.owner_scope,
                keepalive_seconds=None,
            )
            endpoint = next(
                route.endpoint
                for route in inner.routes
                if isinstance(route, APIRoute) and route.path == "/"
            )
            self.sessions[thread_id] = (endpoint, store, asyncio.Lock())
        return self.sessions[thread_id]

    async def respond(self, data: AGUIRequest) -> Response:
        thread_id = TypeAdapter(Id).validate_python(data.thread_id)
        if data.tools or data.context or data.forwarded_props:
            raise ValueError("Client tools, context and forwarded properties are not accepted.")
        if data.resume:
            for entry in data.resume:
                decision = entry.payload
                if entry.status == "cancelled":
                    continue
                if (
                    not isinstance(decision, dict)
                    or set(decision) != {"approved"}
                    or type(decision["approved"]) is not bool
                ):
                    raise ValueError("Approval may only resolve the exact pending invocation.")
        async with self.creation_lock:
            endpoint, store, lock = await self.session(thread_id)
        if lock.locked():
            return JSONResponse({"detail": "A scene run is active."}, status_code=409)
        await lock.acquire()
        streaming = False
        try:
            # Client snapshots are display caches, not replacement server state.
            selection = (data.state or {}).get("selectedIds")
            if selection is not None:
                await store.select(selection)
            data.state = {}
            known = {message["id"]: message for message in store.snapshot.messages}
            fresh = []
            for message in data.messages:
                if message.get("id") in known:
                    if message != known[message["id"]]:
                        raise ValueError("Stored messages cannot be replaced.")
                    continue
                if (
                    message.get("role") != "user"
                    or not isinstance(message.get("content"), str)
                    or set(message) - {"id", "role", "content"}
                    or len(message["content"]) > 8000
                ):
                    raise ValueError("Only new user text or unchanged history is accepted.")
                fresh.append(message)
            if len(fresh) > 1 or (data.resume and fresh):
                raise ValueError("Send one new user message, or an approval, not both.")
            data.messages = fresh
            response = await endpoint(data)
            if isinstance(response, StreamingResponse):
                original = response.body_iterator

                async def stream():
                    try:
                        async for chunk in original:
                            yield chunk
                    finally:
                        lock.release()

                response.body_iterator = stream()
                streaming = True
            return response
        finally:
            if not streaming:
                lock.release()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        request = Request(scope, receive)
        if request.method != "POST":
            await JSONResponse({"detail": "POST required."}, status_code=405)(scope, receive, send)
            return
        origin = request.headers.get("origin")
        if origin and not is_loopback_url(origin):
            await JSONResponse({"detail": "Local origin required."}, status_code=403)(
                scope, receive, send
            )
            return
        try:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 262144:
                    raise ValueError("Request exceeds 256 KiB.")
            data = AGUIRequest.model_validate_json(body)
            response = await self.respond(data)
            await response(scope, receive, send)
        except (ValueError, ValidationError) as error:
            logger.info("Rejected scene request: %s", type(error).__name__)
            await JSONResponse({"detail": "Invalid scene request."}, status_code=422)(
                scope, receive, send
            )


def create_app(settings: Settings, client: SupportsChatGetResponse) -> FastAPI:
    app = FastAPI(title="Agent 3D")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
    app.mount("/agent", SceneGateway(settings, client))
    return app


def create_model_client(
    settings: Settings, *, hosted: bool = False
) -> tuple[SupportsChatGetResponse, list[Callable[[], Awaitable[None]]]]:
    close: list[Callable[[], Awaitable[None]]]
    function_configuration: FunctionInvocationConfiguration = {
        "allow_concurrent_invocation": False,
    }
    if settings.provider == "foundry":
        from agent_framework.foundry import FoundryChatClient
        from azure.ai.projects.aio import AIProjectClient
        from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential

        credential = (
            ManagedIdentityCredential(client_id=os.environ.get("AZURE_CLIENT_ID"))
            if hosted
            else AzureCliCredential(process_timeout=30)
        )
        project = AIProjectClient(endpoint=settings.endpoint, credential=credential)
        model_client = FoundryChatClient(
            project_client=project,
            model=settings.model,
            credential=credential,
            function_invocation_configuration=function_configuration,
        )
        close = [project.close, credential.close]
    else:
        from agent_framework.openai import OpenAIChatCompletionClient
        from openai import AsyncOpenAI, DefaultAsyncHttpxClient

        api_key = os.environ.get("AGENT_API_KEY")
        if settings.provider == "openai" and not api_key:
            raise ValueError("AGENT_API_KEY is required for the connected OpenAI profile.")
        transport = DefaultAsyncHttpxClient(trust_env=settings.provider != "local", timeout=60)
        api = AsyncOpenAI(
            base_url=settings.endpoint,
            api_key=api_key or "local-no-key",
            http_client=transport,
            max_retries=0,
        )
        model_client = OpenAIChatCompletionClient(
            model=settings.model,
            async_client=api,
            function_invocation_configuration=function_configuration,
        )
        close = [api.close]
    return model_client, close


def configured_app() -> FastAPI:
    settings = Settings.from_env()
    # The adapter logs complete tool events at INFO; retain warnings without payload capture.
    logging.getLogger("agent_framework.ag_ui").setLevel(logging.WARNING)
    logging.getLogger("agent_framework_ag_ui").setLevel(logging.WARNING)
    if settings.otlp_endpoint:
        configure_otel_providers(
            service_name="agent-3d-py",
            otlp_endpoint=settings.otlp_endpoint,
            otlp_protocol="http/protobuf",
            otlp_timeout=2,
            enable_sensitive_data=False,
            enable_message_events=False,
            enable_console_exporters=False,
        )
    model_client, close = create_model_client(settings)
    app = create_app(settings, model_client)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async with AsyncExitStack() as stack:
            for callback in close:
                stack.push_async_callback(callback)
            yield

    app.router.lifespan_context = lifespan
    return app


def main() -> None:
    import uvicorn

    uvicorn.run(configured_app(), host="127.0.0.1", port=8000)
