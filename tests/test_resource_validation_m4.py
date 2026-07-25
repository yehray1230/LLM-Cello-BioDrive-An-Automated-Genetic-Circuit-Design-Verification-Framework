from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from benchmark_suite.resource_parameter_fitting import (
    ResourceFitObservation,
    fit_resource_competition_parameters,
    predict_resource_response,
)
from benchmark_suite.resource_validation import validate_resource_profile_heldout
from schemas.resource_calibration import ValidationSplit


CONTEXT_ID = "ctx_synthetic_m4"
TRAINING_DATASET_ID = "synthetic_training_m4"
VALIDATION_DATASET_ID = "synthetic_heldout_m4"


def _training_observations(seed: int = 101) -> tuple[ResourceFitObservation, ...]:
    rng = np.random.default_rng(seed)
    observations = []
    for level_index, demand in enumerate((0.2, 0.5, 1.0, 2.0)):
        capacity, growth = predict_resource_response(demand, 1.4, 0.75)
        for replicate in range(5):
            observations.append(
                ResourceFitObservation(
                    observation_id=f"train:{level_index}:{replicate}",
                    construct_id=f"train_construct_{level_index}",
                    condition_id=f"train_condition_{level_index}",
                    biological_replicate=f"bio_{replicate}",
                    demand_index=demand,
                    observed_capacity_loss=float(
                        np.clip(capacity + rng.normal(0.0, 0.008), 0.0, 1.0)
                    ),
                    observed_relative_growth=float(
                        np.clip(growth + rng.normal(0.0, 0.008), 0.0, 1.5)
                    ),
                    capacity_sigma=0.015,
                    growth_sigma=0.015,
                )
            )
    return tuple(observations)


def _validation_observations(seed: int = 202) -> tuple[ResourceFitObservation, ...]:
    rng = np.random.default_rng(seed)
    observations = []
    for index, demand in enumerate((0.3, 0.7, 1.4, 2.5)):
        capacity, growth = predict_resource_response(demand, 1.4, 0.75)
        observations.append(
            ResourceFitObservation(
                observation_id=f"heldout:{index}",
                construct_id="heldout_rbs",
                condition_id=f"heldout_condition_{index}",
                biological_replicate=f"bio_{index}",
                demand_index=demand,
                observed_capacity_loss=float(
                    np.clip(capacity + rng.normal(0.0, 0.008), 0.0, 1.0)
                ),
                observed_relative_growth=float(
                    np.clip(growth + rng.normal(0.0, 0.008), 0.0, 1.5)
                ),
                capacity_sigma=0.015,
                growth_sigma=0.015,
            )
        )
    return tuple(observations)


def _split() -> ValidationSplit:
    return ValidationSplit(
        split_id="m4_rbs_holdout_v1",
        strategy="rbs_holdout",
        training_construct_ids=tuple(f"train_construct_{index}" for index in range(4)),
        validation_construct_ids=("heldout_rbs",),
        grouping_key="rbs_id",
        rationale="Synthetic frozen RBS holdout for M4 validation tests.",
        random_seed=404,
    )


def _profile(training=None):
    training = training or _training_observations()
    return fit_resource_competition_parameters(
        training,
        context_id=CONTEXT_ID,
        dataset_id=TRAINING_DATASET_ID,
        bootstrap_samples=40,
        bootstrap_seed=303,
    ).profile


def _output_maps(validation):
    observed = {
        item.observation_id: 1.0 + 0.50 * item.demand_index for item in validation
    }
    predicted = {
        item.observation_id: 1.0 + 0.48 * item.demand_index for item in validation
    }
    return observed, predicted


def _validate(*, training=None, validation=None, include_output=True):
    training = training or _training_observations()
    validation = validation or _validation_observations()
    observed_output, predicted_output = _output_maps(validation)
    return validate_resource_profile_heldout(
        profile=_profile(training),
        split=_split(),
        training_observations=training,
        validation_observations=validation,
        validation_context_id=CONTEXT_ID,
        validation_dataset_id=VALIDATION_DATASET_ID,
        observed_output_fold_changes=observed_output if include_output else None,
        predicted_output_fold_changes=predicted_output if include_output else None,
        output_prediction_model_id=(
            "synthetic_output_predictor_v1" if include_output else ""
        ),
    )


def test_m4_frozen_heldout_path_passes_predeclared_synthetic_gates() -> None:
    report = _validate()
    gates = {gate.gate_name: gate for gate in report.gates}

    assert report.decision == "go"
    assert report.claim_state == "calibrated_comparative_predictor_for_stated_context"
    assert all(gate.passed for gate in report.gates)
    assert report.metrics.burden_spearman >= 0.70
    assert report.metrics.relative_growth_mape <= 0.20
    assert report.metrics.output_direction_accuracy == 1.0
    assert report.metrics.combined_interval_coverage >= 0.80
    assert (
        report.metrics.relative_growth_mape
        < report.metrics.simple_baseline_relative_growth_mape
    )
    assert gates["prediction_interval_coverage"].status == "pass"
    assert report.output_prediction_model_id == "synthetic_output_predictor_v1"


def test_m4_report_is_reproducible_and_validation_does_not_refit() -> None:
    first = _validate()
    second = _validate()

    assert first.to_dict() == second.to_dict()
    assert first.report_id == second.report_id
    assert first.profile_id == _profile().profile_id
    assert all(
        prediction.observation_id.startswith("heldout:")
        for prediction in first.predictions
    )


def test_m4_missing_output_predictor_produces_explainable_no_go() -> None:
    report = _validate(include_output=False)
    gate = next(
        item
        for item in report.gates
        if item.gate_name == "output_fold_change_direction"
    )

    assert report.decision == "no_go"
    assert report.claim_state == "fitted_to_training_dataset_heldout_gates_not_met"
    assert gate.status == "not_evaluable"
    assert gate.reason_code == "output_prediction_missing"
    assert any("not evaluable" in warning for warning in report.warnings)


def test_m4_failed_ranking_growth_and_output_gates_return_no_go() -> None:
    validation = tuple(
        replace(
            item,
            observed_capacity_loss=1.0 - item.observed_capacity_loss,
            observed_relative_growth=0.25,
        )
        for item in _validation_observations()
    )
    training = _training_observations()
    observed_output = {
        item.observation_id: 1.0 + 0.5 * item.demand_index for item in validation
    }
    predicted_output = {
        item.observation_id: max(0.1, 1.0 - 0.5 * item.demand_index)
        for item in validation
    }
    report = validate_resource_profile_heldout(
        profile=_profile(training),
        split=_split(),
        training_observations=training,
        validation_observations=validation,
        validation_context_id=CONTEXT_ID,
        validation_dataset_id="synthetic_bad_holdout_m4",
        observed_output_fold_changes=observed_output,
        predicted_output_fold_changes=predicted_output,
        output_prediction_model_id="intentionally_bad_output_predictor",
    )
    failed = {gate.gate_name for gate in report.gates if not gate.passed}

    assert report.decision == "no_go"
    assert "burden_ranking" in failed
    assert "relative_growth_error" in failed
    assert "output_fold_change_direction" in failed


def test_m4_blocks_training_leakage_fingerprint_and_context_mismatch() -> None:
    training = _training_observations()
    validation = _validation_observations()
    profile = _profile(training)
    changed_training = (
        replace(
            training[0],
            observed_capacity_loss=min(1.0, training[0].observed_capacity_loss + 0.05),
        ),
        *training[1:],
    )

    with pytest.raises(ValueError, match="fingerprint"):
        validate_resource_profile_heldout(
            profile=profile,
            split=_split(),
            training_observations=changed_training,
            validation_observations=validation,
            validation_context_id=CONTEXT_ID,
            validation_dataset_id=VALIDATION_DATASET_ID,
        )

    with pytest.raises(ValueError, match="context does not match"):
        validate_resource_profile_heldout(
            profile=profile,
            split=_split(),
            training_observations=training,
            validation_observations=validation,
            validation_context_id="different_context",
            validation_dataset_id=VALIDATION_DATASET_ID,
        )

    leaked_validation = (
        replace(validation[0], observation_id=training[0].observation_id),
        *validation[1:],
    )
    with pytest.raises(ValueError, match="leakage"):
        validate_resource_profile_heldout(
            profile=profile,
            split=_split(),
            training_observations=training,
            validation_observations=leaked_validation,
            validation_context_id=CONTEXT_ID,
            validation_dataset_id=VALIDATION_DATASET_ID,
        )


def test_m4_non_identifiable_profile_cannot_be_promoted_by_good_holdout_data() -> None:
    capacity, growth = predict_resource_response(1.0, 1.4, 0.75)
    training = tuple(
        ResourceFitObservation(
            observation_id=f"single_train:{index}",
            construct_id="train_single",
            condition_id="single_demand",
            biological_replicate=f"bio_{index}",
            demand_index=1.0,
            observed_capacity_loss=float(capacity),
            observed_relative_growth=float(growth),
        )
        for index in range(10)
    )
    profile = fit_resource_competition_parameters(
        training,
        context_id=CONTEXT_ID,
        dataset_id="non_identifiable_training_m4",
        bootstrap_samples=20,
    ).profile
    validation = _validation_observations()
    observed_output, predicted_output = _output_maps(validation)
    split = ValidationSplit(
        split_id="m4_non_identifiable_holdout",
        strategy="rbs_holdout",
        training_construct_ids=("train_single",),
        validation_construct_ids=("heldout_rbs",),
        grouping_key="rbs_id",
        rationale="Ensure held-out data cannot rescue a non-identifiable fit.",
    )

    report = validate_resource_profile_heldout(
        profile=profile,
        split=split,
        training_observations=training,
        validation_observations=validation,
        validation_context_id=CONTEXT_ID,
        validation_dataset_id="heldout_for_non_identifiable_profile",
        observed_output_fold_changes=observed_output,
        predicted_output_fold_changes=predicted_output,
        output_prediction_model_id="synthetic_output_predictor_v1",
    )
    gates = {gate.gate_name: gate for gate in report.gates}

    assert report.decision == "no_go"
    assert report.claim_state == "non_identifiable_profile_no_go"
    assert gates["identifiable_profile"].passed is False
    assert gates["prediction_interval_coverage"].status == "not_evaluable"
