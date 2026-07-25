from __future__ import annotations

from schemas.workflow_evidence import (
    ODETraceEvidenceV1,
    SimulationEvidenceV1,
    WORKFLOW_EVIDENCE_CONTRACT_VERSION,
    WorkflowEvidenceEnvelopeV1,
    is_valid_ode_trace,
    project_ode_trace_rows,
)


def test_ode_trace_contract_validates_existing_finite_trace() -> None:
    trace = ODETraceEvidenceV1.from_value(
        {
            "time": [0, 1.0, 2],
            "output_protein": [0.0, 0.25, 0.9],
        }
    )
    assert trace.is_valid is True
    assert trace.sample_count == 3
    assert trace.time == (0.0, 1.0, 2.0)
    assert trace.output_protein == (0.0, 0.25, 0.9)
    assert trace.errors == ()


def test_ode_trace_contract_fails_closed_on_malformed_truthy_trace() -> None:
    trace = ODETraceEvidenceV1.from_value(
        {
            "time": ["not-a-number"],
            "output_protein": [],
        }
    )
    assert trace.present is True
    assert trace.is_valid is False
    assert trace.sample_count == 0
    assert trace.errors == (
        "missing_ode_trace_output_protein",
        "ode_trace_requires_finite_numeric_values",
    )


def test_canonical_ode_trace_predicate_rejects_non_finite_and_reversed_time() -> None:
    assert is_valid_ode_trace(
        {"time": [0.0, 1.0], "output_protein": [0.0, 1.0]}
    )
    assert not is_valid_ode_trace(
        {"time": [0.0, 1.0], "output_protein": [0.0, float("nan")]}
    )
    assert not is_valid_ode_trace(
        {"time": [1.0, 0.0], "output_protein": [0.0, 1.0]}
    )


def test_ode_trace_projection_keeps_only_aligned_finite_optional_series() -> None:
    rows = project_ode_trace_rows(
        {
            "time": [0.0, 1.0],
            "output_protein": [0.0, 2.0],
            "total_mrna": [1.0, 3.0],
            "total_protein": [1.0],
            "rnap_occupancy": [0.1, float("inf")],
            "ribosome_occupancy": [0.2, "invalid"],
        }
    )

    assert rows == [
        {"time": 0.0, "output_protein": 0.0, "total_mrna": 1.0},
        {"time": 1.0, "output_protein": 2.0, "total_mrna": 3.0},
    ]


def test_simulation_contract_separates_combinational_and_temporal_evidence() -> None:
    scenario_only = SimulationEvidenceV1.from_topology(
        {
            "simulation_result": {
                "status": "simulated",
                "scenario_results": [{"scenario": "fixture"}],
            }
        }
    )
    assert scenario_only.combinational_complete is True
    assert scenario_only.temporal_complete is False

    trace_backed = SimulationEvidenceV1.from_topology(
        {
            "simulation_result": {"status": "simulated"},
            "ode_trace": {
                "time": [0.0, 1.0],
                "output_protein": [0.0, 1.0],
            },
        }
    )
    assert trace_backed.combinational_complete is True
    assert trace_backed.temporal_complete is True


def test_workflow_envelope_normalizes_legacy_service_payload_without_aliasing() -> None:
    payload = {
        "status": "completed",
        "summary": {"is_completed": True, "best_topology": {"score": 0.8}},
        "artifacts": {"summary": "summary.json"},
        "error": None,
    }
    envelope = WorkflowEvidenceEnvelopeV1.from_service_payload(payload)
    normalized = envelope.to_benchmark_payload()

    assert WORKFLOW_EVIDENCE_CONTRACT_VERSION == "workflow-evidence-v1"
    assert envelope.status == "success"
    assert envelope.service_status == "completed"
    assert envelope.best_topology == {"score": 0.8}
    assert normalized["data"]["artifacts"] == {"summary": "summary.json"}
    normalized["data"]["best_topology"]["score"] = 0.1
    assert payload["summary"]["best_topology"]["score"] == 0.8


def test_workflow_envelope_preserves_existing_standard_payload() -> None:
    payload = {
        "status": "success",
        "data": {"best_topology": {"mapping_status": "unmapped"}},
        "custom": {"retained": True},
    }
    envelope = WorkflowEvidenceEnvelopeV1.from_service_payload(payload)
    assert envelope.already_standard is True
    assert envelope.to_benchmark_payload() == payload
