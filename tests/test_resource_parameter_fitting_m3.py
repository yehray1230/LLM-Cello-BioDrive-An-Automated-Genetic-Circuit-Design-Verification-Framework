from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import numpy as np
import pytest

from benchmark_suite.resource_parameter_fitting import (
    PARAMETER_NAMES,
    ResourceFitObservation,
    fit_resource_competition_parameters,
    observations_from_derived_metrics,
    predict_resource_response,
    resource_condition_key,
)
from benchmark_suite.resource_plate_reader import (
    load_plate_map,
    preprocess_plate_reader_csv,
)
from schemas.resource_calibration import (
    calibration_context_from_dict,
    construct_metadata_from_dict,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "resource_calibration"


def _synthetic_observations(
    *,
    demand_coefficient: float = 1.4,
    growth_coupling: float = 0.75,
    seed: int = 41,
) -> tuple[ResourceFitObservation, ...]:
    rng = np.random.default_rng(seed)
    observations = []
    for level_index, demand in enumerate((0.0, 0.25, 0.5, 1.0, 2.0)):
        expected_capacity, expected_growth = predict_resource_response(
            demand,
            demand_coefficient,
            growth_coupling,
        )
        for replicate in range(6):
            observations.append(
                ResourceFitObservation(
                    observation_id=f"synthetic:{level_index}:{replicate}",
                    construct_id=f"load_{level_index}",
                    condition_id=f"demand_{level_index}",
                    biological_replicate=f"bio_{replicate}",
                    demand_index=demand,
                    observed_capacity_loss=float(
                        np.clip(expected_capacity + rng.normal(0.0, 0.012), 0.0, 1.0)
                    ),
                    observed_relative_growth=float(
                        np.clip(expected_growth + rng.normal(0.0, 0.012), 0.0, 1.5)
                    ),
                    capacity_sigma=0.012,
                    growth_sigma=0.012,
                    source_metric_ids=(f"capacity:{level_index}:{replicate}",),
                )
            )
    return tuple(observations)


def _fit(observations=None, *, seed=2026, bootstrap_samples=60):
    return fit_resource_competition_parameters(
        observations or _synthetic_observations(),
        context_id="ctx_synthetic_m3",
        dataset_id="synthetic_recovery_m3",
        bootstrap_samples=bootstrap_samples,
        bootstrap_seed=seed,
    )


def test_m3_joint_log_space_fit_recovers_synthetic_combined_parameters() -> None:
    result = _fit()
    profile = result.profile
    parameters = {item.parameter_name: item for item in profile.parameters}

    assert profile.diagnostics.status == "identifiable"
    assert profile.usable_for_prediction is True
    assert (
        profile.claim_state
        == "fitted_to_synthetic_or_local_dataset_not_heldout_validated"
    )
    assert parameters["aggregate_demand_coefficient"].value == pytest.approx(
        1.4, rel=0.08
    )
    assert parameters["normalized_growth_coupling"].value == pytest.approx(
        0.75, rel=0.08
    )
    assert parameters["aggregate_demand_coefficient"].interval_lower < 1.4
    assert parameters["aggregate_demand_coefficient"].interval_upper > 1.4
    assert parameters["normalized_growth_coupling"].interval_lower < 0.75
    assert parameters["normalized_growth_coupling"].interval_upper > 0.75
    assert profile.diagnostics.bootstrap_successes == 60
    assert profile.diagnostics.jacobian_rank == 2
    assert profile.diagnostics.reason_codes == ()
    assert len(result.fitted_points) == 30


def test_m3_fit_is_reproducible_and_profile_is_immutable() -> None:
    observations = _synthetic_observations()
    first = _fit(observations, seed=99, bootstrap_samples=30)
    second = _fit(tuple(reversed(observations)), seed=99, bootstrap_samples=30)

    assert first.to_dict() == second.to_dict()
    assert first.profile.profile_id == second.profile.profile_id
    json.dumps(first.to_dict(), allow_nan=False)
    with pytest.raises(FrozenInstanceError):
        first.profile.profile_id = "mutated"  # type: ignore[misc]


def test_m3_non_identifiable_design_does_not_publish_confident_intervals() -> None:
    capacity, growth = predict_resource_response(1.0, 1.4, 0.75)
    observations = tuple(
        ResourceFitObservation(
            observation_id=f"single_level:{index}",
            construct_id="single_load",
            condition_id="single_level",
            biological_replicate=f"bio_{index}",
            demand_index=1.0,
            observed_capacity_loss=float(capacity),
            observed_relative_growth=float(growth),
        )
        for index in range(10)
    )

    result = _fit(observations, bootstrap_samples=40)

    assert result.profile.diagnostics.status == "non_identifiable"
    assert result.profile.usable_for_prediction is False
    assert result.profile.claim_state == "non_identifiable"
    assert "insufficient_distinct_positive_demand_levels" in (
        result.profile.diagnostics.reason_codes
    )
    assert "insufficient_demand_dynamic_range" in (
        result.profile.diagnostics.reason_codes
    )
    assert result.profile.diagnostics.bootstrap_successes == 0
    assert all(item.interval_lower is None for item in result.profile.parameters)
    assert result.profile.warnings


def test_m3_profile_reports_bounds_and_default_comparison() -> None:
    result = _fit(bootstrap_samples=20)
    comparisons = {
        item.parameter_name: item for item in result.profile.default_comparison
    }

    assert tuple(item.parameter_name for item in result.profile.parameters) == (
        PARAMETER_NAMES
    )
    assert all(item.log_space for item in result.profile.parameters)
    assert all(
        item.lower_bound < item.value < item.upper_bound
        for item in result.profile.parameters
    )
    assert comparisons["aggregate_demand_coefficient"].default_value == 0.8
    assert comparisons["aggregate_demand_coefficient"].fitted_to_default_ratio > 1.0
    assert result.profile.diagnostics.optimizer == "scipy_least_squares_trf_log_space"


def test_m3_consumes_m2_metrics_but_flags_two_level_fixture_as_insufficient() -> None:
    context = calibration_context_from_dict(
        json.loads((FIXTURE_DIR / "context.json").read_text(encoding="utf-8"))
    )
    constructs = tuple(
        construct_metadata_from_dict(item)
        for item in json.loads(
            (FIXTURE_DIR / "constructs.json").read_text(encoding="utf-8")
        )
    )
    m2_report = preprocess_plate_reader_csv(
        FIXTURE_DIR / "plate_reader_m2_raw.csv",
        context=context,
        constructs=constructs,
        plate_map=load_plate_map(FIXTURE_DIR / "plate_map_m2.json"),
    )
    observations = observations_from_derived_metrics(
        m2_report.derived_metrics,
        demand_index_by_condition={
            resource_condition_key("empty_vector_capacity"): 0.0,
            resource_condition_key("load_reference", "aTc", 10.0, "nM"): 1.0,
        },
        baseline_construct_id="empty_vector_capacity",
    )

    result = fit_resource_competition_parameters(
        observations,
        context_id=context.context_id,
        dataset_id="m2_two_level_fixture",
        bootstrap_samples=20,
        bootstrap_seed=7,
    )

    assert len(observations) == 4
    assert result.profile.diagnostics.status == "non_identifiable"
    assert result.profile.usable_for_prediction is False
    assert "insufficient_distinct_positive_demand_levels" in (
        result.profile.diagnostics.reason_codes
    )
