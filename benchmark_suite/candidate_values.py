from __future__ import annotations

from typing import Any

from utils.scalar_values import optional_float as maybe_float


def candidate_float(candidate: dict[str, Any], key: str, default: float) -> float:
    """Read a float-like candidate field using the benchmark fallback contract."""
    value = maybe_float(candidate.get(key))
    return default if value is None else value


def candidate_int(candidate: dict[str, Any], key: str, default: int) -> int:
    """Read an int-like candidate field using the benchmark fallback contract."""
    value = candidate.get(key)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
