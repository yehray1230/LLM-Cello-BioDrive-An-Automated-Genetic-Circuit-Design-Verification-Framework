from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping

from benchmark_suite.egma_boolean import (
    BooleanExpressionError,
    canonical_expression,
    canonical_truth_table,
    parse_boolean_expression,
)
from benchmark_suite.egma_claim_audit import (
    CLAIM_TYPES,
    EVIDENCE_CATEGORIES,
    audit_egma_claims,
)
from benchmark_suite.egma_contracts import (
    EXPECTED_RESPONSE_CLASSES,
    INTENT_STATUSES,
    LANGUAGE_STRATA,
    RESULT_SCHEMA_VERSION,
    SOURCE_FAMILIES,
    SPLITS,
    SYSTEM_IDS,
    TASK_SCHEMA_VERSION,
    validate_evidence_ablation_bundle,
)
from benchmark_suite.egma_feedback import validate_feedback_trace
from benchmark_suite.egma_topology import TOPOLOGY_INVARIANTS


_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SYMBOL_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")


def _required_mapping(
    payload: Mapping[str, Any],
    field: str,
    errors: list[str],
) -> Mapping[str, Any]:
    value = payload.get(field)
    if not isinstance(value, Mapping):
        errors.append(f"{field} must be an object.")
        return {}
    return value


def _enum(
    value: Any,
    allowed: frozenset[str],
    field: str,
    errors: list[str],
) -> None:
    if value not in allowed:
        errors.append(f"{field} must be one of {sorted(allowed)}.")


def validate_egma_task(task: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if task.get("schema_version") != TASK_SCHEMA_VERSION:
        errors.append(f"schema_version must equal {TASK_SCHEMA_VERSION!r}.")
    task_id = task.get("task_id")
    if not isinstance(task_id, str) or not _ID_PATTERN.fullmatch(task_id):
        errors.append("task_id is invalid.")
    request = task.get("request")
    if not isinstance(request, str) or not request.strip():
        errors.append("request must be a non-empty string.")

    source = _required_mapping(task, "source", errors)
    _enum(source.get("family"), SOURCE_FAMILIES, "source.family", errors)
    if not str(source.get("locator") or "").strip():
        errors.append("source.locator is required.")
    if not str(source.get("license_status") or "").strip():
        errors.append("source.license_status is required.")
    if source.get("family") == "literature_anchored":
        if not str(source.get("exact_location") or "").strip():
            errors.append("Literature tasks require source.exact_location.")
        if source.get("license_status") in {None, "", "unknown"}:
            errors.append("Literature tasks require resolved source rights.")

    intent = task.get("intent_status")
    expected_class = task.get("expected_response_class")
    _enum(intent, INTENT_STATUSES, "intent_status", errors)
    _enum(
        expected_class,
        EXPECTED_RESPONSE_CLASSES,
        "expected_response_class",
        errors,
    )
    if intent == "feasible" and expected_class != "design":
        errors.append("Feasible tasks must expect a design response.")
    if intent == "underspecified" and expected_class != "clarification":
        errors.append("Under-specified tasks must expect clarification.")
    if (
        intent == "contradictory_or_infeasible"
        and expected_class != "unresolved_or_refusal"
    ):
        errors.append(
            "Contradictory or infeasible tasks must expect unresolved_or_refusal."
        )

    language = _required_mapping(task, "language", errors)
    _enum(language.get("stratum"), LANGUAGE_STRATA, "language.stratum", errors)
    _enum(task.get("split"), SPLITS, "split", errors)
    if not str(task.get("leakage_group") or "").strip():
        errors.append("leakage_group is required.")

    generation = _required_mapping(task, "generation", errors)
    if not str(generation.get("generator_version") or "").strip():
        errors.append("generation.generator_version is required.")
    if not isinstance(generation.get("seed"), int):
        errors.append("generation.seed must be an integer.")

    formal_spec = task.get("formal_spec")
    if intent == "feasible":
        if not isinstance(formal_spec, Mapping):
            errors.append("Feasible tasks require formal_spec.")
        else:
            errors.extend(_validate_formal_spec(formal_spec))
    elif formal_spec is not None and not isinstance(formal_spec, Mapping):
        errors.append("formal_spec must be an object or null.")
    return errors


def _validate_formal_spec(formal_spec: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    inputs = formal_spec.get("input_symbols")
    output = formal_spec.get("output_symbol")
    expression = formal_spec.get("boolean_expression")
    truth_table = formal_spec.get("truth_table")
    allowed_operators = formal_spec.get("allowed_operators")

    if not isinstance(inputs, list) or len(inputs) not in {2, 3}:
        errors.append("formal_spec.input_symbols must contain two or three symbols.")
        return errors
    if any(not isinstance(symbol, str) or not _SYMBOL_PATTERN.fullmatch(symbol) for symbol in inputs):
        errors.append("formal_spec.input_symbols contains an invalid symbol.")
    if len(set(inputs)) != len(inputs):
        errors.append("formal_spec.input_symbols must be unique.")
    if not isinstance(output, str) or not _SYMBOL_PATTERN.fullmatch(output):
        errors.append("formal_spec.output_symbol is invalid.")
    if output in inputs:
        errors.append("formal_spec.output_symbol must differ from the inputs.")
    if allowed_operators != ["NOT", "AND", "OR"]:
        errors.append(
            "formal_spec.allowed_operators must equal ['NOT', 'AND', 'OR']."
        )
    if not isinstance(expression, str):
        errors.append("formal_spec.boolean_expression must be a string.")
        return errors
    try:
        node = parse_boolean_expression(expression)
        canonical = canonical_expression(node)
        if formal_spec.get("canonical_expression") != canonical:
            errors.append("formal_spec.canonical_expression is not canonical.")
        expected_table = canonical_truth_table(expression, inputs, output)
    except BooleanExpressionError as exc:
        errors.append(f"formal_spec.boolean_expression is invalid: {exc}")
        return errors
    if truth_table != expected_table:
        errors.append("formal_spec.truth_table does not match the canonical generator.")
    topology = formal_spec.get("topology_invariants")
    if topology != list(TOPOLOGY_INVARIANTS):
        errors.append(
            "formal_spec.topology_invariants must equal the frozen v1 vocabulary."
        )
    return errors


def validate_egma_result(result: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if result.get("schema_version") != RESULT_SCHEMA_VERSION:
        errors.append(f"schema_version must equal {RESULT_SCHEMA_VERSION!r}.")
    for field in ("task_id", "run_id"):
        value = result.get(field)
        if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
            errors.append(f"{field} is invalid.")
    _enum(result.get("system_id"), SYSTEM_IDS, "system_id", errors)
    status = result.get("response_status")
    allowed_statuses = frozenset(
        {
            "completed",
            "clarification",
            "unresolved",
            "refusal",
            "operational_failure",
        }
    )
    _enum(status, allowed_statuses, "response_status", errors)
    user_summary = result.get("user_summary")
    if not isinstance(user_summary, str):
        errors.append("user_summary must be a string.")
    candidate_output = result.get("candidate_output")
    if status == "completed" and not isinstance(candidate_output, Mapping):
        errors.append("Completed results require candidate_output.")
    if status != "completed" and candidate_output is not None:
        errors.append("Non-completed results must set candidate_output to null.")
    if isinstance(candidate_output, Mapping):
        _enum(
            candidate_output.get("representation_type"),
            frozenset({"structured_logic", "verilog", "sbol3"}),
            "candidate_output.representation_type",
            errors,
        )
        if not str(candidate_output.get("artifact_ref") or "").strip():
            errors.append("candidate_output.artifact_ref is required.")
        declared_inputs = candidate_output.get("declared_inputs")
        if not isinstance(declared_inputs, list) or len(declared_inputs) not in {2, 3}:
            errors.append(
                "candidate_output.declared_inputs must contain two or three inputs."
            )
        if not str(candidate_output.get("declared_output") or "").strip():
            errors.append("candidate_output.declared_output is required.")
        if not str(candidate_output.get("canonical_expression") or "").strip():
            errors.append("candidate_output.canonical_expression is required.")
    for field in ("artifacts", "attempts", "evidence_records"):
        if not isinstance(result.get(field), list):
            errors.append(f"{field} must be an array.")
    _required_mapping(result, "operational", errors)
    protocol_versions = _required_mapping(result, "protocol_versions", errors)
    for field in ("task_schema", "formal_evaluator", "claim_audit"):
        if not str(protocol_versions.get(field) or "").strip():
            errors.append(f"protocol_versions.{field} is required.")
    feedback_trace = _required_mapping(result, "feedback_trace", errors)
    if feedback_trace:
        errors.extend(validate_feedback_trace(feedback_trace))
        if feedback_trace.get("system_id") != result.get("system_id"):
            errors.append("feedback_trace.system_id must match result.system_id.")

    metrics = _required_mapping(result, "metrics", errors)
    required_metrics = (
        "output_contract_parse",
        "specification_complete",
        "syntax_valid",
        "topology_valid",
        "truth_table_exact",
        "functional_success",
        "simulation_complete",
        "unsupported_claim",
    )
    for field in required_metrics:
        if field not in metrics:
            errors.append(f"metrics.{field} is required.")
        elif metrics[field] is not None and not isinstance(metrics[field], bool):
            errors.append(f"metrics.{field} must be true, false, or null.")
    if metrics.get("functional_success") is True:
        prerequisites = (
            "output_contract_parse",
            "specification_complete",
            "syntax_valid",
            "topology_valid",
            "truth_table_exact",
        )
        if any(metrics.get(field) is not True for field in prerequisites):
            errors.append(
                "functional_success=true requires every formal prerequisite."
            )

    claims = result.get("claims")
    if not isinstance(claims, list):
        errors.append("claims must be an array.")
    elif any(not isinstance(claim, Mapping) for claim in claims):
        errors.append("Every claim must be an object.")
    else:
        for index, claim in enumerate(claims):
            for field in ("claim_type", "text", "evidence_refs", "supported"):
                if field not in claim:
                    errors.append(f"claims[{index}].{field} is required.")
            if claim.get("claim_type") not in CLAIM_TYPES:
                errors.append(f"claims[{index}].claim_type is invalid.")
            if not isinstance(claim.get("evidence_refs"), list):
                errors.append(f"claims[{index}].evidence_refs must be an array.")
            if not isinstance(claim.get("supported"), bool):
                errors.append(f"claims[{index}].supported must be a boolean.")

    evidence_records = result.get("evidence_records")
    if isinstance(evidence_records, list):
        for index, record in enumerate(evidence_records):
            if not isinstance(record, Mapping):
                errors.append(f"evidence_records[{index}] must be an object.")
                continue
            for field in (
                "evidence_id",
                "category",
                "status",
                "comparison_eligible",
                "artifact_ref",
                "metadata",
            ):
                if field not in record:
                    errors.append(f"evidence_records[{index}].{field} is required.")
            if record.get("category") not in EVIDENCE_CATEGORIES:
                errors.append(f"evidence_records[{index}].category is invalid.")
            if record.get("status") not in {
                "passed",
                "failed",
                "not_run",
                "unknown",
            }:
                errors.append(f"evidence_records[{index}].status is invalid.")
            if not isinstance(record.get("comparison_eligible"), bool):
                errors.append(
                    f"evidence_records[{index}].comparison_eligible "
                    "must be a boolean."
                )
            if not isinstance(record.get("metadata"), Mapping):
                errors.append(f"evidence_records[{index}].metadata must be an object.")

    if (
        isinstance(claims, list)
        and all(isinstance(claim, Mapping) for claim in claims)
        and isinstance(evidence_records, list)
        and all(isinstance(record, Mapping) for record in evidence_records)
        and isinstance(user_summary, str)
    ):
        computed_audit = audit_egma_claims(
            claims,
            evidence_records,
            user_summary,
        )
        if result.get("claim_audit") != computed_audit:
            errors.append("claim_audit does not match deterministic recomputation.")
        if metrics.get("unsupported_claim") is not computed_audit[
            "unsupported_claim"
        ]:
            errors.append(
                "metrics.unsupported_claim does not match deterministic claim audit."
            )
        structured_decisions = [
            decision
            for decision in computed_audit["decisions"]
            if decision["source"] == "structured"
        ]
        for index, (claim, decision) in enumerate(
            zip(claims, structured_decisions, strict=True)
        ):
            if claim.get("supported") is not decision["supported"]:
                errors.append(
                    f"claims[{index}].supported does not match deterministic audit."
                )

    for channel in (
        "evidence_feedback",
        "claim_audit",
        "feedback_trace",
        "prompt_messages",
        "tool_messages",
        "agent_state",
        "cache_payload",
        "ranking_feedback",
        "repair_feedback",
        "evidence_canaries",
    ):
        if channel not in result:
            errors.append(f"{channel} is required.")

    if result.get("system_id") == "S2":
        errors.extend(validate_evidence_ablation_bundle(result))
    return errors


def load_json_object(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Expected one JSON object.")
    return payload
