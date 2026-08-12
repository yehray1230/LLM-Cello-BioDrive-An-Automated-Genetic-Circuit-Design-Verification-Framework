from __future__ import annotations

import importlib
import os

import application.offline_contract_validation as offline_validation
from application.offline_contract_validation import (
    NEGATIVE_TASK_IDS,
    POSITIVE_TASK_IDS,
    TASK_SET_ID,
    run_offline_contract_validation,
)
from application.services import create_application_services
from benchmark_suite.design_task_dataset import load_design_task_set


def test_frozen_matrix_has_three_positive_and_two_negative_cases() -> None:
    task_set = load_design_task_set(TASK_SET_ID)

    assert tuple(task.task_id for task in task_set.tasks) == (
        *POSITIVE_TASK_IDS,
        *NEGATIVE_TASK_IDS,
    )
    assert task_set.provenance["external_cello_executed"] is False
    assert task_set.provenance["wet_lab_validated"] is False


def test_offline_validation_import_restores_existing_cost_map_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "caller-owned-value")

    importlib.reload(offline_validation)

    assert os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] == "caller-owned-value"


def test_offline_contract_validation_passes_frozen_matrix(tmp_path) -> None:
    services = create_application_services(tmp_path / "data")

    packet = run_offline_contract_validation(
        services,
        output_dir=tmp_path / "evidence",
        timeout_seconds=30.0,
    )

    assert packet["summary"] == {
        "result": "PASS",
        "case_count": 5,
        "passed_case_count": 5,
        "positive_simulation_count": 3,
        "negative_case_count": 2,
    }
    assert packet["execution_boundary"]["external_cello_executed"] is False
    assert packet["execution_boundary"]["agent_orchestration_executed"] is False
    assert packet["execution_boundary"]["validation_scope"] == (
        "software_contract_fixture"
    )
    assert packet["execution_boundary"]["cello_claim_level"] == "not_mapped"
    by_id = {case["task_id"]: case for case in packet["cases"]}
    for task_id in POSITIVE_TASK_IDS:
        assert by_id[task_id]["simulation_status"] == "simulated"
        assert by_id[task_id]["evaluation"]["truth_table_match"] is True
    assert by_id["fsv_ambiguous_sensor_v1"]["execution_status"] == (
        "clarification_returned"
    )
    assert by_id["fsv_ambiguous_sensor_v1"]["simulation_status"] == "skipped"
    assert by_id["fsv_clocked_counter_v1"]["execution_status"] == (
        "blocked_before_execution"
    )
    assert by_id["fsv_clocked_counter_v1"]["simulation_status"] == "not_started"
    assert by_id["fsv_clocked_counter_v1"]["decision_source"] == (
        "fixture_defined_stop_contract"
    )
    assert by_id["fsv_a_and_b_gfp_v1"]["decision_source"] == (
        "deterministic_topology_from_frozen_expected_contract"
    )


def test_offline_contract_result_hash_is_reproducible(tmp_path) -> None:
    first = run_offline_contract_validation(
        create_application_services(tmp_path / "data_a"),
        output_dir=tmp_path / "run_a",
        timeout_seconds=30.0,
    )
    second = run_offline_contract_validation(
        create_application_services(tmp_path / "data_b"),
        output_dir=tmp_path / "run_b",
        timeout_seconds=30.0,
    )

    assert first["stable_result_hash"] == second["stable_result_hash"]
    assert [case["packet_hash"] for case in first["cases"]] == [
        case["packet_hash"] for case in second["cases"]
    ]
