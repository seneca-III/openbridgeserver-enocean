"""JSON helpers for runtime values."""

from __future__ import annotations

import json
from datetime import date, datetime, time
from typing import Any


def json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def json_dumps(value: Any) -> str:
    return json.dumps(value, default=json_default)


def jsonable(value: Any) -> Any:
    """Return JSON-compatible runtime values without following cycles."""
    return _jsonable(value, set())


def _jsonable(value: Any, active_containers: set[int]) -> Any:
    if isinstance(value, dict):
        container_id = id(value)
        if container_id in active_containers:
            return "<recursive dict>"
        active_containers.add(container_id)
        try:
            return {_jsonable_key(key): _jsonable(item, active_containers) for key, item in value.items()}
        finally:
            active_containers.remove(container_id)
    if isinstance(value, (list, tuple)):
        container_id = id(value)
        if container_id in active_containers:
            return f"<recursive {type(value).__name__}>"
        active_containers.add(container_id)
        try:
            return [_jsonable(item, active_containers) for item in value]
        finally:
            active_containers.remove(container_id)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return value


def _jsonable_key(key: Any) -> Any:
    if isinstance(key, (str, int, float, bool)) or key is None:
        return key
    if isinstance(key, (date, datetime, time)):
        return key.isoformat()
    return str(key)
