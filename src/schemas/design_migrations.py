from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from schemas.design_ir_v2 import (
    AttributedValue,
    BiologicalContext,
    BiologicalPartV2,
    ConstructPart,
    ConstructV2,
    DesignAssumption,
    DesignIRV2,
    DesignRevisionV2,
    DesignSpecification,
    FieldProvenance,
    PART_EVIDENCE_LEVELS,
    PlasmidV2,
    ProvenanceRecordV2,
    RegulatoryInteractionV2,
    design_ir_v2_from_dict,
)
from utils.scalar_values import optional_float as _optional_float
from utils.scalar_values import optional_trimmed_text as _optional_string


@dataclass
class MigrationResult:
    design: DesignIRV2
    source_version: str
    target_version: str = "2.0"
    warnings: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    migrated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "design": self.design.to_dict(),
            "source_version": self.source_version,
            "target_version": self.target_version,
            "warnings": list(self.warnings),
            "assumptions": list(self.assumptions),
            "migrated_at": self.migrated_at,
        }


def migrate_design_payload_to_v2(payload: dict[str, Any]) -> MigrationResult:
    version = str(payload.get("schema_version") or "1.0")
    if version.startswith("2"):
        design = design_ir_v2_from_dict(payload)
        errors = design.validate()
        if errors:
            raise ValueError("Invalid DesignIR v2 payload: " + " ".join(errors))
        return MigrationResult(design=design, source_version=version)
    return migrate_design_ir_v1_to_v2(payload)


def migrate_design_ir_v1_to_v2(payload: dict[str, Any]) -> MigrationResult:
    source_version = str(payload.get("schema_version") or "1.0")
    design_id = str(payload.get("design_id") or "candidate")
    provenance = [
        ProvenanceRecordV2(
            id=str(item.get("id") or f"source_{index}"),
            source_type=str(item.get("source_type") or "unknown"),
            source_uri=_optional_string(item.get("source_uri")),
            source_version=_optional_string(item.get("source_version")),
            generated_by=_optional_string(item.get("generated_by")),
            generated_at=_optional_string(item.get("generated_at")),
            artifact_manifest_path=_optional_string(
                item.get("artifact_manifest_path")
            ),
            license_expression=_optional_string(item.get("license_expression")),
            rights_uri=_optional_string(item.get("rights_uri")),
            license_status=str(item.get("license_status") or "unknown"),
            attribution_required=bool(item.get("attribution_required", False)),
            permitted_uses=list(item.get("permitted_uses") or []),
            prohibited_uses=list(item.get("prohibited_uses") or []),
            metadata=dict(item.get("metadata") or {}),
        )
        for index, item in enumerate(_dict_list(payload.get("provenance")), start=1)
    ]
    parts = [
        BiologicalPartV2(
            id=str(item.get("id") or ""),
            name=str(item.get("name") or ""),
            part_type=str(item.get("part_type") or "unknown"),
            role=str(item.get("role") or ""),
            sequence=_optional_string(item.get("sequence")),
            source=str(item.get("source") or "conceptual"),
            evidence_level=_part_evidence_level(item),
            host_compatibility=list(item.get("host_compatibility") or []),
            provenance_ids=list(item.get("provenance_ids") or []),
            metadata={
                key: value
                for key, value in item.items()
                if key
                not in {
                    "id",
                    "name",
                    "part_type",
                    "role",
                    "sequence",
                    "source",
                    "evidence_level",
                    "sequence_status",
                    "host_compatibility",
                    "provenance_ids",
                }
            },
        )
        for item in _dict_list(payload.get("parts"))
    ]
    constructs: list[ConstructV2] = []
    plasmids: list[PlasmidV2] = []
    assumptions: list[DesignAssumption] = []
    assumption_messages: list[str] = []
    for construct_index, item in enumerate(
        _dict_list(payload.get("constructs")), start=1
    ):
        construct_id = str(item.get("id") or f"construct_{construct_index}")
        constructs.append(
            ConstructV2(
                id=construct_id,
                name=str(item.get("name") or construct_id),
                part_instances=[
                    ConstructPart(
                        instance_id=f"{construct_id}_part_{part_index}",
                        part_id=str(part_id),
                        order=part_index,
                    )
                    for part_index, part_id in enumerate(
                        item.get("parts") or [], start=1
                    )
                ],
                topology=str(item.get("topology") or "linear"),
                assembly_method=AttributedValue(
                    value=item.get("assembly_method"),
                    status="explicit" if item.get("assembly_method") else "unknown",
                ),
                validation_status=dict(item.get("validation_status") or {}),
            )
        )
        backbone = item.get("backbone")
        if backbone:
            plasmid_id = f"plasmid_{construct_id}"
            plasmids.append(
                PlasmidV2(
                    id=plasmid_id,
                    name=f"{item.get('name') or construct_id} plasmid",
                    construct_ids=[construct_id],
                    backbone=AttributedValue(value=backbone, status="explicit"),
                )
            )
            for field_name in (
                "origin_of_replication",
                "copy_number",
                "selection_marker",
            ):
                assumption_id = f"{plasmid_id}_{field_name}_unknown"
                assumptions.append(
                    DesignAssumption(
                        assumption_id=assumption_id,
                        field_path=f"plasmids.{plasmid_id}.{field_name}",
                        rationale="The v1 design did not define this plasmid property.",
                    )
                )
                assumption_messages.append(
                    f"{plasmid_id}.{field_name} remains unknown."
                )

    host_values = sorted(
        {
            str(host)
            for part in parts
            for host in part.host_compatibility
            if str(host).strip()
        }
    )
    host_status = "derived" if host_values else "unknown"
    host_value = host_values[0] if len(host_values) == 1 else host_values or None
    if not host_values:
        assumptions.append(
            DesignAssumption(
                assumption_id=f"{design_id}_host_unknown",
                field_path="biological_context.host_organism",
                rationale="No host compatibility information was present in DesignIR v1.",
            )
        )
        assumption_messages.append("Host organism remains unknown.")

    source_id = provenance[0].id if provenance else None
    field_provenance = [
        FieldProvenance(
            field_path="specification.logic_expression",
            status="explicit" if payload.get("logic_expression") else "unknown",
            source_id=source_id,
        ),
        FieldProvenance(
            field_path="biological_context.host_organism",
            status=host_status,
            source_id=source_id,
            note="Derived from part host_compatibility values during v1 migration.",
        ),
    ]
    for source in provenance:
        raw_evidence = source.metadata.get("field_evidence")
        for item in _dict_list(raw_evidence):
            field_path = str(item.get("field_path") or "").strip()
            if not field_path:
                continue
            field_provenance.append(
                FieldProvenance(
                    field_path=field_path,
                    status=str(item.get("status") or "unknown"),
                    source_id=source.id,
                    locator=_optional_string(item.get("locator")),
                    confidence=_optional_float(item.get("confidence")),
                    note=str(item.get("note") or ""),
                )
            )
    revision_payload = (
        dict(payload.get("revision")) if isinstance(payload.get("revision"), dict) else {}
    )
    design = DesignIRV2(
        design_id=design_id,
        name=str(payload.get("name") or "Genetic circuit design"),
        specification=DesignSpecification(
            inputs=list(payload.get("inputs") or []),
            outputs=list(payload.get("outputs") or []),
            logic_expression=str(payload.get("logic_expression") or ""),
        ),
        biological_context=BiologicalContext(
            host_organism=AttributedValue(
                value=host_value,
                status=host_status,
                source_id=source_id,
            )
        ),
        parts=parts,
        interactions=[
            RegulatoryInteractionV2(
                source=str(item.get("source") or ""),
                target=str(item.get("target") or ""),
                interaction_type=str(item.get("interaction_type") or ""),
                label=str(item.get("label") or ""),
            )
            for item in _dict_list(payload.get("interactions"))
        ],
        constructs=constructs,
        plasmids=plasmids,
        provenance=provenance,
        field_provenance=field_provenance,
        assumptions=assumptions,
        validation_status=dict(payload.get("validation_status") or {}),
        warnings=list(payload.get("warnings") or []),
        extensions={
            "design_ir_v1": {
                "assignments": list(payload.get("assignments") or []),
            }
        },
        revision=DesignRevisionV2(
            revision_id=str(
                revision_payload.get("revision_id") or f"{design_id}_revision_1"
            ),
            parent_revision_id=_optional_string(
                revision_payload.get("parent_revision_id")
            ),
            revision_number=int(revision_payload.get("revision_number") or 1),
            created_at=_optional_string(revision_payload.get("created_at")),
            created_by=str(revision_payload.get("created_by") or "migration"),
            change_type=str(revision_payload.get("change_type") or "migration"),
            summary=str(
                revision_payload.get("summary") or "Migrated DesignIR v1 to v2."
            ),
            changes=list(revision_payload.get("changes") or []),
        ),
    )
    errors = design.validate()
    if errors:
        raise ValueError("Migrated DesignIR v2 is invalid: " + " ".join(errors))
    warnings = []
    if not plasmids:
        warnings.append(
            "No plasmid was created because the v1 constructs did not define a backbone."
        )
    return MigrationResult(
        design=design,
        source_version=source_version,
        warnings=warnings,
        assumptions=assumption_messages,
    )


def design_ir_v2_to_v1_payload(payload: dict[str, Any]) -> dict[str, Any]:
    design = design_ir_v2_from_dict(payload)
    return {
        "design_id": design.design_id,
        "name": design.name,
        "inputs": list(design.specification.inputs),
        "outputs": list(design.specification.outputs),
        "logic_expression": design.specification.logic_expression,
        "parts": [
            {
                "id": part.id,
                "name": part.name,
                "part_type": part.part_type,
                "role": part.role,
                "sequence": part.sequence,
                "source": part.source,
                "confidence": str(part.metadata.get("confidence") or "unknown"),
                "host_compatibility": list(part.host_compatibility),
                "upstream": list(part.metadata.get("upstream") or []),
                "downstream": list(part.metadata.get("downstream") or []),
                "rationale": str(part.metadata.get("rationale") or ""),
                "sequence_format": str(
                    part.metadata.get("sequence_format") or "DNA"
                ),
                "provenance_ids": list(part.provenance_ids),
                "assignment": part.metadata.get("assignment"),
            }
            for part in design.parts
        ],
        "interactions": [
            {
                "source": item.source,
                "target": item.target,
                "interaction_type": item.interaction_type,
                "label": item.label,
            }
            for item in design.interactions
        ],
        "constructs": [
            {
                "id": construct.id,
                "name": construct.name,
                "parts": [
                    item.part_id
                    for item in sorted(
                        construct.part_instances, key=lambda value: value.order
                    )
                ],
                "topology": construct.topology,
                "backbone": _backbone_for_construct(design, construct.id),
                "assembly_method": construct.assembly_method.value,
                "validation_status": dict(construct.validation_status),
            }
            for construct in design.constructs
        ],
        "validation_status": dict(design.validation_status),
        "warnings": list(design.warnings),
        "provenance": [
            {
                "id": item.id,
                "source_type": item.source_type,
                "source_uri": item.source_uri,
                "source_version": item.source_version,
                "generated_by": item.generated_by,
                "generated_at": item.generated_at,
                "artifact_manifest_path": item.artifact_manifest_path,
                "license_expression": item.license_expression,
                "rights_uri": item.rights_uri,
                "license_status": item.license_status,
                "attribution_required": item.attribution_required,
                "permitted_uses": list(item.permitted_uses),
                "prohibited_uses": list(item.prohibited_uses),
                "metadata": dict(item.metadata),
            }
            for item in design.provenance
        ],
        "assignments": list(
            design.extensions.get("design_ir_v1", {}).get("assignments", [])
        ),
        "revision": {
            "revision_id": design.revision.revision_id,
            "parent_revision_id": design.revision.parent_revision_id,
            "revision_number": design.revision.revision_number,
            "created_at": design.revision.created_at,
            "created_by": design.revision.created_by,
            "change_type": design.revision.change_type,
            "summary": design.revision.summary,
            "changes": list(design.revision.changes),
        },
    }


def _backbone_for_construct(design: DesignIRV2, construct_id: str) -> Any:
    for plasmid in design.plasmids:
        if construct_id in plasmid.construct_ids:
            return plasmid.backbone.value
    return None


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _part_evidence_level(item: dict[str, Any]) -> str:
    candidates = (
        item.get("evidence_level"),
        item.get("sequence_status"),
        item.get("confidence"),
    )
    for value in candidates:
        normalized = str(value or "").strip().lower()
        if normalized in PART_EVIDENCE_LEVELS:
            return normalized
    return "unknown"
