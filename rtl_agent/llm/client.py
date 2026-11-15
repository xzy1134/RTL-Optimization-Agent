"""Model boundary: plain data only, no filesystem or tool execution capabilities."""
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ModelRequest:
    original_rtl: str
    parent_rtl: str
    top: str
    primary_strategy: str
    strategies: list[dict]
    metrics: dict
    feedback: list[dict]
    constraints: dict


@dataclass(frozen=True)
class ModelResponse:
    action: str = 'CANDIDATE'
    strategy_id: str = ''
    description: str = ''
    rtl: str | None = None
    usage: dict = field(default_factory=lambda: {'mode': 'mock', 'input_tokens': 0,
                                                'output_tokens': 0, 'real_api_calls': 0})


class ModelClient(Protocol):
    def generate(self, request: ModelRequest, *, timeout: float) -> ModelResponse:
        """Return data within timeout; future clients must enforce request timeouts."""
        ...
