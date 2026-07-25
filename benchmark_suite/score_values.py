from __future__ import annotations

from typing import Any


def clamp01(value: Any) -> float:
    """Convert a benchmark score to float and clamp it to [0.0, 1.0]."""
    return max(0.0, min(1.0, float(value)))
