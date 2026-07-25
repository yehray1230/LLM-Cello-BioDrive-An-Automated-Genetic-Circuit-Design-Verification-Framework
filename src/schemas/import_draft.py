from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import re
from typing import Any
from uuid import uuid4

from schemas.design_ir import (
    BiologicalPart,
    DesignIR,
    DesignRevision,
    GeneticConstruct,
    ProvenanceRecord,
    RegulatoryInteraction,
)
from utils.scalar_values import optional_trimmed_text as _optional_text


SCHEMA_VERSION = "1.0"
EVIDENCE_LEVELS = {
    "explicit": 1.0,
    "derived": 0.75,
    "inferred": 0.5,
    "assumed": 0.25,
    "not_reported": 0.0,
    "unknown": 0.0,
}
UNKNOWN_VALUES = {"", "unknown", "not_reported", "not_applicable"}


@dataclass
class FieldEvidence:
    field_path: str
    status: str = "unknown"
    source_uri: str | None = None
    locator: str | None = None
    note: str = ""
    confidence: float | None = None


@dataclass
class DraftPart:
    id: str
    name: str
    part_type: str
    role: str = ""
    sequence: str | None = None
    host_compatibility: list[str] = field(default_factory=list)
    evidence: FieldEvidence | None = None


@dataclass
class DraftInteraction:
    source: str
    target: str
    interaction_type: str
    label: str = ""


@dataclass
class ImportDraft:
    draft_id: str
    name: str
    source_type: str
    source_uri: str | None = None
    citation: str = ""
    host_organism: str = "unknown"
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    logic_expression: str = ""
    validation_status: str = "unknown"
    validation_notes: str = ""
    parts: list[DraftPart] = field(default_factory=list)
    interactions: list[DraftInteraction] = field(default_factory=list)
    evidence: list[FieldEvidence] = field(default_factory=list)
    notes: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def empty(cls) -> ImportDraft:
        return cls(
            draft_id=f"external_{uuid4().hex[:12]}",
            name="",
            source_type="literature",
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


@dataclass
class ImportValidation:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    completeness: float = 0.0
    evidence_quality: float = 0.0
    applicable_sections: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)

    @property
    def can_import(self) -> bool:
        return not self.errors


def import_draft_from_json(value: str | bytes | dict[str, Any]) -> ImportDraft:
    if isinstance(value, bytes):
        value = value.decode("utf-8-sig")
    payload = json.loads(value) if isinstance(value, str) else value
    if not isinstance(payload, dict):
        raise ValueError("External design JSON must contain one object.")
    return ImportDraft(
        draft_id=_text(payload.get("draft_id")) or f"external_{uuid4().hex[:12]}",
        name=_text(payload.get("name")),
        source_type=_text(payload.get("source_type")) or "literature",
        source_uri=_optional_text(payload.get("source_uri")),
        citation=_text(payload.get("citation")),
        host_organism=_text(payload.get("host_organism")) or "unknown",
        inputs=_string_list(payload.get("inputs")),
        outputs=_string_list(payload.get("outputs")),
        logic_expression=_text(payload.get("logic_expression")),
        validation_status=_text(payload.get("validation_status")) or "unknown",
        validation_notes=_text(payload.get("validation_notes")),
        parts=[
            _draft_part_from_dict(item)
            for item in payload.get("parts", [])
            if isinstance(item, dict)
        ],
        interactions=[
            DraftInteraction(
                source=_text(item.get("source")),
                target=_text(item.get("target")),
                interaction_type=_text(item.get("interaction_type")),
                label=_text(item.get("label")),
            )
            for item in payload.get("interactions", [])
            if isinstance(item, dict)
        ],
        evidence=[
            _evidence_from_dict(item)
            for item in payload.get("evidence", [])
            if isinstance(item, dict)
        ],
        notes=_text(payload.get("notes")),
        created_at=_text(payload.get("created_at"))
        or datetime.now(timezone.utc).isoformat(),
        schema_version=_text(payload.get("schema_version")) or SCHEMA_VERSION,
    )


def validate_import_draft(draft: ImportDraft) -> ImportValidation:
    result = ImportValidation()
    required = {
        "name": draft.name,
        "source_type": draft.source_type,
        "inputs": draft.inputs,
        "outputs": draft.outputs,
        "logic_expression": draft.logic_expression,
        "validation_status": draft.validation_status,
    }
    result.missing_fields = [
        name for name, value in required.items() if _is_missing(value)
    ]
    if not draft.name.strip():
        result.errors.append("Design name is required.")
    if not draft.source_type.strip():
        result.errors.append("Source type is required.")
    if not draft.inputs:
        result.errors.append("At least one input is required.")
    if not draft.outputs:
        result.errors.append("At least one output is required.")
    if not draft.logic_expression.strip():
        result.errors.append("A Boolean expression or logic description is required.")
    if not draft.source_uri and not draft.citation.strip():
        result.warnings.append(
            "No source URI or citation was provided; the design will be difficult to audit."
        )
    if draft.host_organism.lower() in UNKNOWN_VALUES:
        result.warnings.append("Host organism is unknown or was not reported.")
    if not draft.parts:
        result.warnings.append(
            "No biological parts were entered; evaluation is limited to logic-level evidence."
        )

    part_ids = [part.id for part in draft.parts]
    duplicates = sorted({part_id for part_id in part_ids if part_ids.count(part_id) > 1})
    if duplicates:
        result.errors.append(f"Part IDs must be unique: {', '.join(duplicates)}.")

    known_parts = set(part_ids)
    for interaction in draft.interactions:
        if interaction.source not in known_parts or interaction.target not in known_parts:
            result.warnings.append(
                f"Interaction {interaction.source} -> {interaction.target} references an unknown part."
            )

    result.completeness = _completeness_score(draft)
    result.evidence_quality = _evidence_quality_score(draft)
    result.applicable_sections = ["logic"]
    if draft.parts:
        result.applicable_sections.extend(["parts", "regulatory_structure"])
    if any(part.sequence for part in draft.parts):
        result.applicable_sections.append("sequence")
    if draft.host_organism.lower() not in UNKNOWN_VALUES:
        result.applicable_sections.append("host_context")
    if draft.validation_status not in UNKNOWN_VALUES:
        result.applicable_sections.append("experimental_evidence")
    return result


def import_draft_to_design_ir(draft: ImportDraft) -> DesignIR:
    validation = validate_import_draft(draft)
    if not validation.can_import:
        raise ValueError("Cannot import draft: " + " ".join(validation.errors))

    provenance_id = f"provenance_{draft.draft_id}"
    parts = [
        BiologicalPart(
            id=part.id,
            name=part.name,
            part_type=part.part_type,
            role=part.role or f"Imported {part.part_type}",
            sequence=_normalize_sequence(part.sequence),
            source=draft.source_type,
            confidence=part.evidence.status if part.evidence else "unknown",
            host_compatibility=part.host_compatibility
            or (
                []
                if draft.host_organism.lower() in UNKNOWN_VALUES
                else [draft.host_organism]
            ),
            rationale=(
                part.evidence.note
                if part.evidence and part.evidence.note
                else "Imported from an external design record."
            ),
            provenance_ids=[provenance_id],
        )
        for part in draft.parts
    ]
    constructs = []
    if parts:
        constructs.append(
            GeneticConstruct(
                id=f"construct_{draft.draft_id}",
                name=f"{draft.name} imported construct",
                parts=[part.id for part in parts],
                validation_status={
                    "part_assignment": "externally_reported",
                    "sequence": _sequence_coverage(parts),
                    "backbone": "not_reported",
                    "assembly": "not_checked",
                },
            )
        )
    warnings = list(validation.warnings)
    warnings.extend(
        f"Missing import field: {field_name}."
        for field_name in validation.missing_fields
    )
    return DesignIR(
        design_id=draft.draft_id,
        name=draft.name,
        inputs=list(draft.inputs),
        outputs=list(draft.outputs),
        logic_expression=draft.logic_expression,
        parts=parts,
        interactions=[
            RegulatoryInteraction(
                source=item.source,
                target=item.target,
                interaction_type=item.interaction_type,
                label=item.label,
            )
            for item in draft.interactions
        ],
        constructs=constructs,
        validation_status={
            "logic": "externally_reported",
            "regulatory_model": "reported" if parts else "missing",
            "part_mapping": "externally_reported" if parts else "missing",
            "sequences": _sequence_coverage(parts),
            "assembly_ready": "unknown",
            "experimental_validation": draft.validation_status,
            "import_completeness": f"{validation.completeness:.3f}",
            "evidence_quality": f"{validation.evidence_quality:.3f}",
        },
        warnings=warnings,
        provenance=[
            ProvenanceRecord(
                id=provenance_id,
                source_type=draft.source_type,
                source_uri=draft.source_uri,
                generated_by="external_design_import_v1",
                generated_at=draft.created_at,
                metadata={
                    "citation": draft.citation,
                    "validation_notes": draft.validation_notes,
                    "notes": draft.notes,
                    "schema_version": draft.schema_version,
                    "field_evidence": [asdict(item) for item in draft.evidence],
                    "applicable_sections": validation.applicable_sections,
                },
            )
        ],
        revision=DesignRevision(
            revision_id=f"{draft.draft_id}_revision_1",
            created_at=draft.created_at,
            created_by="external_import",
            change_type="external_import",
            summary="Imported external genetic-circuit design.",
        ),
    )


def _draft_part_from_dict(payload: dict[str, Any]) -> DraftPart:
    evidence = payload.get("evidence")
    return DraftPart(
        id=_text(payload.get("id")),
        name=_text(payload.get("name")),
        part_type=_text(payload.get("part_type")),
        role=_text(payload.get("role")),
        sequence=_optional_text(payload.get("sequence")),
        host_compatibility=_string_list(payload.get("host_compatibility")),
        evidence=_evidence_from_dict(evidence) if isinstance(evidence, dict) else None,
    )


def _evidence_from_dict(payload: dict[str, Any]) -> FieldEvidence:
    confidence = payload.get("confidence")
    try:
        confidence_value = None if confidence is None else float(confidence)
    except (TypeError, ValueError):
        confidence_value = None
    return FieldEvidence(
        field_path=_text(payload.get("field_path")),
        status=_text(payload.get("status")) or "unknown",
        source_uri=_optional_text(payload.get("source_uri")),
        locator=_optional_text(payload.get("locator")),
        note=_text(payload.get("note")),
        confidence=confidence_value,
    )


def _completeness_score(draft: ImportDraft) -> float:
    fields = [
        draft.name,
        draft.source_type,
        draft.source_uri or draft.citation,
        draft.host_organism,
        draft.inputs,
        draft.outputs,
        draft.logic_expression,
        draft.validation_status,
        draft.validation_notes,
        draft.parts,
    ]
    return sum(not _is_missing(value) for value in fields) / len(fields)


def _evidence_quality_score(draft: ImportDraft) -> float:
    records = list(draft.evidence)
    records.extend(part.evidence for part in draft.parts if part.evidence)
    if not records:
        return 0.0
    scores = []
    for record in records:
        base = EVIDENCE_LEVELS.get(record.status, 0.0)
        if record.confidence is not None:
            base = (base + min(1.0, max(0.0, record.confidence))) / 2
        scores.append(base)
    return sum(scores) / len(scores)


def _sequence_coverage(parts: list[BiologicalPart]) -> str:
    if not parts:
        return "missing"
    count = sum(bool(part.sequence) for part in parts)
    if count == len(parts):
        return "complete"
    return "partial" if count else "missing"


def _normalize_sequence(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", "", value).upper()
    return normalized or None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        return []
    return [text for item in values if (text := _text(item))]


def _is_missing(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in UNKNOWN_VALUES
    return not value


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()
