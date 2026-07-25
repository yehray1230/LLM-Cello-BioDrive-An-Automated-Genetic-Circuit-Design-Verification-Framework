from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_json_sha256(payload: Any) -> str:
    """Return SHA-256 for the compact, key-sorted UTF-8 JSON representation."""
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
