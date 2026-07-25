from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from utils.scalar_values import optional_float as _optional_float
from utils.scalar_values import optional_trimmed_text as _optional_string


DEFAULT_DEMO_LIBRARY = (
    Path(__file__).resolve().parent.parent
    / "part_libraries"
    / "demo_cello_v1.json"
)


@dataclass(frozen=True)
class LibraryPart:
    id: str
    name: str
    part_type: str
    sequence: str | None
    sequence_status: str
    host_compatibility: tuple[str, ...]
    roles: tuple[str, ...] = ()
    compatible_gate_types: tuple[str, ...] = ()
    compatible_regulators: tuple[str, ...] = ()
    orthogonality_group: str | None = None
    burden_score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PartLibrary:
    library_id: str
    version: str
    name: str
    description: str
    evidence_level: str
    host_organisms: tuple[str, ...]
    parts: tuple[LibraryPart, ...]
    source_path: str

    @classmethod
    def from_json(cls, path: str | Path) -> PartLibrary:
        selected_path = Path(path).resolve()
        payload = json.loads(selected_path.read_text(encoding="utf-8"))
        raw_parts = payload.get("parts", [])
        if not isinstance(raw_parts, list):
            raise ValueError("Part library 'parts' must be a list.")
        parts = []
        for raw in raw_parts:
            if not isinstance(raw, dict):
                continue
            part_id = str(raw.get("id", "")).strip()
            name = str(raw.get("name", part_id)).strip()
            part_type = str(raw.get("part_type", "")).strip()
            if not part_id or not part_type:
                raise ValueError("Every library part requires id and part_type.")
            known = {
                "id", "name", "part_type", "sequence", "sequence_status",
                "host_compatibility", "roles", "compatible_gate_types",
                "compatible_regulators", "orthogonality_group", "burden_score",
            }
            parts.append(
                LibraryPart(
                    id=part_id,
                    name=name,
                    part_type=part_type,
                    sequence=_normalize_sequence(raw.get("sequence")),
                    sequence_status=str(raw.get("sequence_status", "unknown")),
                    host_compatibility=tuple(str(item) for item in raw.get("host_compatibility", [])),
                    roles=tuple(str(item) for item in raw.get("roles", [])),
                    compatible_gate_types=tuple(
                        str(item).upper() for item in raw.get("compatible_gate_types", [])
                    ),
                    compatible_regulators=tuple(
                        str(item) for item in raw.get("compatible_regulators", [])
                    ),
                    orthogonality_group=_optional_string(raw.get("orthogonality_group")),
                    burden_score=_optional_float(raw.get("burden_score")),
                    metadata={key: value for key, value in raw.items() if key not in known},
                )
            )
        return cls(
            library_id=str(payload.get("library_id", selected_path.stem)),
            version=str(payload.get("version", "0.0.0")),
            name=str(payload.get("name", selected_path.stem)),
            description=str(payload.get("description", "")),
            evidence_level=str(payload.get("evidence_level", "unknown")),
            host_organisms=tuple(str(item) for item in payload.get("host_organisms", [])),
            parts=tuple(parts),
            source_path=str(selected_path),
        )

    @classmethod
    def demo(cls) -> PartLibrary:
        return cls.from_json(DEFAULT_DEMO_LIBRARY)

    def get(self, part_id: str) -> LibraryPart | None:
        return next((part for part in self.parts if part.id == part_id), None)

    def compatible_parts(
        self,
        *,
        part_type: str,
        host_organism: str | None = None,
        gate_type: str | None = None,
    ) -> list[LibraryPart]:
        normalized_type = part_type.lower()
        normalized_host = (host_organism or "").lower()
        normalized_gate = (gate_type or "").upper()
        return [
            part
            for part in self.parts
            if part.part_type.lower() == normalized_type
            and (
                not normalized_host
                or not part.host_compatibility
                or normalized_host in {host.lower() for host in part.host_compatibility}
            )
            and (
                not normalized_gate
                or not part.compatible_gate_types
                or normalized_gate in part.compatible_gate_types
            )
        ]


def _normalize_sequence(value: Any) -> str | None:
    if value is None:
        return None
    sequence = "".join(str(value).split()).upper()
    return sequence or None
