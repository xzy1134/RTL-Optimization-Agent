"""Replay source candidates, never verification or PPA results."""
from dataclasses import replace
import time

from .client import ModelResponse


class MockLLM:
    def __init__(self, responses=(), *, delay_seconds=0):
        self.responses = tuple(responses)
        self.index = 0
        self.delay_seconds = delay_seconds
        self.requests = []

    def generate(self, request, *, timeout):
        self.requests.append(request)
        if self.delay_seconds >= timeout:
            time.sleep(max(0, timeout))
            raise TimeoutError('Mock request exceeded remaining runtime budget')
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if self.index >= len(self.responses):
            return ModelResponse(action='NO_CHANGE', description='Mock sequence exhausted')
        response = self.responses[self.index]
        self.index += 1
        return replace(response, strategy_id=response.strategy_id or request.primary_strategy)
