"""Model boundary: plain data only, no filesystem or tool execution capabilities."""
from dataclasses import dataclass, field
import re
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
    memory: dict = field(default_factory=dict)
    feedback_mode: str = 'historical'


@dataclass(frozen=True)
class ModelResponse:
    action: str = 'CANDIDATE'
    strategy_id: str = ''
    description: str = ''
    rtl: str | None = None
    usage: dict = field(default_factory=lambda: {'mode': 'mock', 'input_tokens': 0,
                                                'output_tokens': 0, 'real_api_calls': 0})
    artifacts: dict = field(default_factory=dict)


def empty_usage(mode='mock'):
    return {'mode': mode, 'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0,
            'charged_tokens': 0, 'real_api_calls': 0, 'calls': 0, 'retries': 0,
            'unknown_usage_calls': 0, 'api_seconds': 0.0}


class ModelError(ValueError):
    """Safe diagnostics with accounting preserved even when no RTL can be parsed."""
    def __init__(self, code, message, *, usage=None, artifacts=None):
        super().__init__(message)
        self.code = code
        self.usage = usage or empty_usage('deepseek')
        self.artifacts = artifacts or {}


class ModelClient(Protocol):
    def generate(self, request: ModelRequest, *, timeout: float) -> ModelResponse:
        """Return data within timeout; future clients must enforce request timeouts."""
        ...


def is_strategy_label(value):
    """A reporting label, not an allowlist of transformations."""
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_+.-]{1,120}', value) is not None
