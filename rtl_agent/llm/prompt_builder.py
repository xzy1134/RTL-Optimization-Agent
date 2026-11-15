"""Render model messages without invoking a provider or parsing its output."""
from dataclasses import asdict
import json
from pathlib import Path
from string import Template

from .client import ModelRequest

PROMPTS = Path(__file__).parent / 'prompts'


def build_messages(request: ModelRequest) -> list[dict[str, str]]:
    context = json.dumps(asdict(request), ensure_ascii=False, indent=2)
    return [
        {'role': 'system', 'content': (PROMPTS / 'system.md').read_text(encoding='utf-8')},
        {'role': 'user', 'content': Template((PROMPTS / 'user.md').read_text(encoding='utf-8')).substitute(context=context)},
    ]
