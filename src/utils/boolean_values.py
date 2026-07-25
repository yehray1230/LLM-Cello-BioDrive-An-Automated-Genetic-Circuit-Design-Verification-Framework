from __future__ import annotations

from typing import Any


def defaulted_bool(value: Any, default: bool) -> bool:
    """Coerce compatibility values while defaulting unknown or absent strings."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y"}:
            return True
        if lowered in {"0", "false", "no", "n"}:
            return False
        return default
    return bool(value)
