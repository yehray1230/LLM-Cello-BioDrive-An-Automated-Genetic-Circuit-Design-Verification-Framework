from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from api.dependencies import get_services
from api.main import app
from application.services import create_application_services
from benchmark_suite.resource_model_analysis import (
    run_resource_model_analysis,
    run_resource_morris_screening,
    run_resource_sobol_pilot,
)
from benchmark_suite.resource_parameter_fitting import predict_resource_response


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


def _stored_workflow() -> dict:
    training_constructs = [f"train_{index}" for index in range(4)]
    observations = []
    for index, demand in enumerate((0.2, 0.5, 1.0, 2.0)):
        observations.extend(
            _observation(
                f"train:{index}:{replicate}",
                training_constructs[index],
                demand,
                f"bio_{replicate}",
            )
            for replicate in range(5)
        )
    observations.extend(
        _observation(
            f"heldout:{index}",
            "heldout_rbs",
            demand,
            f"heldout_{index}",
        )
        for index, demand in enumerate((0.3, 0.7, 1.4, 2.5))
    )
    return {
        "workflow_id": "resource_m6_fixture",
        "status": "completed",
        "dataset_id": "synthetic_training_m6",
        "validation_dataset_id": "synthetic_heldout_m6",
        "context": {"context_id": "ctx_m6_synthetic"},
        "validation_split": {
            "split_id": "m6_rbs_holdout",
            "training_construct_ids": training_constructs,
            "validation_construct_ids": ["heldout_rbs"],
        },
        "observations": observations,
        "validation": {"decision": "go"},
        "claim_boundary": {
            "claim_state": "calibrated_comparative_predictor_for_stated_context",
            "decision": "go",
            "statement": "Synthetic M6 fixture only.",
        },
        "parameter_role_summary": {
            "observed": len(observations) * 2,
            "calibrated": 2,
            "defaulted": 4,
            "inferred": len(observations),
        },
        "provenance": {
            "context_fingerprint": "0" * 64,
            "observation_ids": [item["observation_id"] for item in observations],
            "source_metric_ids": [item["source_metric_ids"][0] for item in observations],
            "raw_trace_count": 0,
        },
        "stages": {},
        "preprocessing": {"status": "not_run_prederived_input"},
        "warnings": ["Synthetic M6 fixture only."],
        "fit": None,
        "candidate_comparison": [],
        "input_mode": "derived_observations",
        "dominant_layer": "heldout_gates_passed",
    }


def _config() -> dict:
    return {
        "morris_trajectories": 8,
        "morris_levels": 6,
        "sobol_sample_count": 128,
        "fit_bootstrap_samples": 10,
        "random_seed": 616,
    }


def test_m6_morris_and_sobol_pilots_are_reproducible_and_bounded() -> None:
    demands = (0.2, 0.5, 1.0, 2.0)
    first = run_resource_morris_screening(
        demands, trajectories=8, levels=6, seed=17
    )
    second = run_resource_morris_screening(
        demands, trajectories=8, levels=6, seed=17
    )
    sobol = run_resource_sobol_pilot(demands, sample_count=128, seed=18)

    assert first == second
    assert first["status"] == "screening_only"
    assert len(first["ranking"]) == 2
    assert sobol["status"] == "pilot_not_release_grade"
    assert sobol["model_evaluation_count"] == 512
    for output in sobol["indices"].values():
        for item in output.values():
            assert 0.0 <= item["total_order_clipped"] <= 1.0

    with pytest.raises(ValueError, match="exactly"):
        run_resource_morris_screening(
            demands,
            parameter_ranges={"aggregate_demand_coefficient": (0.2, 3.0)},
        )


def test_m6_analysis_compares_models_without_promoting_richer_family() -> None:
    analysis = run_resource_model_analysis(_stored_workflow(), _config())

    assert analysis["status"] == "completed"
    assert analysis["model_comparison"]["best_model"] == (
        "resource_competition_fit_v0.1"
    )
    assert analysis["recommendation"]["automatic_action"] is False
    assert analysis["sbml_biocrnpyler_gate"]["decision"] == "no_go"
    assert analysis["automatic_model_promotion"]["allowed"] is False
    assert analysis["parameter_range_source"] == "fixed_research_preview_defaults"


def test_m6_api_persists_analysis_and_web_renders_readiness_gate(
    tmp_path: Path,
) -> None:
    services = create_application_services(tmp_path / "api_data")
    repository = services.evaluations.resource_calibration_repository
    assert repository is not None
    repository.save("resource_m6_fixture", _stored_workflow())
    app.dependency_overrides[get_services] = lambda: services
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/resource-calibrations/resource_m6_fixture/model-analysis",
                json=_config(),
            )
            client.cookies.set("lang", "en")
            page = client.get(
                "/web/resource-calibrations/resource_m6_fixture"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["data"]["sobol_pilot"]["base_sample_count"] == 128
    persisted = repository.get("resource_m6_fixture")
    assert persisted is not None and persisted["model_analysis"]
    assert page.status_code == 200
    assert "Morris screening" in page.text
    assert "Frozen-holdout model comparison" in page.text
    assert "SBML/BioCRNpyler gate" in page.text
    assert "no_go" in page.text
