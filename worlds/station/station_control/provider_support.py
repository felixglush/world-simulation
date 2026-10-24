"""Shared bounded request mechanics for model-provider adapters.

Role-specific prompts, response validation, and SDK clients stay in each adapter.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

from .controllers import ProviderError, ProviderErrorCode

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_INPUT_CHARS = 40_000
MAX_ARGUMENT_CHARS = 12_000


@dataclass(slots=True)
class CallBudget:
    """Shared finite provider-call budget and per-call output-token ceiling."""

    max_calls: int
    max_output_tokens_per_call: int
    _used_calls: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.max_calls) is not int or self.max_calls < 1:
            raise ValueError("max_calls must be a positive integer")
        if type(self.max_output_tokens_per_call) is not int or self.max_output_tokens_per_call < 1:
            raise ValueError("max_output_tokens_per_call must be a positive integer")

    def consume(self) -> int:
        """Spend one request before network I/O and return its output-token limit."""
        if self._used_calls >= self.max_calls:
            raise ProviderError(ProviderErrorCode.BUDGET_EXHAUSTED)
        self._used_calls += 1
        return self.max_output_tokens_per_call


def validate_configuration(
    api_key: str,
    model: str,
    base_url: str,
    timeout: float,
    budget: CallBudget,
) -> None:
    if not all(isinstance(value, str) and value.strip() for value in (api_key, model, base_url)):
        raise ProviderError(ProviderErrorCode.INVALID_INPUT)
    if (
        not isinstance(budget, CallBudget)
        or type(timeout) not in (int, float)
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ProviderError(ProviderErrorCode.INVALID_INPUT)


def ensure_bounded(value: object) -> None:
    try:
        serialized = json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise ProviderError(ProviderErrorCode.INVALID_INPUT) from None
    if len(serialized) > MAX_INPUT_CHARS:
        raise ProviderError(ProviderErrorCode.INVALID_INPUT)


def reported_cost(usage: object) -> float | None:
    extra = getattr(usage, "model_extra", None)
    value = extra.get("cost") if isinstance(extra, Mapping) else getattr(usage, "cost", None)
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def measure_latency_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def call_metadata(
    *,
    provider: str,
    model: str,
    input_tokens: object,
    output_tokens: object,
    cost: float | None,
    elapsed_ms: float,
) -> dict[str, object]:
    return {
        "provider": provider,
        "calls": 1,
        "request_made": True,
        "model": model,
        "input_tokens": input_tokens if type(input_tokens) is int and input_tokens >= 0 else None,
        "output_tokens": output_tokens
        if type(output_tokens) is int and output_tokens >= 0
        else None,
        "cost_usd": cost,
        "latency_ms": elapsed_ms,
    }


def failure_metadata(
    provider: str,
    model: object,
    *,
    request_made: bool,
    latency_ms: float,
) -> dict[str, object]:
    return {
        "provider": provider,
        "model": model if isinstance(model, str) else "unknown",
        "calls": int(request_made),
        "request_made": request_made,
        "latency_ms": latency_ms,
    }
