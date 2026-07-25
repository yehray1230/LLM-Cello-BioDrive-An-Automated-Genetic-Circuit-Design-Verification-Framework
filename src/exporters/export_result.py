from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExportResult:
    ok: bool
    format: str
    filename: str
    media_type: str
    content: str | bytes
    status: str
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    safety_status: str = "safe"
    safety_review_required: bool = False

