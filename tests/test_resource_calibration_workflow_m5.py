from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.dependencies import get_services
from api.main import app
from application.services import create_application_services
from benchmark_suite.resource_parameter_fitting import predict_resource_response


def _context() -> dict:
    return {
        "context_id": "ctx_m5_synthetic",
        "host_organism": "Escherichia coli",
        "strain": "MG1655",
        "medium": "M9 glucose",
        "temperature_c": 37.0,
        "aeration": "shaking",
        "culture_format": "96-well plate",
        "working_volume_ul": 150.0,
        "instrument": "synthetic_test_fixture",
        "gain_settings": {"capacity": 50, "output": 50},
        "growth_phase": "exponential",
        "capacity_reporter": "constitutive GFP",
        "output_reporter": "RFP",
        "protocol_version": "synthetic-m5-v1",
        "metadata": {"data_boundary": "synthetic_test_only"},
    }


def _construct(construct_id: str, rbs_id: str) -> dict:
    return {
        "construct_id": construct_id,
        "backbone_id": "p15a_test",
        "origin": "p15a",
        "promoter_id": "p_const",
        "rbs_id": rbs_id,
        "cds_id": "m5_reporter",
        "terminator_id": "t_test",
        "copy_number_source": "fixed_synthetic_assumption",
        "sequence_available": False,
        "metadata": {"data_boundary": "synthetic_test_only"},
    }


def _observation(
    observation_id: str,
    construct_id: str,
    demand: float,
    replicate: str,
) -> dict:
    capacity, growth = predict_resource_response(demand, 1.4, 0.75)
    return {
        "observation_id": observation_id,
        "construct_id": construct_id,
        "condition_id": f"demand_{demand}",
        "biological_replicate": replicate,
        "demand_index": demand,
        "observed_capacity_loss": float(capacity),
        "observed_relative_growth": float(growth),
        "capacity_sigma": 0.015,
        "growth_sigma": 0.015,
        "source_metric_ids": [f"metric:{observation_id}"],
    }


def _payload() -> dict:
    training_constructs = [f"train_{index}" for index in range(4)]
    observations = []
    for index, demand in enumerate((0.2, 0.5, 1.0, 2.0)):
        for replicate in range(5):
            observations.append(
                _observation(
                    f"train:{index}:{replicate}",
                    training_constructs[index],
                    demand,
                    f"bio_{replicate}",
                )
            )
    heldout_ids = []
    for index, demand in enumerate((0.3, 0.7, 1.4, 2.5)):
        observation_id = f"heldout:{index}"
        heldout_ids.append(observation_id)
        observations.append(
            _observation(observation_id, "heldout_rbs", demand, f"heldout_{index}")
        )
    return {
        "input_mode": "derived_observations",
        "dataset_id": "synthetic_training_m5",
        "validation_dataset_id": "synthetic_heldout_m5",
        "context": _context(),
        "constructs": [
            *[
                _construct(construct_id, f"rbs_train_{index}")
                for index, construct_id in enumerate(training_constructs)
            ],
            _construct("heldout_rbs", "rbs_heldout"),
        ],
        "validation_split": {
            "split_id": "m5_rbs_holdout_v1",
            "strategy": "rbs_holdout",
            "training_construct_ids": training_constructs,
            "validation_construct_ids": ["heldout_rbs"],
            "grouping_key": "rbs_id",
            "rationale": "Frozen synthetic split for M5 workflow contract tests.",
            "random_seed": 505,
            "frozen": True,
        },
        "observations": observations,
        "bootstrap_samples": 10,
        "bootstrap_seed": 606,
        "observed_output_fold_changes": {
            observation_id: 1.0 + 0.50 * observations[-4 + index]["demand_index"]
            for index, observation_id in enumerate(heldout_ids)
        },
        "predicted_output_fold_changes": {
            observation_id: 1.0 + 0.48 * observations[-4 + index]["demand_index"]
            for index, observation_id in enumerate(heldout_ids)
        },
        "output_prediction_model_id": "synthetic_output_predictor_m5",
    }


def test_m5_service_persists_traceable_claim_safe_workflow(tmp_path: Path) -> None:
    services = create_application_services(tmp_path / "api_data")

    result = services.evaluations.create_resource_calibration_workflow(_payload())

    assert result["status"] == "completed"
    assert result["validation"]["decision"] == "go"
    assert result["dominant_layer"] == "heldout_gates_passed"
    assert result["automatic_application"]["allowed"] is False
    assert result["preprocessing"]["status"] == "not_run_prederived_input"
    assert result["provenance"]["observation_ids"]
    assert result["provenance"]["source_metric_ids"]
    assert result["parameter_role_summary"]["calibrated"] == 2
    persisted = services.evaluations.resource_calibration_workflow(
        result["workflow_id"]
    )
    assert persisted == result


def test_m5_api_create_list_detail_and_validate_input(tmp_path: Path) -> None:
    services = create_application_services(tmp_path / "api_data")
    app.dependency_overrides[get_services] = lambda: services
    try:
        with TestClient(app) as client:
            created = client.post("/api/v1/resource-calibrations", json=_payload())
            assert created.status_code == 201
            workflow = created.json()["data"]
            detail = client.get(
                f"/api/v1/resource-calibrations/{workflow['workflow_id']}"
            )
            listing = client.get("/api/v1/resource-calibrations")
            invalid = client.post(
                "/api/v1/resource-calibrations",
                json={**_payload(), "observations": []},
            )
    finally:
        app.dependency_overrides.clear()

    assert detail.status_code == 200
    assert detail.json()["data"]["claim_boundary"]["decision"] == "go"
    assert listing.status_code == 200
    assert listing.json()["data"]["count"] == 1
    assert invalid.status_code == 422


def test_m5_web_upload_renders_diagnostics_and_claim_boundary(
    tmp_path: Path,
) -> None:
    services = create_application_services(tmp_path / "api_data")
    app.dependency_overrides[get_services] = lambda: services
    try:
        with TestClient(app) as client:
            client.cookies.set("lang", "en")
            response = client.post(
                "/web/resource-calibrations",
                files={
                    "bundle_file": (
                        "m5.json",
                        json.dumps(_payload()),
                        "application/json",
                    )
                },
                follow_redirects=True,
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "Resource Competition Calibration Diagnostics" in response.text
    assert "heldout_gates_passed" in response.text
    assert "never updates production simulation parameters automatically" in response.text
    assert "synthetic_output_predictor_m5" not in response.text
