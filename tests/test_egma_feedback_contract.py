from __future__ import annotations

from copy import deepcopy

import pytest

from benchmark_suite.egma_feedback import (
    AGENT_REPAIR_BUDGET,
    DIRECT_BUDGET,
    FEEDBACK_POLICY_VERSION,
    project_feedback_items,
    validate_feedback_trace,
    validate_s2_s3_parity,
)


def _record(
    evidence_id: str,
    category: str,
    *,
    status: str = "failed",
    metadata: dict | None = None,
) -> dict:
    return {
        "evidence_id": evidence_id,
        "category": category,
        "status": status,
        "comparison_eligible": True,
        "artifact_ref": f"artifact://{evidence_id}",
        "metadata": metadata or {},
    }


def _trace(
    system_id: str,
    iterations: list[dict],
    stopped_reason: str,
) -> dict:
    budget = DIRECT_BUDGET if system_id in {"S0", "S1"} else AGENT_REPAIR_BUDGET
    return {
        "policy_version": FEEDBACK_POLICY_VERSION,
        "system_id": system_id,
        "budget": budget.to_dict(),
        "iterations": iterations,
        "stopped_reason": stopped_reason,
    }


def _iteration(
    index: int,
    *,
    formal_success: bool,
    ranking_feedback: list[dict] | None = None,
    repair_feedback: list[dict] | None = None,
) -> dict:
    return {
        "iteration_index": index,
        "candidate_id": f"candidate-{index}",
        "formal_success": formal_success,
        "ranking_feedback": ranking_feedback or [],
        "repair_feedback": repair_feedback or [],
    }


def _parity_config(system_id: str, enabled: bool) -> dict:
    return {
        "system_id": system_id,
        "evidence_feedback_enabled": enabled,
        "base_model": "frozen-model-v1",
        "provider_version": "frozen-provider-v1",
        "temperature": 0.0,
        "seed": 1729,
        "timeout_seconds": 120,
        "max_output_tokens": 4096,
        "provider_retry_count": 1,
        "tool_allowlist": ["formal_evaluator", "artifact_writer"],
        "repair_budget": AGENT_REPAIR_BUDGET.to_dict(),
    }


def test_s3_projection_is_deterministic_and_filters_non_allowlisted_payload() -> None:
    records = [
        _record(
            "truth",
            "truth_table_check",
            metadata={
                "input_assignment": {"A": 1, "B": 0},
                "expected_output": 1,
                "actual_output": 0,
                "secret_answer_key": "must-not-project",
            },
        ),
        _record(
            "simulation",
            "simulation_trace",
            metadata={
                "missing_fields": ["time"],
                "reason_codes": ["TRACE_INCOMPLETE"],
                "raw_trace": [1, 2, 3],
            },
        ),
        _record(
            "passed-syntax",
            "formal_syntax_check",
            status="passed",
            metadata={"line": 1},
        ),
        _record(
            "external",
            "external_cello_mapping",
            metadata={"mapping_mode": "external"},
        ),
    ]

    first = project_feedback_items("S3", records, candidate_id="candidate-0")
    second = project_feedback_items("S3", records, candidate_id="candidate-0")

    assert first == second
    assert [item["category"] for item in first] == [
        "truth_table_mismatch",
        "simulation_incomplete",
    ]
    assert first[0]["payload"] == {
        "input_assignment": {"A": 1, "B": 0},
        "expected_output": 1,
        "actual_output": 0,
    }
    assert "raw_trace" not in first[1]["payload"]


def test_s3_projection_applies_the_eight_item_iteration_cap() -> None:
    records = [
        _record(
            f"syntax-{index:02d}",
            "formal_syntax_check",
            metadata={"line": index, "column": 0, "reason_codes": ["INVALID"]},
        )
        for index in range(10)
    ]

    items = project_feedback_items("S3", records, candidate_id="candidate-0")

    assert len(items) == 8
    assert [item["evidence_ref"] for item in items] == [
        f"syntax-{index:02d}" for index in range(8)
    ]


@pytest.mark.parametrize("system_id", ["S0", "S1", "S2"])
def test_non_s3_systems_receive_no_projected_evidence(
    system_id: str,
) -> None:
    records = [
        _record(
            "truth",
            "truth_table_check",
            metadata={"expected_output": 1, "actual_output": 0},
        )
    ]

    assert (
        project_feedback_items(system_id, records, candidate_id="candidate-0")
        == []
    )


def test_valid_s3_trace_allows_two_repairs_then_success() -> None:
    items = project_feedback_items(
        "S3",
        [
            _record(
                "truth",
                "truth_table_check",
                metadata={
                    "input_assignment": {"A": 1, "B": 0},
                    "expected_output": 1,
                    "actual_output": 0,
                },
            )
        ],
        candidate_id="candidate-0",
    )
    trace = _trace(
        "S3",
        [
            _iteration(
                0,
                formal_success=False,
                ranking_feedback=items,
                repair_feedback=deepcopy(items),
            ),
            _iteration(1, formal_success=False),
            _iteration(2, formal_success=True),
        ],
        "formal_success",
    )

    assert validate_feedback_trace(trace) == []


def test_trace_rejects_candidate_and_repair_budget_overrun() -> None:
    trace = _trace(
        "S3",
        [
            _iteration(0, formal_success=False),
            _iteration(1, formal_success=False),
            _iteration(2, formal_success=False),
            _iteration(3, formal_success=False),
        ],
        "budget_exhausted",
    )

    errors = validate_feedback_trace(trace)

    assert "candidate evaluation budget exceeded." in errors
    assert "repair iteration budget exceeded." in errors


def test_trace_rejects_feedback_field_and_payload_bypass() -> None:
    item = project_feedback_items(
        "S3",
        [
            _record(
                "truth",
                "truth_table_check",
                metadata={
                    "input_assignment": {"A": 1, "B": 0},
                    "expected_output": 1,
                    "actual_output": 0,
                },
            )
        ],
        candidate_id="candidate-0",
    )[0]
    item["raw_answer_key"] = "forbidden"
    item["payload"]["raw_truth_table"] = [{"A": 0, "Y": 1}]
    trace = _trace(
        "S3",
        [_iteration(0, formal_success=False, repair_feedback=[item])],
        "nonrepairable",
    )

    errors = validate_feedback_trace(trace)

    assert any("fields do not match" in error for error in errors)
    assert any("non-allowlisted fields" in error for error in errors)


def test_trace_rejects_noncanonical_id_and_typed_payload_bypass() -> None:
    item = project_feedback_items(
        "S3",
        [
            _record(
                "truth",
                "truth_table_check",
                metadata={
                    "input_assignment": {"A": 1, "B": 0},
                    "expected_output": 1,
                    "actual_output": 0,
                },
            )
        ],
        candidate_id="candidate-0",
    )[0]
    item["feedback_id"] = "forged"
    item["payload"]["expected_output"] = "one"
    trace = _trace(
        "S3",
        [_iteration(0, formal_success=False, repair_feedback=[item])],
        "nonrepairable",
    )

    errors = validate_feedback_trace(trace)

    assert any("feedback_id is not canonical" in error for error in errors)
    assert any("expected_output must be binary" in error for error in errors)


def test_s2_trace_rejects_feedback_and_canary_bypass() -> None:
    item = project_feedback_items(
        "S3",
        [
            _record(
                "truth",
                "truth_table_check",
                metadata={
                    "input_assignment": {"A": 1, "B": 0},
                    "expected_output": 1,
                    "actual_output": 0,
                },
            )
        ],
        candidate_id="candidate-0",
    )[0]
    trace = _trace(
        "S2",
        [_iteration(0, formal_success=False, repair_feedback=[item])],
        "nonrepairable",
    )
    trace["prompt_messages"] = ["hidden EGMA-CANARY-42"]
    trace["evidence_canaries"] = ["EGMA-CANARY-42"]

    errors = validate_feedback_trace(trace)

    assert any("forbidden for S2" in error for error in errors)
    assert any("evidence canary leaked" in error for error in errors)


def test_s2_s3_parity_allows_only_the_named_ablation() -> None:
    s2 = _parity_config("S2", False)
    s3 = _parity_config("S3", True)

    assert validate_s2_s3_parity(s2, s3) == []
    s3["max_output_tokens"] = 8192

    assert validate_s2_s3_parity(s2, s3) == [
        "S2/S3 parity mismatch: max_output_tokens."
    ]


def test_budget_exhaustion_requires_exactly_three_failed_candidates() -> None:
    valid = _trace(
        "S3",
        [
            _iteration(0, formal_success=False),
            _iteration(1, formal_success=False),
            _iteration(2, formal_success=False),
        ],
        "budget_exhausted",
    )
    early = deepcopy(valid)
    early["iterations"].pop()

    assert validate_feedback_trace(valid) == []
    assert "budget_exhausted requires the full candidate evaluation budget." in (
        validate_feedback_trace(early)
    )


def test_trace_cannot_continue_after_formal_success() -> None:
    trace = _trace(
        "S3",
        [
            _iteration(0, formal_success=True),
            _iteration(1, formal_success=False),
        ],
        "formal_success",
    )

    assert "feedback trace continues after formal success." in (
        validate_feedback_trace(trace)
    )


def test_trace_candidate_ids_are_unique() -> None:
    trace = _trace(
        "S3",
        [
            _iteration(0, formal_success=False),
            _iteration(1, formal_success=True),
        ],
        "formal_success",
    )
    trace["iterations"][1]["candidate_id"] = "candidate-0"

    assert "feedback trace candidate_id values must be unique." in (
        validate_feedback_trace(trace)
    )
