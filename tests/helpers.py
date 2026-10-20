"""Integration checks use a scripted model, never a replacement agent loop."""

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any
from uuid import uuid4

from agent_framework import (
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    Message,
    ResponseStream,
)
from agent_framework.observability import ChatTelemetryLayer


class ScriptedClient(
    FunctionInvocationLayer, ChatMiddlewareLayer, ChatTelemetryLayer, BaseChatClient
):
    """Supply model outputs while the real Harness owns execution and approval."""

    def __init__(self, arguments: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.calls = 0
        self.propose_next = True
        self.arguments = arguments or {
            "ids": ["box-01"],
            "expectedRevision": 0,
            "edit": {"color": "#16A34A"},
        }

    def _inner_get_response(
        self,
        *,
        messages: Sequence[Message],
        stream: bool,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> ResponseStream[ChatResponseUpdate, ChatResponse]:
        self.calls += 1
        contents = (
            [
                Content.from_function_call(
                    f"edit-{self.calls}", "edit_objects", arguments=self.arguments
                )
            ]
            if self.propose_next
            else [Content.from_text("Scene operation finished.")]
        )
        self.propose_next = False

        async def updates() -> AsyncIterator[ChatResponseUpdate]:
            yield ChatResponseUpdate(
                role="assistant",
                contents=contents,
                message_id=str(uuid4()),
                response_id=str(uuid4()),
            )

        return ResponseStream(updates(), finalizer=ChatResponse.from_updates)


def events(response: Any) -> list[dict[str, Any]]:
    assert response.status_code == 200, response.text
    result = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert not [event for event in result if event["type"] == "RUN_ERROR"], result
    return result
