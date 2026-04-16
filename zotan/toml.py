from __future__ import annotations

from typing import Any


def remove_none(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: remove_none(v) for k, v in o.items() if v is not None}  # type: ignore[reportUnknownVariableType]
    elif isinstance(o, list):
        return [remove_none(v) for v in o]  # type: ignore[reportUnknownVariableType]
    else:
        return o


def deep_merge_dict(base: Any, override: Any) -> Any:
    """Recursively merge two dictionaries or replace values.

    Used for overlaying multiple YAML config files, where later configs
    override earlier ones. Nested dictionaries are merged recursively.

    Args:
        base: The base dictionary to merge into
        override: The override dictionary - its values take precedence

    Returns:
        Merged dictionary with override values taking precedence
    """
    merged: dict[str, Any] = {}
    if override is None:
        return base
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override
    for key in set(base.keys()) | set(override.keys()):  # type: ignore[reportUnknownVariableType,reportUnknownArgumentType]
        merged[key] = deep_merge_dict(base.get(key), override.get(key))  # type: ignore[reportUnknownArgumentType]
    return merged
