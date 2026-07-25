from __future__ import annotations

from typing import Any


def optional_trimmed_text(value: Any) -> str | None:
    """Convert a value to stripped text, using None for absent or blank text."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def optional_float(value: Any) -> float | None:
    """Convert a value to float, using None when permissive conversion fails."""
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
