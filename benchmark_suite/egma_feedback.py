from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from benchmark_suite.egma_contracts import validate_evidence_ablation_bundle


FEEDBACK_POLICY_VERSION = "egma-evidence-feedback-v1"


@dataclass(frozen=True)
class RepairBudget:
    budget_id: str
    max_repair_iterations: int
    max_candidate_evaluations: int
    max_feedback_items_per_iteration: int
    max_total_feedback_items: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DIRECT_BUDGET = RepairBudget(
    budget_id="egma-direct-budget-v1",
    max_repair_iterations=0,
    max_candidate_evaluations=1,
    max_feedback_items_per_iteration=0,
    max_total_feedback_items=0,
)
AGENT_REPAIR_BUDGET = RepairBudget(
    budget_id="egma-agent-repair-budget-v1",
    max_repair_iterations=2,
    max_candidate_evaluations=3,
    max_feedback_items_per_iteration=8,
    max_total_feedback_items=16,
)
SYSTEM_BUDGETS = {
    "S0": DIRECT_BUDGET,
    "S1": DIRECT_BUDGET,
    "S2": AGENT_REPAIR_BUDGET,
    "S3": AGENT_REPAIR_BUDGET,
}

FEEDBACK_CATEGORY_BY_EVIDENCE = {
    "output_contract_check": "output_contract_failure",
    "specification_check": "specification_incomplete",
    "formal_syntax_check": "formal_syntax_failure",
    "topology_check": "topology_invariant_failure",
    "truth_table_check": "truth_table_mismatch",
    "constraint_check": "constraint_failure",
    "signal_overlap_check": "signal_overlap",
    "simulation_trace": "simulation_incomplete",
    "provenance_check": "provenance_gap",
    "claim_audit": "unsupported_claim",
}
FEEDBACK_CATEGORIES = frozenset(FEEDBACK_CATEGORY_BY_EVIDENCE.values())
FEEDBACK_SEVERITY = {
    "output_contract_failure": "blocking",
    "specification_incomplete": "blocking",
    "formal_syntax_failure": "blocking",
    "topology_invariant_failure": "blocking",
    "truth_table_mismatch": "blocking",
    "constraint_failure": "blocking",
    "signal_overlap": "warning",
    "simulation_incomplete": "warning",
    "provenance_gap": "warning",
    "unsupported_claim": "blocking",
}
FEEDBACK_PAYLOAD_FIELDS = {
    "output_contract_failure": ("field_path", "reason_codes"),
    "specification_incomplete": ("missing_symbols", "reason_codes"),
    "formal_syntax_failure": ("line", "column", "reason_codes"),
    "topology_invariant_failure": ("invariant", "reason_codes"),
    "truth_table_mismatch": (
        "input_assignment",
        "expected_output",
        "actual_output",
    ),
    "constraint_failure": ("constraint_id", "reason_codes"),
    "signal_overlap": ("signals", "overlap_score", "reason_codes"),
    "simulation_incomplete": ("missing_fields", "reason_codes"),
    "provenance_gap": ("field_path", "reason_codes"),
    "unsupported_claim": ("claim_type", "reason_codes"),
}
FEEDBACK_REQUIRED_PAYLOAD_FIELDS = {
    "output_contract_failure": frozenset({"reason_codes"}),
    "specification_incomplete": frozenset({"missing_symbols"}),
    "formal_syntax_failure": frozenset({"reason_codes"}),
    "topology_invariant_failure": frozenset({"invariant", "reason_codes"}),
    "truth_table_mismatch": frozenset(
        {"input_assignment", "expected_output", "actual_output"}
    ),
    "constraint_failure": frozenset({"constraint_id", "reason_codes"}),
    "signal_overlap": frozenset({"signals", "overlap_score"}),
    "simulation_incomplete": frozenset({"missing_fields", "reason_codes"}),
    "provenance_gap": frozenset({"field_path", "reason_codes"}),
    "unsupported_claim": frozenset({"claim_type", "reason_codes"}),
}
_FEEDBACK_ITEM_FIELDS = frozenset(
    {
        "policy_version",
        "feedback_id",
        "category",
        "severity",
        "evidence_ref",
        "candidate_id",
        "payload",
    }
)


def project_feedback_items(
    system_id: str,
    evidence_records: Iterable[Mapping[str, Any]],
    *,
    candidate_id: str,
) -> list[dict[str, Any]]:
    """Project only frozen S3 fields; S0-S2 receive no evidence feedback."""

    if system_id != "S3":
        return []
    projected: list[dict[str, Any]] = []
    for record in evidence_records:
        evidence_category = str(record.get("category") or "")
        feedback_category = FEEDBACK_CATEGORY_BY_EVIDENCE.get(evidence_category)
        if feedback_category is None or record.get("status") != "failed":
            continue
        evidence_id = str(record.get("evidence_id") or "")
        metadata = record.get("metadata")
        if not evidence_id or not isinstance(metadata, Mapping):
            continue
        allowed_fields = FEEDBACK_PAYLOAD_FIELDS[feedback_category]
        payload = {
            field: deepcopy(metadata[field])
            for field in allowed_fields
            if field in metadata
        }
        if not FEEDBACK_REQUIRED_PAYLOAD_FIELDS[feedback_category].issubset(payload):
            continue
        projected.append(
            {
                "policy_version": FEEDBACK_POLICY_VERSION,
                "feedback_id": f"{candidate_id}:{evidence_id}",
                "category": feedback_category,
                "severity": FEEDBACK_SEVERITY[feedback_category],
                "evidence_ref": evidence_id,
                "candidate_id": candidate_id,
                "payload": payload,
            }
        )
    projected.sort(
        key=lambda item: (
            0 if item["severity"] == "blocking" else 1,
            item["category"],
            item["evidence_ref"],
        )
    )
    return projected[: AGENT_REPAIR_BUDGET.max_feedback_items_per_iteration]


def validate_feedback_trace(trace: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    system_id = trace.get("system_id")
    if system_id not in SYSTEM_BUDGETS:
        return [f"Unknown feedback trace system_id: {system_id!r}."]
    if trace.get("policy_version") != FEEDBACK_POLICY_VERSION:
        errors.append("feedback trace policy_version is invalid.")
    expected_budget = SYSTEM_BUDGETS[str(system_id)]
    if trace.get("budget") != expected_budget.to_dict():
        errors.append("feedback trace budget does not match the frozen system budget.")
    iterations = trace.get("iterations")
    if not isinstance(iterations, list):
        return errors + ["feedback trace iterations must be an array."]
    if not iterations:
        errors.append("feedback trace requires at least one evaluated attempt.")
    if len(iterations) > expected_budget.max_candidate_evaluations:
        errors.append("candidate evaluation budget exceeded.")
    if len(iterations) - 1 > expected_budget.max_repair_iterations:
        errors.append("repair iteration budget exceeded.")

    total_feedback = 0
    formal_success_seen = False
    candidate_ids: list[str] = []
    for index, iteration in enumerate(iterations):
        if not isinstance(iteration, Mapping):
            errors.append(f"iterations[{index}] must be an object.")
            continue
        if iteration.get("iteration_index") != index:
            errors.append(f"iterations[{index}] has a non-canonical iteration_index.")
        candidate_id = iteration.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            errors.append(f"iterations[{index}].candidate_id is required.")
        else:
            candidate_ids.append(candidate_id)
        if formal_success_seen:
            errors.append("feedback trace continues after formal success.")
        if iteration.get("formal_success") is True:
            formal_success_seen = True
        iteration_feedback: dict[str, Mapping[str, Any]] = {}
        for channel in ("ranking_feedback", "repair_feedback"):
            items = iteration.get(channel)
            if not isinstance(items, list):
                errors.append(f"iterations[{index}].{channel} must be an array.")
                continue
            if len(items) > expected_budget.max_feedback_items_per_iteration:
                errors.append(f"iterations[{index}].{channel} exceeds item budget.")
            for item_index, item in enumerate(items):
                if isinstance(item, Mapping):
                    feedback_id = str(item.get("feedback_id") or "")
                    previous = iteration_feedback.get(feedback_id)
                    if previous is not None and previous != item:
                        errors.append(
                            f"iterations[{index}] reuses feedback_id with "
                            "different content."
                        )
                    elif feedback_id:
                        iteration_feedback[feedback_id] = item
                errors.extend(
                    _validate_feedback_item(
                        item,
                        system_id=str(system_id),
                        candidate_id=str(candidate_id or ""),
                        path=f"iterations[{index}].{channel}[{item_index}]",
                    )
                )
        if (
            len(iteration_feedback)
            > expected_budget.max_feedback_items_per_iteration
        ):
            errors.append(
                f"iterations[{index}] exceeds the combined feedback item budget."
            )
        total_feedback += len(iteration_feedback)
    if len(candidate_ids) != len(set(candidate_ids)):
        errors.append("feedback trace candidate_id values must be unique.")
    if total_feedback > expected_budget.max_total_feedback_items:
        errors.append("total feedback item budget exceeded.")

    stopped_reason = trace.get("stopped_reason")
    allowed_reasons = {
        "formal_success",
        "budget_exhausted",
        "nonrepairable",
        "invalid_intent",
        "operational_failure",
    }
    if stopped_reason not in allowed_reasons:
        errors.append("feedback trace stopped_reason is invalid.")
    if stopped_reason == "formal_success" and not formal_success_seen:
        errors.append("formal_success stop requires a successful iteration.")
    if stopped_reason == "budget_exhausted":
        if len(iterations) != expected_budget.max_candidate_evaluations:
            errors.append(
                "budget_exhausted requires the full candidate evaluation budget."
            )
        if formal_success_seen:
            errors.append("budget_exhausted cannot follow formal success.")

    if system_id == "S2":
        bundle = {
            "system_id": "S2",
            "evidence_feedback": [],
            "ranking_feedback": [
                item
                for iteration in iterations
                if isinstance(iteration, Mapping)
                for item in iteration.get("ranking_feedback", [])
            ],
            "repair_feedback": [
                item
                for iteration in iterations
                if isinstance(iteration, Mapping)
                for item in iteration.get("repair_feedback", [])
            ],
            "prompt_messages": trace.get("prompt_messages", []),
            "tool_messages": trace.get("tool_messages", []),
            "agent_state": trace.get("agent_state", {}),
            "cache_payload": trace.get("cache_payload", {}),
            "evidence_canaries": trace.get("evidence_canaries", []),
        }
        errors.extend(validate_evidence_ablation_bundle(bundle))
    return errors


def validate_s2_s3_parity(
    s2_configuration: Mapping[str, Any],
    s3_configuration: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    allowed_differences = {"system_id", "evidence_feedback_enabled"}
    s2_keys = set(s2_configuration) - allowed_differences
    s3_keys = set(s3_configuration) - allowed_differences
    if s2_keys != s3_keys:
        errors.append("S2/S3 parity keys differ outside the ablation.")
    for key in sorted(s2_keys & s3_keys):
        if s2_configuration.get(key) != s3_configuration.get(key):
            errors.append(f"S2/S3 parity mismatch: {key}.")
    if s2_configuration.get("system_id") != "S2":
        errors.append("S2 configuration has the wrong system_id.")
    if s3_configuration.get("system_id") != "S3":
        errors.append("S3 configuration has the wrong system_id.")
    if s2_configuration.get("evidence_feedback_enabled") is not False:
        errors.append("S2 evidence feedback must be disabled.")
    if s3_configuration.get("evidence_feedback_enabled") is not True:
        errors.append("S3 evidence feedback must be enabled.")
    if s2_configuration.get("repair_budget") != AGENT_REPAIR_BUDGET.to_dict():
        errors.append("S2 repair budget is not frozen.")
    if s3_configuration.get("repair_budget") != AGENT_REPAIR_BUDGET.to_dict():
        errors.append("S3 repair budget is not frozen.")
    return errors


def _validate_feedback_item(
    item: Any,
    *,
    system_id: str,
    candidate_id: str,
    path: str,
) -> list[str]:
    if not isinstance(item, Mapping):
        return [f"{path} must be an object."]
    errors: list[str] = []
    if system_id != "S3":
        errors.append(f"{path} is forbidden for {system_id}.")
    if set(item) != _FEEDBACK_ITEM_FIELDS:
        errors.append(f"{path} fields do not match the frozen allowlist.")
    category = item.get("category")
    if category not in FEEDBACK_CATEGORIES:
        errors.append(f"{path}.category is not allowlisted.")
        return errors
    if item.get("policy_version") != FEEDBACK_POLICY_VERSION:
        errors.append(f"{path}.policy_version is invalid.")
    if item.get("candidate_id") != candidate_id:
        errors.append(f"{path}.candidate_id does not match its iteration.")
    if item.get("severity") != FEEDBACK_SEVERITY[category]:
        errors.append(f"{path}.severity does not match the frozen category.")
    if not str(item.get("evidence_ref") or ""):
        errors.append(f"{path}.evidence_ref is required.")
    expected_feedback_id = f"{candidate_id}:{item.get('evidence_ref')}"
    if item.get("feedback_id") != expected_feedback_id:
        errors.append(f"{path}.feedback_id is not canonical.")
    payload = item.get("payload")
    if not isinstance(payload, Mapping):
        errors.append(f"{path}.payload must be an object.")
    elif not set(payload).issubset(FEEDBACK_PAYLOAD_FIELDS[category]):
        errors.append(f"{path}.payload contains non-allowlisted fields.")
    else:
        if not FEEDBACK_REQUIRED_PAYLOAD_FIELDS[category].issubset(payload):
            errors.append(f"{path}.payload is missing required fields.")
        errors.extend(_validate_payload_types(category, payload, path))
    return errors


def _validate_payload_types(
    category: str,
    payload: Mapping[str, Any],
    path: str,
) -> list[str]:
    errors: list[str] = []
    string_fields = {"field_path", "invariant", "constraint_id", "claim_type"}
    string_list_fields = {
        "reason_codes",
        "missing_symbols",
        "signals",
        "missing_fields",
    }
    integer_fields = {"line", "column"}
    binary_fields = {"expected_output", "actual_output"}
    for field, value in payload.items():
        if field in string_fields and not isinstance(value, str):
            errors.append(f"{path}.payload.{field} must be a string.")
        elif field in string_list_fields and (
            not isinstance(value, list)
            or any(not isinstance(item, str) for item in value)
        ):
            errors.append(f"{path}.payload.{field} must be a string array.")
        elif field in integer_fields and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            errors.append(f"{path}.payload.{field} must be a non-negative integer.")
        elif field in binary_fields and (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value not in {0, 1}
        ):
            errors.append(f"{path}.payload.{field} must be binary.")
        elif field == "overlap_score" and (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not 0.0 <= float(value) <= 1.0
        ):
            errors.append(f"{path}.payload.overlap_score must be within [0, 1].")
        elif field == "input_assignment":
            if not isinstance(value, Mapping) or any(
                not isinstance(symbol, str)
                or not isinstance(bit, int)
                or isinstance(bit, bool)
                or bit not in {0, 1}
                for symbol, bit in (
                    value.items() if isinstance(value, Mapping) else ()
                )
            ):
                errors.append(
                    f"{path}.payload.input_assignment must map symbols to binary."
                )
    return errors
