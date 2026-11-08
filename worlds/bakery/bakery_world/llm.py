import json
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

OPENROUTER_URL = "https://openrouter.ai/api/v1"
PLACEHOLDER_KEY = "injected-by-proxy"
MANAGER_MODEL = "openai/gpt-5.6-terra"
ANALYST_MODEL = "openai/gpt-6-luna"
WRITER_MODEL = "openai/gpt-6-luna"
MAX_OUTPUT_TOKENS = 8000


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int | None = None
    output_tokens: int = 0
    cost: float | None = None


@dataclass(frozen=True)
class Reply:
    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = Usage()

    def message(self) -> dict:
        out: dict = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            out["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": c.arguments},
                }
                for c in self.tool_calls
            ]
        return out


class ChatModel(Protocol):
    name: str

    def complete(self, messages: list[dict], tools: list[dict]) -> Reply: ...


class ModelError(Exception):
    pass


class BudgetExhausted(Exception):
    pass


@dataclass
class CallBudget:
    limit: int
    used: int = 0

    @property
    def remaining(self) -> int:
        return self.limit - self.used

    def spend(self) -> None:
        if self.used >= self.limit:
            raise BudgetExhausted(f"call budget of {self.limit} exhausted")
        self.used += 1


@dataclass(frozen=True)
class CallRecord:
    role: str
    model: str
    input_tokens: int
    cached_input_tokens: int | None
    output_tokens: int
    latency_s: float
    cost: float | None


Sink = Callable[[CallRecord, Reply], None]


@dataclass
class Caller:
    budget: CallBudget
    sink: Sink = lambda record, reply: None

    def call(self, role: str, model: ChatModel, messages: list[dict], tools: list[dict]) -> Reply:
        self.budget.spend()
        started = time.perf_counter()
        reply = model.complete(messages, tools)
        u = reply.usage
        record = CallRecord(
            role,
            model.name,
            u.input_tokens,
            u.cached_input_tokens,
            u.output_tokens,
            round(time.perf_counter() - started, 3),
            u.cost,
        )
        self.sink(record, reply)
        return reply


class ToolError(Exception):
    pass


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, dict]
    handler: Callable[[dict], str]
    required: tuple[str, ...] | None = None

    def schema(self) -> dict:
        required = list(self.parameters) if self.required is None else list(self.required)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        }


def dispatch(tools: dict[str, Tool], call: ToolCall) -> str:
    tool = tools.get(call.name)
    if tool is None:
        return f"error: there is no tool {call.name!r}"
    try:
        args = json.loads(call.arguments or "{}")
    except json.JSONDecodeError:
        return "error: arguments must be a JSON object"
    if not isinstance(args, dict):
        return "error: arguments must be a JSON object"
    unknown = sorted(set(args) - set(tool.parameters))
    if unknown:
        return f"error: {call.name} does not take {', '.join(unknown)}"
    try:
        return tool.handler(args)
    except ToolError as e:
        return str(e)


def tool_result(call: ToolCall, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call.id, "content": content}


def _usage(raw: Any) -> Usage:
    if raw is None:
        return Usage()
    details = getattr(raw, "prompt_tokens_details", None)
    cost = getattr(raw, "cost", None)
    return Usage(
        raw.prompt_tokens or 0,
        getattr(details, "cached_tokens", None),
        raw.completion_tokens or 0,
        float(cost) if cost is not None else None,
    )


@dataclass
class OpenRouterChat:
    name: str
    max_output_tokens: int = MAX_OUTPUT_TOKENS
    client: Any = None

    def __post_init__(self) -> None:
        if self.client is None:
            from openai import OpenAI

            key = os.environ.get("OPENROUTER_API_KEY") or PLACEHOLDER_KEY
            self.client = OpenAI(base_url=OPENROUTER_URL, api_key=key)

    def complete(self, messages: list[dict], tools: list[dict]) -> Reply:
        from openai import OpenAIError

        try:
            response = self.client.chat.completions.create(
                model=self.name,
                messages=messages,
                max_tokens=self.max_output_tokens,
                extra_body={"usage": {"include": True}},
                **({"tools": tools} if tools else {}),
            )
        except OpenAIError as e:
            raise ModelError(f"{type(e).__name__}: {e}") from e
        if not response.choices:
            raise ModelError("the model returned no choices")
        msg = response.choices[0].message
        calls = tuple(
            ToolCall(c.id, c.function.name, c.function.arguments or "")
            for c in msg.tool_calls or ()
            if c.type == "function"
        )
        return Reply(msg.content, calls, _usage(response.usage))


Script = Sequence[Reply] | Callable[[list[dict], list[dict]], Reply]


@dataclass
class FakeChatModel:
    script: Script
    name: str = "fake"
    seen: list[tuple[list[dict], list[dict]]] = field(default_factory=list)

    def complete(self, messages: list[dict], tools: list[dict]) -> Reply:
        self.seen.append(([dict(m) for m in messages], tools))
        if callable(self.script):
            return self.script(messages, tools)
        n = len(self.seen) - 1
        return self.script[n] if n < len(self.script) else Reply("done")


def tool_call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(f"call-{name}", name, json.dumps(arguments))
