# Third-party notices

Original application code is licensed under [MIT](LICENSE). Installed dependencies retain their own licenses and copyright notices; this repository does not replace them.

- [Microsoft Agent Framework](https://github.com/microsoft/agent-framework/blob/main/LICENSE): MIT. Used for the Harness, native skills, function invocation, approval, sessions, provider clients and AG-UI integration.
- [Azure AI AgentServer](https://github.com/azure/azure-sdk-for-python/blob/main/LICENSE): MIT. Used for optional Invocations hosting and platform storage.
- [AG-UI](https://github.com/ag-ui-protocol/ag-ui/blob/main/LICENSE): MIT. Native event protocol and SDK.
- [FastAPI](https://github.com/fastapi/fastapi/blob/master/LICENSE) and [Pydantic](https://github.com/pydantic/pydantic/blob/main/LICENSE): MIT. HTTP hosting and validation.
- [OpenTelemetry Python](https://github.com/open-telemetry/opentelemetry-python/blob/main/LICENSE): Apache-2.0. Instrumentation and optional exporters.
- [agent-3d-ui](https://github.com/ericchansen/agent-3d-ui): MIT. Vendored application schema and original synthetic fixtures under `agent_3d/contracts`.

The exact direct and transitive dependencies are in [uv.lock](uv.lock). Distribution packages include their upstream license metadata. Model-server software and model weights are not bundled; obtain and comply with their separate licenses.
