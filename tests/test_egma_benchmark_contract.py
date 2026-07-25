from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark_suite.egma_boolean import (
    BooleanExpressionError,
    canonical_expression,
    canonical_truth_table,
    parse_boolean_expression,
)
from benchmark_suite.egma_claim_audit import audit_egma_claims
from benchmark_suite.egma_contracts import (
    project_model_visible_state,
    validate_evidence_ablation_bundle,
)
from benchmark_suite.egma_feedback import (
    FEEDBACK_POLICY_VERSION,
    SYSTEM_BUDGETS,
)
from benchmark_suite.egma_validation import (
    validate_egma_result,
    validate_egma_task,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "egma" / "task_contract_cases.json"
PROTOCOL_DIR = Path(__file__).parents[1] / "benchmark_suite" / "protocols"


def _fixtures() -> dict[str, dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _valid_result(system_id: str = "S3") -> dict:
    result = {
        "schema_version": "egma-result-v1",
        "task_id": "fixture_good_a_and_not_b",
        "run_id": "run-001",
        "system_id": system_id,
        "response_status": "completed",
        "user_summary": "A computational design result.",
        "candidate_output": {
            "representation_type": "structured_logic",
            "artifact_ref": "artifact://candidate-001",
            "declared_inputs": ["A", "B"],
            "declared_output": "GFP",
            "canonical_expression": "(A AND (NOT B))",
        },
        "metrics": {
            "output_contract_parse": True,
            "specification_complete": True,
            "syntax_valid": True,
            "topology_valid": True,
            "truth_table_exact": True,
            "functional_success": True,
            "simulation_complete": None,
            "unsupported_claim": False,
        },
        "claims": [],
        "claim_audit": {},
        "artifacts": [],
        "attempts": [],
        "evidence_records": [],
        "operational": {
            "provider_model": "offline-fixture",
            "latency_ms": 0,
            "estimated_cost_usd": 0.0,
        },
        "protocol_versions": {
            "task_schema": "egma-task-v1",
            "formal_evaluator": "egma-formal-evaluator-v1",
            "claim_audit": "egma-claim-audit-v1",
        },
        "feedback_trace": {
            "policy_version": FEEDBACK_POLICY_VERSION,
            "system_id": system_id,
            "budget": SYSTEM_BUDGETS[system_id].to_dict(),
            "iterations": [
                {
                    "iteration_index": 0,
                    "candidate_id": "candidate-001",
                    "formal_success": True,
                    "ranking_feedback": [],
                    "repair_feedback": [],
                }
            ],
            "stopped_reason": "formal_success",
        },
        "evidence_feedback": {},
        "prompt_messages": [],
        "tool_messages": [],
        "agent_state": {},
        "cache_payload": {},
        "ranking_feedback": [],
        "repair_feedback": [],
        "evidence_canaries": [],
    }
    result["claim_audit"] = audit_egma_claims(
        result["claims"],
        result["evidence_records"],
        result["user_summary"],
    )
    return result


def test_boolean_grammar_has_frozen_precedence_and_canonical_form() -> None:
    node = parse_boolean_expression("A | B & !C   ")

    assert canonical_expression(node) == "(A OR (B AND (NOT C)))"


def test_truth_table_generator_is_exhaustive_and_deterministic() -> None:
    table = canonical_truth_table("A AND NOT B", ["A", "B"], "Y")

    assert table == [
        {"A": 0, "B": 0, "Y": 0},
        {"A": 0, "B": 1, "Y": 0},
        {"A": 1, "B": 0, "Y": 1},
        {"A": 1, "B": 1, "Y": 0},
    ]
    assert table == canonical_truth_table("A && ~B", ["A", "B"], "Y")


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("A AND (B", "Missing closing parenthesis"),
        ("A XOR B", "Unexpected token"),
        ("A AND C", "unused declared inputs"),
    ],
)
def test_truth_table_generator_fails_closed(
    expression: str,
    message: str,
) -> None:
    with pytest.raises(BooleanExpressionError, match=message):
        canonical_truth_table(expression, ["A", "B"], "Y")


def test_known_good_task_is_the_only_valid_contract_fixture() -> None:
    fixtures = _fixtures()

    assert validate_egma_task(fixtures["known_good"]) == []
    for name in ("known_bad", "malformed", "ambiguous", "contradictory"):
        assert validate_egma_task(fixtures[name]), name


def test_invalid_fixtures_fail_for_the_intended_reason() -> None:
    fixtures = _fixtures()

    assert any(
        "truth_table does not match" in error
        for error in validate_egma_task(fixtures["known_bad"])
    )
    assert any(
        "boolean_expression is invalid" in error
        for error in validate_egma_task(fixtures["malformed"])
    )
    assert any(
        "must expect clarification" in error
        for error in validate_egma_task(fixtures["ambiguous"])
    )
    assert any(
        "must expect unresolved_or_refusal" in error
        for error in validate_egma_task(fixtures["contradictory"])
    )


def test_result_contract_keeps_primary_and_secondary_metrics_separate() -> None:
    result = _valid_result()

    assert validate_egma_result(result) == []
    result["metrics"]["syntax_valid"] = False

    assert any(
        "functional_success=true requires" in error
        for error in validate_egma_result(result)
    )
    result = _valid_result()
    result["metrics"]["simulation_complete"] = 1

    assert any(
        "simulation_complete must be true, false, or null" in error
        for error in validate_egma_result(result)
    )


def test_result_contract_recomputes_unsupported_claim_metric() -> None:
    result = _valid_result()
    result["claims"] = [
        {
            "claim_type": "wet_lab_ready",
            "text": "This design is wet-lab ready.",
            "evidence_refs": [],
            "supported": False,
        }
    ]
    result["user_summary"] = "This design is wet-lab ready."
    result["metrics"]["unsupported_claim"] = True
    result["claim_audit"] = audit_egma_claims(
        result["claims"],
        result["evidence_records"],
        result["user_summary"],
    )

    assert validate_egma_result(result) == []
    result["metrics"]["unsupported_claim"] = False

    assert any(
        "unsupported_claim does not match" in error
        for error in validate_egma_result(result)
    )


def test_s2_projection_removes_protected_evidence_recursively() -> None:
    state = {
        "candidate": {"design_id": "candidate-1"},
        "nested": {
            "truth_table_mismatch": {"canary": "TT-CANARY-912"},
            "safe_note": "builder output",
        },
        "verifier_result": {"status": "failed"},
    }

    projected = project_model_visible_state("S2", state)

    assert projected == {
        "candidate": {"design_id": "candidate-1"},
        "nested": {"safe_note": "builder output"},
    }
    assert project_model_visible_state("S3", state) == state


def test_s2_no_leakage_contract_detects_fields_and_canaries() -> None:
    result = _valid_result("S2")
    result["tool_messages"] = [
        {"truth_table_mismatch": {"expected": 1, "actual": 0}}
    ]
    result["prompt_messages"] = ["repair using TT-CANARY-912"]
    result["evidence_canaries"] = ["TT-CANARY-912"]

    errors = validate_evidence_ablation_bundle(result)

    assert any("protected evidence field" in error for error in errors)
    assert any("evidence canary leaked" in error for error in errors)
    assert validate_egma_result(result) == errors


def test_standalone_json_schemas_are_well_formed_and_versioned() -> None:
    task_schema = json.loads(
        (PROTOCOL_DIR / "egma-task-v1.schema.json").read_text(encoding="utf-8")
    )
    result_schema = json.loads(
        (PROTOCOL_DIR / "egma-result-v1.schema.json").read_text(encoding="utf-8")
    )
    topology_schema = json.loads(
        (PROTOCOL_DIR / "egma-topology-v1.schema.json").read_text(encoding="utf-8")
    )
    claim_schema = json.loads(
        (PROTOCOL_DIR / "egma-claim-audit-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    feedback_schema = json.loads(
        (PROTOCOL_DIR / "egma-feedback-trace-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert task_schema["properties"]["schema_version"]["const"] == "egma-task-v1"
    assert result_schema["properties"]["schema_version"]["const"] == "egma-result-v1"
    assert (
        topology_schema["properties"]["schema_version"]["const"]
        == "egma-topology-invariants-v1"
    )
    assert (
        claim_schema["properties"]["schema_version"]["const"]
        == "egma-claim-audit-v1"
    )
    assert (
        feedback_schema["properties"]["policy_version"]["const"]
        == "egma-evidence-feedback-v1"
    )
