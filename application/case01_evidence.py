from __future__ import annotations

from typing import Any

from schemas.evidence_governance import (
    ClaimEvidenceLink,
    EvidenceRecord,
    build_evidence_manifest,
)
from schemas.simulation import canonical_payload_hash
from utils.scalar_values import optional_trimmed_text as _optional_text


CASE01_INTENDED_USE = "public_evidence_review"
CASE01_PROJECT_LICENSE = "Apache-2.0"


def build_case01_evidence_manifest(packet: dict[str, Any]) -> dict[str, Any]:
    fixed_demo = dict(packet.get("fixed_demo") or {})
    research = dict(packet.get("research_run") or {})
    benchmark = dict(packet.get("benchmark_run") or {})
    sequence = dict(packet.get("sequence_analysis") or {})
    records = [
        EvidenceRecord(
            evidence_id="case01_task_contract",
            evidence_type="synthetic_task_contract",
            source_uri="benchmark_suite/task_sets/exp003_design_tasks_v1.json",
            source_version=str(fixed_demo.get("task_set_version") or ""),
            content_hash=_optional_text(fixed_demo.get("task_set_content_hash")),
            license_expression=(
                _optional_text(fixed_demo.get("task_set_license"))
                or CASE01_PROJECT_LICENSE
            ),
            rights_uri="LICENSE",
            license_status="attribution_required",
            attribution_required=True,
            permitted_uses=["internal_evaluation", "public_evidence_review"],
            biological_context={"chassis": fixed_demo.get("chassis")},
            method="curated_project_fixture",
            scope="Computational task definition only.",
            metadata={"claim_eligible": True},
        ),
        EvidenceRecord(
            evidence_id="case01_simulation_result",
            evidence_type="computational_simulation",
            source_uri="generated:research_run",
            source_version=_optional_text(research.get("simulation_model")),
            content_hash=_optional_text(research.get("result_hash")),
            license_expression=CASE01_PROJECT_LICENSE,
            rights_uri="LICENSE",
            license_status="attribution_required",
            attribution_required=True,
            permitted_uses=["internal_evaluation", "public_evidence_review"],
            biological_context={"chassis": fixed_demo.get("chassis")},
            method="resource_aware_regulatory_ode",
            scope="Relative computational screening under named assumptions.",
            metadata={"claim_eligible": True},
        ),
        EvidenceRecord(
            evidence_id="case01_benchmark_result",
            evidence_type="synthetic_benchmark",
            source_uri="benchmark_suite/datasets/research_smoke_v1.json",
            source_version=_optional_text(benchmark.get("dataset_version")),
            content_hash=_stable_benchmark_evidence_hash(benchmark),
            license_expression=(
                _optional_text(benchmark.get("dataset_license"))
                or CASE01_PROJECT_LICENSE
            ),
            rights_uri="LICENSE",
            license_status="attribution_required",
            attribution_required=True,
            permitted_uses=["internal_evaluation", "public_evidence_review"],
            method="deterministic_project_benchmark",
            scope="Software-contract evidence; not measured circuit performance.",
            metadata={"claim_eligible": True},
        ),
        EvidenceRecord(
            evidence_id="case01_sequence_checks",
            evidence_type="illustrative_sequence_check",
            source_uri="generated:sequence_analysis",
            content_hash=canonical_payload_hash(sequence) if sequence else None,
            license_expression=CASE01_PROJECT_LICENSE,
            rights_uri="LICENSE",
            license_status="attribution_required",
            attribution_required=True,
            permitted_uses=["internal_evaluation", "public_evidence_review"],
            biological_context={"chassis": fixed_demo.get("chassis")},
            method="deterministic_sequence_analysis",
            scope="Illustrative demo sequences only.",
            metadata={"claim_eligible": False},
            notes=["Passing checks do not establish empirical part characterization."],
        ),
        EvidenceRecord(
            evidence_id="case01_external_cello_mapping",
            evidence_type="external_tool_mapping",
            source_uri=None,
            license_status="unknown",
            availability="missing",
            scope="External Cello mapping evidence was not produced for this snapshot.",
        ),
        EvidenceRecord(
            evidence_id="case01_experimental_validation",
            evidence_type="experimental_measurement",
            source_uri=None,
            license_status="unknown",
            availability="missing",
            scope="No wet-lab measurement is included in this snapshot.",
        ),
    ]
    links = [
        ClaimEvidenceLink(
            claim_id="computationally_consistent",
            evidence_ids=[
                "case01_task_contract",
                "case01_simulation_result",
                "case01_benchmark_result",
            ],
            note="Logic, simulation, and benchmark evidence support only a computational claim.",
        ),
        ClaimEvidenceLink(
            claim_id="externally_mapped",
            evidence_ids=["case01_external_cello_mapping"],
            note="External mapping must remain unsupported when Cello was not run.",
        ),
        ClaimEvidenceLink(
            claim_id="sequence_supported",
            evidence_ids=["case01_sequence_checks"],
            note="Illustrative sequence checks are insufficient for a full biological claim.",
        ),
        ClaimEvidenceLink(
            claim_id="experimentally_supported",
            evidence_ids=["case01_experimental_validation"],
            note="Experimental support requires measured evidence.",
        ),
    ]
    return build_evidence_manifest(
        subject={
            "subject_type": "public_demo_snapshot",
            "identifier": str(fixed_demo.get("task_id") or "case_01"),
            "intent": packet.get("intent"),
        },
        claim_boundary=str(packet.get("claim_boundary") or ""),
        evidence_records=records,
        claim_links=links,
        intended_use=CASE01_INTENDED_USE,
        generated_at=_optional_text(packet.get("created_at")),
    )


def _stable_benchmark_evidence_hash(benchmark: dict[str, Any]) -> str | None:
    """Hash reviewable benchmark facts without transient run identifiers."""

    stable_fields = (
        "dataset_id",
        "dataset_version",
        "dataset_license",
        "profile_id",
        "scoring_version",
        "case_count",
        "passed_count",
        "failed_count",
        "pass_rate",
        "mean_score",
    )
    stable_payload = {field: benchmark.get(field) for field in stable_fields}
    if not any(value is not None for value in stable_payload.values()):
        return None
    return canonical_payload_hash(stable_payload)
