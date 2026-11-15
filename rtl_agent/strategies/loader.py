"""Safe YAML loading with duplicate-key and duplicate-id checks."""
from pathlib import Path
import yaml

from .schema import StrategyError, validate_strategy


class StrictLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            if key in result:
                raise StrategyError(f"Duplicate YAML key: {key}")
            result[key] = loader.construct_object(value_node, deep=deep)
        except TypeError as exc:
            raise StrategyError("YAML mapping keys must be scalar") from exc
    return result


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_strategies(directory=None):
    directory = Path(directory or Path(__file__).parent)
    files = sorted([*directory.glob('*.yaml'), *directory.glob('*.yml')])
    if not files:
        raise StrategyError(f"No strategy YAML files in {directory}")
    strategies, seen = [], set()
    for path in files:
        try:
            data = yaml.load(path.read_text(encoding='utf-8'), Loader=StrictLoader)
            strategy = validate_strategy(data)
            if strategy.id in seen:
                raise StrategyError(f"Duplicate strategy id: {strategy.id}")
            seen.add(strategy.id)
            strategies.append(strategy)
        except (OSError, yaml.YAMLError, StrategyError) as exc:
            raise StrategyError(f"{path.name}: {exc}") from exc
    return tuple(sorted(strategies, key=lambda s: s.id))
