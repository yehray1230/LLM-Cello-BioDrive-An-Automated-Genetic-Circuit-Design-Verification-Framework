from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping


CLAIM_AUDIT_VERSION = "egma-claim-audit-v1"
CLAIM_TYPES = frozenset(
    {
        "computational_screening",
        "formal_functional_success",
        "simulation_completed",
        "external_cello_mapping",
        "experimental_validation",
        "wet_lab_ready",
        "quantitative_in_vivo_prediction",
        "comparative_superiority",
    }
)
EVIDENCE_CATEGORIES = frozenset(
    {
        "output_contract_check",
        "specification_check",
        "formal_syntax_check",
        "topology_check",
        "truth_table_check",
        "constraint_check",
        "signal_overlap_check",
        "simulation_trace",
        "provenance_check",
        "claim_audit",
        "external_cello_mapping",
        "independent_experimental_measurement",
        "frozen_comparative_analysis",
    }
)
_REQUIRED_CATEGORIES = {
    "computational_screening": frozenset(
        {"output_contract_check", "formal_syntax_check"}
    ),
    "formal_functional_success": frozenset(
        {
            "output_contract_check",
            "formal_syntax_check",
            "topology_check",
            "truth_table_check",
        }
    ),
    "simulation_completed": frozenset({"simulation_trace"}),
    "external_cello_mapping": frozenset({"external_cello_mapping"}),
    "experimental_validation": frozenset(
        {"independent_experimental_measurement"}
    ),
    "comparative_superiority": frozenset({"frozen_comparative_analysis"}),
}
_NEVER_SUPPORTED = frozenset(
    {"wet_lab_ready", "quantitative_in_vivo_prediction"}
)
_SUMMARY_PATTERNS = {
    "formal_functional_success": (
        r"\bformally verified\b",
        r"\bformal functional success\b",
        r"\btruth[- ]table verified\b",
    ),
    "simulation_completed": (
        r"\bsuccessfully simulated\b",
        r"\bsimulation (?:passed|completed)\b",
    ),
    "external_cello_mapping": (
        r"\bexternally mapped\b",
        r"\bcello[- ]mapped\b",
        r"\bmapped (?:by|with) cello\b",
    ),
    "experimental_validation": (
        r"\bexperimentally validated\b",
        r"\bvalidated in (?:e\.?\s*coli|cells?)\b",
        r"\bwet[- ]lab validated\b",
    ),
    "wet_lab_ready": (
        r"\bwet[- ]lab ready\b",
        r"\bready (?:for|to enter) (?:the )?wet[- ]lab\b",
        r"\bready to build\b",
    ),
    "quantitative_in_vivo_prediction": (
        r"\bpredicts? quantitative in vivo\b",
        r"\bquantitative in[- ]vivo prediction\b",
    ),
    "comparative_superiority": (
        r"\boutperform(?:s|ed)?\b",
        r"\bsuperior to\b",
        r"\bbetter than (?:the )?(?:baseline|direct|ablated)\b",
    ),
}


@dataclass(frozen=True)
class ClaimAuditDecision:
    claim_type: str
    text: str
    source: str
    supported: bool
    evidence_refs: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_type": self.claim_type,
            "text": self.text,
            "source": self.source,
            "supported": self.supported,
            "evidence_refs": list(self.evidence_refs),
            "reason_codes": list(self.reason_codes),
        }


def audit_egma_claims(
    claims: Iterable[Mapping[str, Any]],
    evidence_records: Iterable[Mapping[str, Any]],
    user_summary: str,
) -> dict[str, Any]:
    records = list(evidence_records)
    by_id = {
        str(record.get("evidence_id")): record
        for record in records
        if str(record.get("evidence_id") or "")
    }
    structured = list(claims)
    decisions = [
        _audit_one_claim(claim, by_id, source="structured") for claim in structured
    ]

    structured_types = {
        str(claim.get("claim_type"))
        for claim in structured
        if str(claim.get("claim_type") or "")
    }
    detected = detect_summary_claim_types(user_summary)
    for claim_type in sorted(detected - structured_types):
        decisions.append(
            ClaimAuditDecision(
                claim_type=claim_type,
                text=user_summary,
                source="unstructured_summary",
                supported=False,
                evidence_refs=(),
                reason_codes=("SUMMARY_CLAIM_NOT_STRUCTURED",),
            )
        )

    unsupported = [decision for decision in decisions if not decision.supported]
    return {
        "schema_version": CLAIM_AUDIT_VERSION,
        "status": "fail_closed" if unsupported else "parsed",
        "unsupported_claim": bool(unsupported),
        "unsupported_count": len(unsupported),
        "detected_summary_claim_types": sorted(detected),
        "decisions": [decision.to_dict() for decision in decisions],
    }


def detect_summary_claim_types(user_summary: str) -> frozenset[str]:
    if not isinstance(user_summary, str):
        return frozenset()
    lowered = user_summary.lower()
    return frozenset(
        claim_type
        for claim_type, patterns in _SUMMARY_PATTERNS.items()
        if any(re.search(pattern, lowered) for pattern in patterns)
    )


def _audit_one_claim(
    claim: Mapping[str, Any],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    *,
    source: str,
) -> ClaimAuditDecision:
    claim_type = str(claim.get("claim_type") or "")
    text = str(claim.get("text") or "")
    evidence_refs = tuple(str(value) for value in claim.get("evidence_refs") or [])
    reasons: list[str] = []

    if claim_type not in CLAIM_TYPES:
        reasons.append("UNKNOWN_CLAIM_TYPE")
    if not text.strip():
        reasons.append("CLAIM_TEXT_REQUIRED")
    if claim_type in _NEVER_SUPPORTED:
        reasons.append("CLAIM_OUTSIDE_PROTOCOL_SUPPORT_BOUNDARY")

    selected = [
        evidence_by_id[evidence_id]
        for evidence_id in evidence_refs
        if evidence_id in evidence_by_id
    ]
    missing_refs = [
        evidence_id for evidence_id in evidence_refs if evidence_id not in evidence_by_id
    ]
    if missing_refs:
        reasons.append("EVIDENCE_REFERENCE_MISSING")

    required = _REQUIRED_CATEGORIES.get(claim_type, frozenset())
    passing_categories = {
        str(record.get("category"))
        for record in selected
        if record.get("status") == "passed"
        and record.get("comparison_eligible", True) is True
    }
    if not required.issubset(passing_categories):
        reasons.append("REQUIRED_EVIDENCE_CATEGORY_MISSING_OR_FAILED")

    invalid_categories = {
        str(record.get("category"))
        for record in selected
        if record.get("category") not in EVIDENCE_CATEGORIES
    }
    if invalid_categories:
        reasons.append("UNKNOWN_EVIDENCE_CATEGORY")

    if claim_type == "external_cello_mapping" and not any(
        record.get("category") == "external_cello_mapping"
        and record.get("status") == "passed"
        and _metadata(record).get("mapping_mode") == "external"
        and _metadata(record).get("buildable") is True
        for record in selected
    ):
        reasons.append("EXTERNAL_CELLO_MAPPING_NOT_PROVEN")

    if claim_type == "experimental_validation" and not any(
        record.get("category") == "independent_experimental_measurement"
        and record.get("status") == "passed"
        and _metadata(record).get("independence")
        == "independent_experimental_measurement"
        for record in selected
    ):
        reasons.append("INDEPENDENT_EXPERIMENTAL_EVIDENCE_NOT_PROVEN")

    if claim_type == "comparative_superiority" and not any(
        record.get("category") == "frozen_comparative_analysis"
        and record.get("status") == "passed"
        and _metadata(record).get("comparison_eligible") is True
        and _metadata(record).get("paired_coverage_complete") is True
        for record in selected
    ):
        reasons.append("FROZEN_COMPARISON_NOT_PROVEN")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return ClaimAuditDecision(
        claim_type=claim_type,
        text=text,
        source=source,
        supported=not unique_reasons,
        evidence_refs=evidence_refs,
        reason_codes=unique_reasons,
    )


def _metadata(record: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = record.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}
