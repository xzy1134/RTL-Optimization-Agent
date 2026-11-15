"""Validated strategy data. No executable rules are loaded from YAML."""
from dataclasses import dataclass
import re
from urllib.parse import urlparse


class StrategyError(ValueError):
    pass


@dataclass(frozen=True)
class Strategy:
    schema_version: int
    id: str
    version: int
    name: str
    goal: str
    applicable_when: tuple[str, ...]
    detection_hints: tuple[str, ...]
    transformation: tuple[str, ...]
    risks: tuple[str, ...]
    validation: tuple[str, ...]
    requires: tuple[str, ...]
    forbidden_changes: tuple[str, ...]
    evidence_level: str
    source: tuple[dict, ...]
    enabled: bool


def validate_strategy(data):
    if not isinstance(data, dict):
        raise StrategyError("Strategy must be a mapping")
    if any(not isinstance(k, str) for k in data):
        raise StrategyError("Strategy keys must be strings")
    fields = set(Strategy.__dataclass_fields__)
    if set(data) != fields:
        raise StrategyError(f"Missing fields: {sorted(fields-set(data))}; unknown fields: {sorted(set(data)-fields)}")
    if type(data['schema_version']) is not int or data['schema_version'] != 1:
        raise StrategyError("schema_version must be 1")
    if type(data['version']) is not int or data['version'] < 1:
        raise StrategyError("version must be a positive integer")
    if not isinstance(data['id'], str) or not re.fullmatch(r'[a-z][a-z0-9_]*', data['id']):
        raise StrategyError("Invalid strategy id")
    if type(data['enabled']) is not bool:
        raise StrategyError("enabled must be boolean")
    for key in ('name', 'goal', 'evidence_level'):
        if not isinstance(data[key], str) or not data[key].strip():
            raise StrategyError(f"{key} must be nonempty text")
    if data['evidence_level'] not in ('reference_guidance_not_locally_measured', 'locally_measured'):
        raise StrategyError("Invalid evidence_level")
    converted = dict(data)
    for key in ('applicable_when', 'detection_hints', 'transformation', 'risks', 'validation', 'requires', 'forbidden_changes'):
        values = data[key]
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
            raise StrategyError(f"{key} must be a nonempty text list")
        converted[key] = tuple(values)
    sources = data['source']
    if not isinstance(sources, list) or not sources:
        raise StrategyError("source must be a nonempty list")
    for source in sources:
        if not isinstance(source, dict) or set(source) != {'url', 'section', 'supports'}:
            raise StrategyError("Each source needs url, section and supports")
        if any(not isinstance(v, str) or not v.strip() for v in source.values()):
            raise StrategyError("Invalid source text")
        url = urlparse(source['url'])
        if url.scheme != 'https' or not url.netloc:
            raise StrategyError("Sources must use HTTPS URLs")
    converted['source'] = tuple(dict(s) for s in sources)
    return Strategy(**converted)
