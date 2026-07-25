from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from utils.scalar_values import optional_trimmed_text as _optional_string


DESIGN_IR_V2_SCHEMA_VERSION = "2.0"
EVIDENCE_STATUSES = {
    "explicit",
    "derived",
    "inferred",
    "assumed",
    "defaulted",
    "unknown",
}
PART_EVIDENCE_LEVELS = {
    "unknown",
    "conceptual",
    "illustrative",
    "inferred",
    "database_derived",
    "literature_supported",
    "experimentally_characterized",
    "user_verified",
}


@dataclass
class AttributedValue:
    value: Any = None
    status: str = "unknown"
    source_id: str | None = None
    locator: str | None = None
    confidence: float | None = None
    note: str = ""


@dataclass
class FieldProvenance:
    field_path: str
    status: str = "unknown"
    source_id: str | None = None
    locator: str | None = None
    confidence: float | None = None
    note: str = ""


@dataclass
class DesignAssumption:
    assumption_id: str
    field_path: str
    value: Any = None
    rationale: str = ""
    status: str = "active"
    created_by: str = "migration"


@dataclass
class DesignSpecification:
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    logic_expression: str = ""
    truth_table: list[dict[str, Any]] = field(default_factory=list)
    user_intent: str | None = None


@dataclass
class BiologicalContext:
    host_organism: AttributedValue = field(default_factory=AttributedValue)
    chassis: AttributedValue = field(default_factory=AttributedValue)
    growth_conditions: dict[str, AttributedValue] = field(default_factory=dict)


@dataclass
class BiologicalPartV2:
    id: str
    name: str
    part_type: str
    role: str
    sequence: str | None = None
    source: str = "conceptual"
    evidence_level: str = "unknown"
    host_compatibility: list[str] = field(default_factory=list)
    provenance_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RegulatoryInteractionV2:
    source: str
    target: str
    interaction_type: str
    label: str = ""


@dataclass
class ConstructPart:
    instance_id: str
    part_id: str
    orientation: str = "forward"
    order: int = 0


@dataclass
class ConstructV2:
    id: str
    name: str
    part_instances: list[ConstructPart] = field(default_factory=list)
    topology: str = "linear"
    assembly_method: AttributedValue = field(default_factory=AttributedValue)
    validation_status: dict[str, str] = field(default_factory=dict)


@dataclass
class PlasmidV2:
    id: str
    name: str
    construct_ids: list[str] = field(default_factory=list)
    backbone: AttributedValue = field(default_factory=AttributedValue)
    origin_of_replication: AttributedValue = field(default_factory=AttributedValue)
    copy_number: AttributedValue = field(default_factory=AttributedValue)
    selection_marker: AttributedValue = field(default_factory=AttributedValue)
    topology: str = "circular"


@dataclass
class ProvenanceRecordV2:
    id: str
    source_type: str
    source_uri: str | None = None
    source_version: str | None = None
    generated_by: str | None = None
    generated_at: str | None = None
    artifact_manifest_path: str | None = None
    license_expression: str | None = None
    rights_uri: str | None = None
    license_status: str = "unknown"
    attribution_required: bool = False
    permitted_uses: list[str] = field(default_factory=list)
    prohibited_uses: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DesignRevisionV2:
    revision_id: str
    parent_revision_id: str | None = None
    revision_number: int = 1
    created_at: str | None = None
    created_by: str = "system"
    change_type: str = "generated"
    summary: str = "Initial design generation"
    changes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DesignIRV2:
    design_id: str
    name: str
    specification: DesignSpecification
    parts: list[BiologicalPartV2]
    interactions: list[RegulatoryInteractionV2]
    constructs: list[ConstructV2]
    biological_context: BiologicalContext = field(default_factory=BiologicalContext)
    plasmids: list[PlasmidV2] = field(default_factory=list)
    provenance: list[ProvenanceRecordV2] = field(default_factory=list)
    field_provenance: list[FieldProvenance] = field(default_factory=list)
    assumptions: list[DesignAssumption] = field(default_factory=list)
    validation_status: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    extensions: dict[str, Any] = field(default_factory=dict)
    revision: DesignRevisionV2 = field(
        default_factory=lambda: DesignRevisionV2(revision_id="revision_1")
    )
    is_archived: bool = False
    is_deleted: bool = False
    is_pinned: bool = False
    schema_version: str = DESIGN_IR_V2_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.schema_version != DESIGN_IR_V2_SCHEMA_VERSION:
            errors.append(
                f"Unsupported DesignIR schema version: {self.schema_version}."
            )
        if not self.design_id.strip():
            errors.append("design_id is required.")
        if not self.name.strip():
            errors.append("name is required.")
        errors.extend(_duplicate_errors("part", [part.id for part in self.parts]))
        errors.extend(
            _duplicate_errors("construct", [construct.id for construct in self.constructs])
        )
        errors.extend(
            _duplicate_errors("plasmid", [plasmid.id for plasmid in self.plasmids])
        )

        part_ids = {part.id for part in self.parts}
        construct_ids = {construct.id for construct in self.constructs}
        provenance_ids = {item.id for item in self.provenance}
        for part in self.parts:
            if part.evidence_level not in PART_EVIDENCE_LEVELS:
                errors.append(
                    f"Part {part.id} has invalid evidence level "
                    f"{part.evidence_level}."
                )
        for construct in self.constructs:
            for instance in construct.part_instances:
                if instance.part_id not in part_ids:
                    errors.append(
                        f"Construct {construct.id} references unknown part "
                        f"{instance.part_id}."
                    )
        for plasmid in self.plasmids:
            for construct_id in plasmid.construct_ids:
                if construct_id not in construct_ids:
                    errors.append(
                        f"Plasmid {plasmid.id} references unknown construct "
                        f"{construct_id}."
                    )
        for item in self.field_provenance:
            if item.status not in EVIDENCE_STATUSES:
                errors.append(
                    f"Field provenance {item.field_path} has invalid status "
                    f"{item.status}."
                )
            if item.source_id and item.source_id not in provenance_ids:
                errors.append(
                    f"Field provenance {item.field_path} references unknown source "
                    f"{item.source_id}."
                )
        return errors


def design_ir_v2_from_dict(payload: dict[str, Any]) -> DesignIRV2:
    context_payload = _dict(payload.get("biological_context"))
    growth_conditions = {
        str(key): _attributed(value)
        for key, value in _dict(context_payload.get("growth_conditions")).items()
    }
    revision_payload = _dict(payload.get("revision"))
    return DesignIRV2(
        design_id=str(payload.get("design_id") or ""),
        name=str(payload.get("name") or ""),
        specification=DesignSpecification(**_dict(payload.get("specification"))),
        biological_context=BiologicalContext(
            host_organism=_attributed(context_payload.get("host_organism")),
            chassis=_attributed(context_payload.get("chassis")),
            growth_conditions=growth_conditions,
        ),
        parts=[
            BiologicalPartV2(**item)
            for item in _dict_list(payload.get("parts"))
        ],
        interactions=[
            RegulatoryInteractionV2(**item)
            for item in _dict_list(payload.get("interactions"))
        ],
        constructs=[
            ConstructV2(
                id=str(item.get("id") or ""),
                name=str(item.get("name") or ""),
                part_instances=[
                    ConstructPart(**part)
                    for part in _dict_list(item.get("part_instances"))
                ],
                topology=str(item.get("topology") or "linear"),
                assembly_method=_attributed(item.get("assembly_method")),
                validation_status=dict(item.get("validation_status") or {}),
            )
            for item in _dict_list(payload.get("constructs"))
        ],
        plasmids=[
            PlasmidV2(
                id=str(item.get("id") or ""),
                name=str(item.get("name") or ""),
                construct_ids=list(item.get("construct_ids") or []),
                backbone=_attributed(item.get("backbone")),
                origin_of_replication=_attributed(item.get("origin_of_replication")),
                copy_number=_attributed(item.get("copy_number")),
                selection_marker=_attributed(item.get("selection_marker")),
                topology=str(item.get("topology") or "circular"),
            )
            for item in _dict_list(payload.get("plasmids"))
        ],
        provenance=[
            ProvenanceRecordV2(**item)
            for item in _dict_list(payload.get("provenance"))
        ],
        field_provenance=[
            FieldProvenance(**item)
            for item in _dict_list(payload.get("field_provenance"))
        ],
        assumptions=[
            DesignAssumption(**item)
            for item in _dict_list(payload.get("assumptions"))
        ],
        validation_status=dict(payload.get("validation_status") or {}),
        warnings=list(payload.get("warnings") or []),
        extensions=dict(payload.get("extensions") or {}),
        revision=DesignRevisionV2(
            revision_id=str(revision_payload.get("revision_id") or "revision_1"),
            parent_revision_id=_optional_string(
                revision_payload.get("parent_revision_id")
            ),
            revision_number=int(revision_payload.get("revision_number") or 1),
            created_at=_optional_string(revision_payload.get("created_at")),
            created_by=str(revision_payload.get("created_by") or "system"),
            change_type=str(revision_payload.get("change_type") or "generated"),
            summary=str(
                revision_payload.get("summary") or "Initial design generation"
            ),
            changes=list(revision_payload.get("changes") or []),
        ),
        is_archived=bool(payload.get("is_archived", False)),
        is_deleted=bool(payload.get("is_deleted", False)),
        is_pinned=bool(payload.get("is_pinned", False)),
        schema_version=str(
            payload.get("schema_version") or DESIGN_IR_V2_SCHEMA_VERSION
        ),
    )


def _attributed(value: Any) -> AttributedValue:
    if isinstance(value, dict):
        return AttributedValue(**value)
    if value is None:
        return AttributedValue()
    return AttributedValue(value=value, status="explicit")


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _duplicate_errors(label: str, values: list[str]) -> list[str]:
    duplicates = sorted({value for value in values if values.count(value) > 1})
    return [f"Duplicate {label} ID: {value}." for value in duplicates]
