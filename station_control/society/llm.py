"""Chat-model port: structured JSON in, structured JSON out, one shared call budget."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol

from openai import OpenAI

from ..controllers import ProviderError, ProviderErrorCode
from ..provider_support import (
    CallBudget,
    call_metadata,
    ensure_bounded,
    failure_metadata,
    measure_latency_ms,
    reported_cost,
)

BASE_URL = "https://openrouter.ai/api/v1"
LUNA = "openai/gpt-6-luna"
TERRA = "openai/gpt-5.6-terra"
# Without a key in the environment the cloud proxy injects the credential on this host.
PLACEHOLDER_KEY = "proxy-injected"

DEFAULT_MODELS = {
    "react": LUNA,
    "score": LUNA,
    "supplier": LUNA,
    "captain": TERRA,
    "adversary": TERRA,
    "reflect": TERRA,
}


@dataclass(frozen=True, slots=True)
class ChatRequest:
    role: str
    purpose: str
    system: str
    user: str
    schema: Mapping[str, object]
    max_tokens: int = 1200


@dataclass(frozen=True, slots=True)
class ChatReply:
    data: dict[str, object]
    metadata: Mapping[str, object] = field(default_factory=dict)


class ChatModel(Protocol):
    def complete(self, request: ChatRequest) -> ChatReply: ...


class OpenRouterChat:
    """Forced tool call so every reply is a JSON object matching the request schema."""

    def __init__(
        self,
        models: Mapping[str, str],
        budget: CallBudget,
        *,
        api_key: str | None = None,
        base_url: str = BASE_URL,
        timeout: float = 60.0,
        client: OpenAI | None = None,
    ) -> None:
        self._models = dict(models)
        self._budget = budget
        key = (api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")).strip()
        self._client = client or OpenAI(
            api_key=key or PLACEHOLDER_KEY, base_url=base_url, max_retries=0, timeout=timeout
        )

    def close(self) -> None:
        self._client.close()

    def complete(self, request: ChatRequest) -> ChatReply:
        model = self._models.get(request.role)
        if model is None:
            raise ProviderError(ProviderErrorCode.INVALID_INPUT)
        ensure_bounded([request.system, request.user, request.schema])
        limit = self._budget.consume()
        started = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=model,
                max_completion_tokens=min(limit, request.max_tokens),
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "respond",
                            "description": "Return the structured answer.",
                            "parameters": request.schema,
                        },
                    }
                ],
                tool_choice={"type": "function", "function": {"name": "respond"}},
                extra_body={"reasoning": {"effort": "low"}},
            )
        except Exception as error:
            raise ProviderError(
                ProviderErrorCode.PROVIDER_UNAVAILABLE,
                failure_metadata("openrouter", model, request_made=True, latency_ms=_ms(started)),
            ) from error
        elapsed = measure_latency_ms(started)
        usage = getattr(response, "usage", None)
        metadata = call_metadata(
            provider="openrouter",
            model=model,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            cost=reported_cost(usage),
            elapsed_ms=elapsed,
        )
        choice = response.choices[0] if getattr(response, "choices", None) else None
        diagnostic = {
            "finish_reason": getattr(choice, "finish_reason", None),
            "has_tool_call": bool(getattr(getattr(choice, "message", None), "tool_calls", None)),
            "reply_head": _head(choice),
        }
        try:
            message = choice.message
            raw = (
                message.tool_calls[0].function.arguments if message.tool_calls else message.content
            )
            data = json.loads(raw)
        except Exception:
            raise ProviderError(
                ProviderErrorCode.MALFORMED_RESPONSE, {**metadata, **diagnostic}
            ) from None
        if not isinstance(data, dict):
            raise ProviderError(ProviderErrorCode.MALFORMED_RESPONSE, metadata)
        return ChatReply(data, metadata)


def _head(choice: object) -> str:
    message = getattr(choice, "message", None)
    calls = getattr(message, "tool_calls", None)
    raw = calls[0].function.arguments if calls else getattr(message, "content", "")
    return str(raw)[:160]


def _ms(started: float) -> float:
    return measure_latency_ms(started)


class FakeChat:
    """Offline model: a handler maps each request to a reply and every request is kept."""

    def __init__(
        self,
        handler: Callable[[ChatRequest], dict[str, object]],
        budget: CallBudget | None = None,
    ) -> None:
        self._handler = handler
        self._budget = budget
        self.requests: list[ChatRequest] = []

    def complete(self, request: ChatRequest) -> ChatReply:
        if self._budget is not None:
            self._budget.consume()
        self.requests.append(request)
        return ChatReply(
            dict(self._handler(request)),
            {"provider": "fake", "model": request.role, "calls": 1, "request_made": False},
        )
