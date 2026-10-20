# Agent 3D for Python

A small synthetic scene example built on the [Microsoft Agent Framework Harness](https://learn.microsoft.com/en-us/agent-framework/concepts/harness). The Harness owns tool execution, skills, history, compaction, modes, memory and approval. Application code owns bounded scene edits, request validation and atomic persistence.

The scene contains boxes, spheres and cylinders. You can inspect objects, highlight matches, and approve color or absolute-position changes. Adding, deleting, resizing and importing geometry are not supported. Original code is [MIT licensed](LICENSE); model weights are not included.

## Run locally

Install Python 3.13 and [uv](https://docs.astral.sh/uv/). Start a tool-capable OpenAI-compatible model server separately. The application does not download a model.

```powershell
uv sync --frozen
$env:AGENT_PROVIDER = "local"
$env:AGENT_ENDPOINT = "http://127.0.0.1:11434/v1"
$env:AGENT_MODEL = "your-local-model"
$env:AGENT_CONTEXT_TOKENS = "16384"
$env:AGENT_OUTPUT_TOKENS = "1024"
uv run --no-sync agent-3d
```

The server binds to `127.0.0.1:8000`. Configure an AG-UI client, such as [agent-3d-ui](https://github.com/ericchansen/agent-3d-ui), to use `http://127.0.0.1:8000/agent/`. Keep this synthetic single-user profile on loopback; it is not an anonymous multi-user service.

The first empty-message request creates a scene without calling the model:

```powershell
$body = @{
  threadId = "demo"; runId = "hydrate"; messages = @()
  state = @{}; tools = @(); context = @(); forwardedProps = @{}
} | ConvertTo-Json
Invoke-WebRequest http://127.0.0.1:8000/agent/ -Method Post -ContentType application/json -Body $body
```

Expect native SSE with `STATE_SNAPSHOT` containing [the seed scene](agent_3d/contracts/fixtures/seed.scene.json): 12 objects at revision 0. Ask "Find the blue cylinders and highlight them." The expected highlighted ids are `cylinder-01` and `cylinder-02`, not a material-color change. Then ask to move `box-01` upward by two units. Approve the exact native tool request; its final position should be `(-4, 3, -3)` and the revision should advance once. Model text alone is not proof: inspect the returned scene and receipt.

Standard Harness capabilities remain enabled except web search and the temporary auto-allow exception described below. Native skill loading can itself require approval. Shell, arbitrary file access and background agents are not opted in.

## Providers and limits

| Setting | Meaning |
|---|---|
| `AGENT_PROVIDER` | `local` (default), `openai`, or `foundry` |
| `AGENT_ENDPOINT` | Required explicit model API base URL or Foundry project endpoint |
| `AGENT_MODEL` | Required model/deployment name |
| `AGENT_API_KEY` | Required for connected `openai`; optional for a local endpoint |
| `AGENT_CONTEXT_TOKENS` | Context budget; default 8192 |
| `AGENT_OUTPUT_TOKENS` | Output budget; default 1024, smaller than the context budget |
| `AGENT_DATA_DIR` | Local snapshot/memory directory; default `.data` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Optional OTLP/HTTP destination; telemetry export is otherwise not configured |

The native client boundary is [implemented here](agent_3d/app.py). Local and compatible providers use `OpenAIChatCompletionClient`; Foundry uses `FoundryChatClient`. There is no provider failover. Restart with a new data directory when changing provider/model so provider-private continuation state is not transferred.

The local profile accepts only loopback model and telemetry URLs and does not use environment HTTP proxies. Connected endpoints require HTTPS. Function calling retains the native defaults: 40 model roundtrips, with no additional cumulative call-count or duration cap. Function execution is sequential. Configure appropriate model-server request timeouts; the roundtrip limit is not a wall-clock deadline.

For connected Foundry development, install `uv sync --frozen --extra foundry`, sign in using Azure CLI, and set `AGENT_PROVIDER=foundry`, `AGENT_ENDPOINT` to your project endpoint, and `AGENT_MODEL` to your deployment. Development uses `AzureCliCredential`; the hosted entrypoint uses managed identity. Do not put credentials in files committed to this repository.

## State, approval and reconnect

The [application contract](agent_3d/contracts/application.schema.json) is a pinned copy of the public UI contract. Positions are finite and bounded to `[-1000, 1000]`; dimensions are full extents in `[0.1, 100]`. Scenes contain at most 64 objects; edits affect at most 16 distinct existing ids. A null or absent edit field means unchanged; at least one non-null field is required.

`list_objects(shape?)` reads actual names/colors/tags. `get_objects(ids)` reads exact objects. `highlight_objects(ids)` changes only view highlights. `edit_objects(ids, expectedRevision, edit)` requires native approval and revalidates the revision and values before writing.

Client scene snapshots are ignored as authority. A top-level `state.selectedIds` is accepted only when it identifies existing scene objects; the native adapter publishes the authoritative scene before a pending approval so clients retain that selection. Client tools, context, forwarded properties and fabricated conversation messages are rejected. Approval payloads may contain only the Boolean `approved`; the SDK owns pending identity, argument binding and replay checks. The adapter emits canonical approval interrupts without a redundant legacy `confirm_changes` tool card. Follow each native interrupt's response schema rather than replacing its arguments.

**Temporary SDK compatibility exception:** core 1.21.0 with AG-UI 1.5.1 retains a settled approval's invocation budget when the Harness auto-allow middleware is active. Repeated denials can exhaust the native iteration budget across unrelated user turns. This also reproduces with the SDK's own in-memory snapshot store. The sample uses the public [`disable_tool_auto_approval=True` option](https://github.com/microsoft/agent-framework/blob/python-1.21.0/python/packages/core/agent_framework/_harness/_agent.py) until a compatible native fix is verified. This removes standing "don't ask again" rules and queued auto-approval coordination, not per-action approval, pending-call binding or replay protection. No private session state is scrubbed and no replacement approval system is added.

Use the same thread id, a new run id and `messages: []` to reconnect without another model call. Native snapshots replay scene, conversation and pending interrupts. A process restart preserves committed scene/receipt data but invalidates unresolved approvals and reports `SESSION_RESET`. Start a new thread for a fresh scene. The demo holds at most 64 scenes per process and serializes each scene's runs.

Scene and receipt are written together before a success event. Local storage uses an atomic file replacement. Native AG-UI persists conversation continuation through its snapshot-store interface; its later save failures are logged by the SDK and can leave conversation history behind the durable scene. A completed edit is never rolled back merely because final text was lost.

## Telemetry and offline use

Install `uv sync --frozen --extra telemetry` and configure `OTEL_EXPORTER_OTLP_ENDPOINT`, for example `http://127.0.0.1:4318`, to send native agent/client telemetry and scene edit/save spans to [standalone Aspire Dashboard](https://aspire.dev/dashboard/standalone/) or another OTLP receiver. Set receiver authentication through `OTEL_EXPORTER_OTLP_HEADERS`. Keep dashboard authentication and ingestion protection enabled; do not commit tokens.

Prompt/tool payload capture and message-event capture are disabled. Scene outcome counters have only bounded outcome labels. Native reported model usage may be absent; absence is not zero usage.

For an air-gapped deployment, stage the Python environment, UI assets, model server/weights and optional dashboard beforehand. Run the installed application with `uv run --offline --no-sync agent-3d`; do not run an install or model download at runtime. The local-only installation does not require the Foundry extra. With that extra present, the SDK may import its hosted-context support even for a local client; importing support code is not a cloud call.

Full local/offline qualification is not yet established. A process-guarded run of [Qwen3-4B-Instruct](https://ollama.com/library/qwen3:4b-instruct-2507-q4_K_M) completed the canonical read/highlight flow, with external asynchronous DNS and direct-IP negative controls blocked and actual workload connections confined to loopback. The subsequent edit request returned prose rather than a native tool call (`tool_choice=auto`, `finish_reason=stop`), so the full local approval/edit flow remains unqualified. Do not treat deterministic tests or the partial read/highlight result as proof that an arbitrary local model can complete the scenario.

## Foundry Hosted Agent

The separate [hosted entrypoint](agent_3d/hosted.py) uses the official Invocations host and the same native AG-UI response. It requires the authenticated Foundry platform context; it refuses ordinary local invocations. It does not implement login or JWT validation.

Hosted state uses the SDK's `FoundryRequestScope` and `FoundryStateStore`, partitioned by platform user/session and thread, with create-only initial writes and ETag-conditional replacements. Managed identity supplies model access. File-memory cache remains session-local; durable scene and native conversation snapshots use the platform store.

Hosted file memory defaults to `~/.agent-3d` under the platform's writable home directory, not the read-only application package directory. An explicit `AGENT_DATA_DIR` overrides that default and must be writable. Local development still defaults to `.data`.

Generate a deployment dependency file from the public lock:

```powershell
uv export --frozen --no-dev --extra foundry --no-emit-project --no-header --output-file requirements.txt
```

Package `agent_3d`, `requirements.txt`, `pyproject.toml`, `uv.lock` and `LICENSE`. Exclude `.data`, `.env`, `.venv`, caches and tests. Set the startup command to `python -m agent_3d.hosted` and the application variables `AGENT_PROVIDER=foundry`, `AGENT_ENDPOINT`, `AGENT_MODEL`, and the token budgets. The platform must supply its session/context variables and protocol 2.0 identity headers. Use the [official programmatic deployment guidance](https://learn.microsoft.com/en-us/agent-framework/hosting/foundry-hosted-agent); never deploy this entrypoint as a public standalone HTTP server that trusts arbitrary forwarded headers.

Actual deployed Invocations verification covered authenticated hydration, native skill approval, exact read/highlight results, an approved atomic edit, rejection, replay and reconnect against platform-backed state. The final native adapter configuration also passed the normal authenticated UI -> managed identity -> hosted agent flow: selection and transcript survived pending reload, approval committed once, committed reload retained the edit, and a rejected second edit left the scene unchanged. Canonical approval remained gated without a legacy helper card.

An official hosted-session stop and subsequent resumption were verified separately on the preceding deployment: the exact committed scene and receipt survived, `SESSION_RESET` was reported, and the pre-stop pending approval was rejected without another write. This verifies that pending-approval lifecycle case, not every platform failure mode. An earlier deployment needed an explicit follow-up to call `edit_objects` instead of requesting approval only in prose; model text is not an approval interrupt. Local Invocations tests use the real SDK transport, scripted model output and the SDK's local storage backend; they do not replace cloud verification.

## Extend and check

Add trusted skill content under `agent_3d/skills`; native skill discovery loads it. Add a typed tool in `SceneStore.tools()` for new behavior, retaining native approval on mutations and validating domain state inside the function. No new router, graph or planner is needed.

```powershell
uv sync --frozen --all-extras
uv run --no-sync ruff check .
uv run --no-sync ty check
uv run --no-sync pytest
uv build
```

Tests cover contract semantics, save failures, native Harness approval, replay, reconnect, client trust, scoped hosted storage and the actual Invocations adapter. The [lock](uv.lock) pins the tested dependency set: framework core 1.21.0, AG-UI adapter 1.5.1, protocol 0.1.22, OpenAI adapter 1.16.0 and Foundry adapter 1.14.1. Foundry hosting `1.0.0b261008` and file-memory storage APIs include preview/experimental surfaces. See [third-party notices](THIRD_PARTY_NOTICES.md).
