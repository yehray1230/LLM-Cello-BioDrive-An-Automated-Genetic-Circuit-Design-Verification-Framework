from __future__ import annotations

from typing import Any

from exporters.plasmid_tools import AssemblyReport
from schemas.assembly_plan import AssemblyPlan
from schemas.design_ir_v2 import DesignIRV2
from schemas.readiness import (
    READINESS_STAGES,
    ReadinessFinding,
    ReadinessResult,
)
from utils.scalar_values import optional_trimmed_text as _optional_string


PART_EVIDENCE_SCORES = {
    "unknown": 0.0,
    "conceptual": 0.0,
    "illustrative": 0.1,
    "inferred": 0.35,
    "database_derived": 0.7,
    "literature_supported": 0.8,
    "user_verified": 0.9,
    "experimentally_characterized": 1.0,
}
SEQUENCE_WARNING_CODES = {
    "CDS_FRAME_LENGTH",
    "CDS_START_CODON",
    "CDS_STOP_CODON",
    "HOST_COMPATIBILITY",
}


def evaluate_readiness(
    design: DesignIRV2,
    *,
    assembly_report: AssemblyReport | dict[str, Any] | None = None,
    assembly_plan: AssemblyPlan | dict[str, Any] | None = None,
    computational_evaluation: dict[str, Any] | None = None,
    primer_result: dict[str, Any] | None = None,
    sequence_optimization_result: dict[str, Any] | None = None,
    host_optimization_result: dict[str, Any] | None = None,
    calibration_result: dict[str, Any] | None = None,
) -> ReadinessResult:
    assembly_payload = _payload(assembly_report)
    plan_payload = _payload(assembly_plan)
    blockers = _findings(assembly_payload, "assembly_report", "error")
    blockers.extend(_findings(plan_payload, "assembly_plan", "error"))
    warnings = _findings(assembly_payload, "assembly_report", "warning")
    warnings.extend(_findings(plan_payload, "assembly_plan", "warning"))

    computational_score = _optional_score(
        (computational_evaluation or {}).get(
            "computational_design_score",
            (computational_evaluation or {}).get("weighted_total_score"),
        )
    )
    logic_score = _evaluation_dimension(
        computational_evaluation,
        "logic_function",
    )
    dynamic_score = _evaluation_dimension(
        computational_evaluation,
        "dynamic_behavior",
    )
    part_evidence_score = _part_evidence_score(design)
    sequence_quality_score = _sequence_quality_score(
        design,
        assembly_payload,
    )
    assembly_plan_score = _assembly_plan_score(plan_payload)
    primer_readiness_score = _result_score(primer_result)
    sequence_optimization_score = _result_score(sequence_optimization_result)
    host_optimization_score = _result_score(host_optimization_result)
    calibration_score = _result_score(calibration_result)
    experimental_readiness_score = _experimental_readiness_score(
        primer_result,
        sequence_optimization_result,
        host_optimization_result,
        calibration_result,
    )
    completed_stages = _completed_stages(
        assembly_payload,
        plan_payload,
        primer_result,
        sequence_optimization_result,
        host_optimization_result,
    )
    status = "blocked" if blockers else completed_stages[-1]
    return ReadinessResult(
        readiness_status=status,
        computational_design_score=computational_score,
        domain_scores={
            "logic_score": logic_score,
            "dynamic_score": dynamic_score,
            "part_evidence_score": part_evidence_score,
            "sequence_quality_score": sequence_quality_score,
            "assembly_plan_score": assembly_plan_score,
            "primer_readiness_score": primer_readiness_score,
            "sequence_optimization_score": sequence_optimization_score,
            "host_optimization_score": host_optimization_score,
            "calibration_score": calibration_score,
            "experimental_readiness_score": experimental_readiness_score,
        },
        domain_applicability={
            "logic_score": _reported(logic_score),
            "dynamic_score": _reported(dynamic_score),
            "part_evidence_score": "derived_from_design_ir",
            "sequence_quality_score": (
                "derived_from_assembly_report"
                if assembly_payload
                else "not_evaluated"
            ),
            "assembly_plan_score": (
                "derived_from_assembly_plan"
                if plan_payload
                else "not_evaluated"
            ),
            "experimental_readiness_score": (
                "derived_from_deliverables"
                if experimental_readiness_score is not None
                else "not_evaluated"
            ),
            "primer_readiness_score": (
                "derived_from_primer_result"
                if primer_readiness_score is not None
                else "not_evaluated"
            ),
            "sequence_optimization_score": (
                "derived_from_sequence_optimization_result"
                if sequence_optimization_score is not None
                else "not_evaluated"
            ),
            "host_optimization_score": (
                "derived_from_host_optimization_result"
                if host_optimization_score is not None
                else "not_evaluated"
            ),
            "calibration_score": (
                "derived_from_calibration_result"
                if calibration_score is not None
                else "not_evaluated"
            ),
        },
        blockers=blockers,
        warnings=warnings,
        completed_stages=completed_stages,
        next_required_stage=_next_stage(completed_stages, blockers),
    )


def _part_evidence_score(design: Any) -> float | None:
    if not design.parts:
        return None
    values = [
        PART_EVIDENCE_SCORES.get(
            getattr(part, "evidence_level", getattr(part, "confidence", "measured")).lower(),
            0.5,
        )
        for part in design.parts
    ]
    return _mean(values)



def _sequence_quality_score(
    design: DesignIRV2,
    assembly: dict[str, Any],
) -> float | None:
    if not assembly:
        return None
    part_count = max(1, len(design.parts))
    warnings = [
        issue
        for issue in assembly.get("issues", [])
        if isinstance(issue, dict)
        and issue.get("code") in SEQUENCE_WARNING_CODES
    ]
    return _clamp(1.0 - len(warnings) / part_count)


def _assembly_plan_score(plan: dict[str, Any]) -> float | None:
    if not plan:
        return None
    if _issues(plan, "error"):
        return 0.0
    fragments = list(plan.get("fragments") or [])
    junctions = list(plan.get("junctions") or [])
    digests = list(plan.get("digests") or [])
    checks = [
        bool(fragments),
        bool(junctions),
        bool(digests),
        all(bool(item.get("unique")) for item in junctions),
        all(bool(item.get("direction_valid", True)) for item in junctions),
        bool(plan.get("target_checksum")),
    ]
    return sum(checks) / len(checks)


def _experimental_readiness_score(
    primer_result: dict[str, Any] | None,
    sequence_optimization_result: dict[str, Any] | None,
    host_optimization_result: dict[str, Any] | None,
    calibration_result: dict[str, Any] | None,
) -> float | None:
    results = [
        primer_result,
        sequence_optimization_result,
        host_optimization_result,
        calibration_result,
    ]
    available = [
        result
        for result in results
        if isinstance(result, dict)
        and result.get("experimental_evidence") is not False
    ]
    if not available:
        return None
    values = [_result_score(result) or 0.0 for result in available]
    return _mean(values)


def _result_score(result: dict[str, Any] | None) -> float | None:
    if not isinstance(result, dict):
        return None
    status = result.get("status")
    if status in {"passed", "ready", "completed"}:
        return 1.0
    if status in {"needs_review", "warning"}:
        return 0.5
    return 0.0


def _completed_stages(
    assembly: dict[str, Any],
    plan: dict[str, Any],
    primer: dict[str, Any] | None,
    sequence_optimization: dict[str, Any] | None,
    host_optimization: dict[str, Any] | None,
) -> list[str]:
    stages = ["conceptual"]
    if assembly.get("readiness_status") in {
        "sequence_complete",
        "assembly_check_passed",
    }:
        stages.append("sequence_complete")
    if plan.get("status") == "ready":
        stages.append("assembly_planned")
    if _ready(primer):
        stages.append("primer_ready")
    if _ready(sequence_optimization):
        stages.append("sequence_optimized")
    if _ready(host_optimization):
        stages.append("host_optimized")
    return stages


def _next_stage(
    completed: list[str],
    blockers: list[ReadinessFinding],
) -> str | None:
    if blockers:
        return completed[-1]
    current = completed[-1]
    index = READINESS_STAGES.index(current)
    return (
        READINESS_STAGES[index + 1]
        if index + 1 < len(READINESS_STAGES)
        else None
    )


def _findings(
    payload: dict[str, Any],
    source: str,
    severity: str,
) -> list[ReadinessFinding]:
    return [
        ReadinessFinding(
            code=str(item.get("code") or "UNKNOWN"),
            message=str(item.get("message") or ""),
            source=source,
            severity=severity,
            subject_id=_optional_string(item.get("subject_id")),
        )
        for item in _issues(payload, severity)
    ]


def _issues(payload: dict[str, Any], severity: str) -> list[dict[str, Any]]:
    values = payload.get("issues")
    if not isinstance(values, list):
        return []
    return [
        item
        for item in values
        if isinstance(item, dict)
        and str(item.get("severity") or "warning") == severity
    ]


def _evaluation_dimension(
    evaluation: dict[str, Any] | None,
    key: str,
) -> float | None:
    dimensions = (
        evaluation.get("dimension_scores")
        if isinstance(evaluation, dict)
        else None
    )
    return (
        _optional_score(dimensions.get(key))
        if isinstance(dimensions, dict)
        else None
    )


def _payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    return to_dict() if callable(to_dict) else {}


def _ready(value: dict[str, Any] | None) -> bool:
    return isinstance(value, dict) and value.get("status") in {
        "passed",
        "ready",
        "completed",
    }


def _reported(value: float | None) -> str:
    return "reported" if value is not None else "not_evaluated"


def _optional_score(value: Any) -> float | None:
    try:
        return None if value is None else _clamp(float(value))
    except (TypeError, ValueError):
        return None


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 10)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
