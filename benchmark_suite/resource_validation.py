from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from benchmark_suite.resource_parameter_fitting import (
    ResourceCalibrationProfile,
    ResourceFitObservation,
    predict_resource_response,
    resource_observation_fingerprint,
)
from schemas.resource_calibration import ValidationSplit


RESOURCE_VALIDATION_VERSION = "heldout_validation_v0.1"


@dataclass(frozen=True)
class HeldOutGateThresholds:
    minimum_heldout_observations: int = 3
    minimum_burden_spearman: float = 0.70
    maximum_relative_growth_mape: float = 0.20
    minimum_output_direction_accuracy: float = 0.80
    minimum_prediction_interval_coverage: float = 0.80
    require_simple_baseline_improvement: bool = True
    prediction_interval_sigma_multiplier: float = 1.96
    fold_change_neutral_tolerance: float = 0.02
    gate_version: str = "resource_heldout_gates_v0.1"

    def __post_init__(self) -> None:
        if self.minimum_heldout_observations < 3:
            raise ValueError("minimum_heldout_observations must be at least 3.")
        for name in (
            "minimum_burden_spearman",
            "maximum_relative_growth_mape",
            "minimum_output_direction_accuracy",
            "minimum_prediction_interval_coverage",
            "prediction_interval_sigma_multiplier",
            "fold_change_neutral_tolerance",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative.")
        for name in (
            "minimum_output_direction_accuracy",
            "minimum_prediction_interval_coverage",
        ):
            if float(getattr(self, name)) > 1.0:
                raise ValueError(f"{name} cannot exceed 1.0.")
        if self.minimum_burden_spearman > 1.0:
            raise ValueError("minimum_burden_spearman cannot exceed 1.0.")
        if not self.gate_version.strip():
            raise ValueError("gate_version must be non-empty.")


@dataclass(frozen=True)
class ValidationGateResult:
    gate_name: str
    status: str
    passed: bool
    observed_value: float | int | str | None
    threshold: float | int | str | None
    reason_code: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HeldOutPrediction:
    observation_id: str
    construct_id: str
    demand_index: float
    observed_capacity_loss: float
    predicted_capacity_loss: float
    capacity_interval_lower: float | None
    capacity_interval_upper: float | None
    observed_relative_growth: float
    predicted_relative_growth: float
    growth_interval_lower: float | None
    growth_interval_upper: float | None
    observed_output_fold_change: float | None
    predicted_output_fold_change: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HeldOutValidationMetrics:
    heldout_observation_count: int
    heldout_construct_count: int
    distinct_heldout_demand_levels: int
    burden_spearman: float | None
    relative_growth_mape: float
    simple_baseline_relative_growth_mape: float
    output_direction_accuracy: float | None
    capacity_interval_coverage: float | None
    growth_interval_coverage: float | None
    combined_interval_coverage: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HeldOutValidationReport:
    report_id: str
    profile_id: str
    split_id: str
    context_id: str
    training_dataset_id: str
    validation_dataset_id: str
    validation_version: str
    gate_version: str
    decision: str
    claim_state: str
    output_prediction_model_id: str
    metrics: HeldOutValidationMetrics
    gates: tuple[ValidationGateResult, ...]
    predictions: tuple[HeldOutPrediction, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "profile_id": self.profile_id,
            "split_id": self.split_id,
            "context_id": self.context_id,
            "training_dataset_id": self.training_dataset_id,
            "validation_dataset_id": self.validation_dataset_id,
            "validation_version": self.validation_version,
            "gate_version": self.gate_version,
            "decision": self.decision,
            "claim_state": self.claim_state,
            "output_prediction_model_id": self.output_prediction_model_id,
            "metrics": self.metrics.to_dict(),
            "gates": [gate.to_dict() for gate in self.gates],
            "predictions": [item.to_dict() for item in self.predictions],
            "warnings": list(self.warnings),
        }


def validate_resource_profile_heldout(
    *,
    profile: ResourceCalibrationProfile,
    split: ValidationSplit,
    training_observations: Sequence[ResourceFitObservation],
    validation_observations: Sequence[ResourceFitObservation],
    validation_context_id: str,
    validation_dataset_id: str,
    observed_output_fold_changes: Mapping[str, float] | None = None,
    predicted_output_fold_changes: Mapping[str, float] | None = None,
    output_prediction_model_id: str = "",
    thresholds: HeldOutGateThresholds | None = None,
) -> HeldOutValidationReport:
    resolved_thresholds = thresholds or HeldOutGateThresholds()
    training = tuple(
        sorted(training_observations, key=lambda item: item.observation_id)
    )
    validation = tuple(
        sorted(validation_observations, key=lambda item: item.observation_id)
    )
    if not training or not validation:
        raise ValueError("Training and validation observations must both be non-empty.")
    if validation_context_id != profile.context_id:
        raise ValueError(
            "Validation context does not match calibration profile context: "
            f"{validation_context_id!r} != {profile.context_id!r}."
        )
    if not validation_dataset_id.strip():
        raise ValueError("validation_dataset_id must be non-empty.")
    if not split.frozen:
        raise ValueError("Validation split must be frozen.")
    training_ids = {item.observation_id for item in training}
    validation_ids = {item.observation_id for item in validation}
    if len(training_ids) != len(training) or len(validation_ids) != len(validation):
        raise ValueError("Observation IDs must be unique within each partition.")
    leakage = training_ids & validation_ids
    if leakage:
        raise ValueError(f"Training/validation observation leakage: {sorted(leakage)}.")
    if tuple(item.observation_id for item in training) != tuple(
        sorted(profile.source_observation_ids)
    ):
        raise ValueError(
            "Training observation IDs do not match the immutable profile source IDs."
        )
    fingerprint = resource_observation_fingerprint(training)
    if fingerprint != profile.input_fingerprint:
        raise ValueError(
            "Training observation fingerprint does not match the immutable profile."
        )
    _validate_split_membership(split, training, validation)
    observed_output, predicted_output = _validated_output_predictions(
        validation_ids,
        observed_output_fold_changes,
        predicted_output_fold_changes,
        output_prediction_model_id,
    )

    parameter_by_name = {item.parameter_name: item for item in profile.parameters}
    try:
        demand_parameter = parameter_by_name["aggregate_demand_coefficient"]
        growth_parameter = parameter_by_name["normalized_growth_coupling"]
    except KeyError as exc:
        raise ValueError(
            "Calibration profile is missing an M4-required combined parameter."
        ) from exc
    predictions = []
    for observation in validation:
        capacity, growth = predict_resource_response(
            observation.demand_index,
            demand_parameter.value,
            growth_parameter.value,
        )
        capacity_interval, growth_interval = _prediction_intervals(
            observation,
            demand_parameter.interval_lower,
            demand_parameter.interval_upper,
            growth_parameter.interval_lower,
            growth_parameter.interval_upper,
            resolved_thresholds.prediction_interval_sigma_multiplier,
        )
        predictions.append(
            HeldOutPrediction(
                observation_id=observation.observation_id,
                construct_id=observation.construct_id,
                demand_index=observation.demand_index,
                observed_capacity_loss=observation.observed_capacity_loss,
                predicted_capacity_loss=float(capacity),
                capacity_interval_lower=(
                    capacity_interval[0] if capacity_interval else None
                ),
                capacity_interval_upper=(
                    capacity_interval[1] if capacity_interval else None
                ),
                observed_relative_growth=observation.observed_relative_growth,
                predicted_relative_growth=float(growth),
                growth_interval_lower=(growth_interval[0] if growth_interval else None),
                growth_interval_upper=(growth_interval[1] if growth_interval else None),
                observed_output_fold_change=observed_output.get(
                    observation.observation_id
                ),
                predicted_output_fold_change=predicted_output.get(
                    observation.observation_id
                ),
            )
        )
    resolved_predictions = tuple(predictions)
    metrics = _validation_metrics(training, resolved_predictions, resolved_thresholds)
    gates = _validation_gates(
        profile,
        split,
        metrics,
        output_evaluable=bool(observed_output),
        thresholds=resolved_thresholds,
    )
    decision = "go" if all(gate.passed for gate in gates) else "no_go"
    if decision == "go":
        claim_state = "calibrated_comparative_predictor_for_stated_context"
    elif not profile.usable_for_prediction:
        claim_state = "non_identifiable_profile_no_go"
    else:
        claim_state = "fitted_to_training_dataset_heldout_gates_not_met"
    warnings = []
    if not observed_output:
        warnings.append(
            "Output fold-change validation was not evaluable; an explicit external "
            "output predictor and model ID are required."
        )
    failed = [gate.gate_name for gate in gates if not gate.passed]
    if failed:
        warnings.append("Held-out gates not met: " + ", ".join(failed) + ".")
    report_id = _report_id(
        profile,
        split,
        validation_dataset_id,
        resolved_thresholds,
        resolved_predictions,
        output_prediction_model_id,
    )
    return HeldOutValidationReport(
        report_id=report_id,
        profile_id=profile.profile_id,
        split_id=split.split_id,
        context_id=validation_context_id,
        training_dataset_id=profile.dataset_id,
        validation_dataset_id=validation_dataset_id,
        validation_version=RESOURCE_VALIDATION_VERSION,
        gate_version=resolved_thresholds.gate_version,
        decision=decision,
        claim_state=claim_state,
        output_prediction_model_id=output_prediction_model_id,
        metrics=metrics,
        gates=gates,
        predictions=resolved_predictions,
        warnings=tuple(warnings),
    )


def _validate_split_membership(
    split: ValidationSplit,
    training: tuple[ResourceFitObservation, ...],
    validation: tuple[ResourceFitObservation, ...],
) -> None:
    declared_training = set(split.training_construct_ids)
    declared_validation = set(split.validation_construct_ids)
    observed_training = {item.construct_id for item in training}
    observed_validation = {item.construct_id for item in validation}
    wrong_training = observed_training - declared_training
    wrong_validation = observed_validation - declared_validation
    missing_validation = declared_validation - observed_validation
    if wrong_training:
        raise ValueError(
            f"Training observations violate frozen split: {sorted(wrong_training)}."
        )
    if wrong_validation:
        raise ValueError(
            f"Validation observations violate frozen split: {sorted(wrong_validation)}."
        )
    if missing_validation:
        raise ValueError(
            "Frozen split validation constructs have no observations: "
            f"{sorted(missing_validation)}."
        )


def _validated_output_predictions(
    validation_ids: set[str],
    observed: Mapping[str, float] | None,
    predicted: Mapping[str, float] | None,
    model_id: str,
) -> tuple[dict[str, float], dict[str, float]]:
    if observed is None and predicted is None:
        return {}, {}
    if observed is None or predicted is None:
        raise ValueError(
            "Observed and predicted output fold-change mappings must be provided together."
        )
    if not model_id.strip():
        raise ValueError(
            "output_prediction_model_id is required for output validation."
        )
    if set(observed) != validation_ids or set(predicted) != validation_ids:
        raise ValueError(
            "Output fold-change mappings must match validation observation IDs exactly."
        )
    resolved_observed = {key: float(value) for key, value in observed.items()}
    resolved_predicted = {key: float(value) for key, value in predicted.items()}
    for label, values in (
        ("observed", resolved_observed),
        ("predicted", resolved_predicted),
    ):
        if not all(math.isfinite(value) and value > 0.0 for value in values.values()):
            raise ValueError(
                f"All {label} output fold changes must be finite and positive."
            )
    return resolved_observed, resolved_predicted


def _prediction_intervals(
    observation: ResourceFitObservation,
    demand_lower: float | None,
    demand_upper: float | None,
    growth_lower: float | None,
    growth_upper: float | None,
    sigma_multiplier: float,
) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    if None in (demand_lower, demand_upper, growth_lower, growth_upper):
        return None, None
    capacities = []
    growth_values = []
    for demand_parameter in (float(demand_lower), float(demand_upper)):
        for growth_parameter in (float(growth_lower), float(growth_upper)):
            capacity, growth = predict_resource_response(
                observation.demand_index,
                demand_parameter,
                growth_parameter,
            )
            capacities.append(float(capacity))
            growth_values.append(float(growth))
    capacity_margin = sigma_multiplier * observation.capacity_sigma
    growth_margin = sigma_multiplier * observation.growth_sigma
    return (
        (
            max(0.0, min(capacities) - capacity_margin),
            min(1.0, max(capacities) + capacity_margin),
        ),
        (
            max(0.0, min(growth_values) - growth_margin),
            min(1.5, max(growth_values) + growth_margin),
        ),
    )


def _validation_metrics(
    training: tuple[ResourceFitObservation, ...],
    predictions: tuple[HeldOutPrediction, ...],
    thresholds: HeldOutGateThresholds,
) -> HeldOutValidationMetrics:
    observed_burden = [item.observed_capacity_loss for item in predictions]
    predicted_burden = [item.predicted_capacity_loss for item in predictions]
    spearman = _spearman(observed_burden, predicted_burden)
    growth_mape = _median_absolute_percentage_error(
        [item.observed_relative_growth for item in predictions],
        [item.predicted_relative_growth for item in predictions],
    )
    baseline_growth = statistics.median(
        item.observed_relative_growth for item in training
    )
    baseline_mape = _median_absolute_percentage_error(
        [item.observed_relative_growth for item in predictions],
        [baseline_growth for _ in predictions],
    )
    output_pairs = [
        (item.observed_output_fold_change, item.predicted_output_fold_change)
        for item in predictions
        if item.observed_output_fold_change is not None
        and item.predicted_output_fold_change is not None
    ]
    output_accuracy = (
        sum(
            _fold_change_direction(observed, thresholds.fold_change_neutral_tolerance)
            == _fold_change_direction(
                predicted, thresholds.fold_change_neutral_tolerance
            )
            for observed, predicted in output_pairs
        )
        / len(output_pairs)
        if output_pairs
        else None
    )
    capacity_covered = [
        item.capacity_interval_lower
        <= item.observed_capacity_loss
        <= item.capacity_interval_upper
        for item in predictions
        if item.capacity_interval_lower is not None
        and item.capacity_interval_upper is not None
    ]
    growth_covered = [
        item.growth_interval_lower
        <= item.observed_relative_growth
        <= item.growth_interval_upper
        for item in predictions
        if item.growth_interval_lower is not None
        and item.growth_interval_upper is not None
    ]
    capacity_coverage = (
        sum(capacity_covered) / len(capacity_covered) if capacity_covered else None
    )
    growth_coverage = (
        sum(growth_covered) / len(growth_covered) if growth_covered else None
    )
    combined_values = capacity_covered + growth_covered
    combined_coverage = (
        sum(combined_values) / len(combined_values) if combined_values else None
    )
    return HeldOutValidationMetrics(
        heldout_observation_count=len(predictions),
        heldout_construct_count=len({item.construct_id for item in predictions}),
        distinct_heldout_demand_levels=len({item.demand_index for item in predictions}),
        burden_spearman=spearman,
        relative_growth_mape=growth_mape,
        simple_baseline_relative_growth_mape=baseline_mape,
        output_direction_accuracy=output_accuracy,
        capacity_interval_coverage=capacity_coverage,
        growth_interval_coverage=growth_coverage,
        combined_interval_coverage=combined_coverage,
    )


def _validation_gates(
    profile: ResourceCalibrationProfile,
    split: ValidationSplit,
    metrics: HeldOutValidationMetrics,
    *,
    output_evaluable: bool,
    thresholds: HeldOutGateThresholds,
) -> tuple[ValidationGateResult, ...]:
    profile_gate = ValidationGateResult(
        gate_name="identifiable_profile",
        status="pass" if profile.usable_for_prediction else "fail",
        passed=profile.usable_for_prediction,
        observed_value=profile.diagnostics.status,
        threshold="identifiable",
        reason_code=(
            "profile_identifiable"
            if profile.usable_for_prediction
            else "profile_non_identifiable"
        ),
        detail="M3 profile must pass identifiability gates before held-out claims.",
    )
    design_pass = (
        metrics.heldout_observation_count >= thresholds.minimum_heldout_observations
        and metrics.heldout_construct_count >= 1
        and bool(split.validation_construct_ids)
    )
    design_gate = ValidationGateResult(
        gate_name="heldout_design",
        status="pass" if design_pass else "fail",
        passed=design_pass,
        observed_value=metrics.heldout_observation_count,
        threshold=thresholds.minimum_heldout_observations,
        reason_code=(
            "heldout_design_sufficient"
            if design_pass
            else "heldout_design_insufficient"
        ),
        detail="A frozen held-out construct and enough observations are required.",
    )
    ranking_pass = (
        metrics.burden_spearman is not None
        and metrics.burden_spearman >= thresholds.minimum_burden_spearman
    )
    ranking_gate = ValidationGateResult(
        gate_name="burden_ranking",
        status="pass" if ranking_pass else "fail",
        passed=ranking_pass,
        observed_value=metrics.burden_spearman,
        threshold=thresholds.minimum_burden_spearman,
        reason_code=(
            "burden_ranking_pass" if ranking_pass else "burden_ranking_below_gate"
        ),
        detail="Spearman correlation compares observed and predicted held-out capacity loss.",
    )
    growth_pass = (
        metrics.relative_growth_mape <= thresholds.maximum_relative_growth_mape
    )
    growth_gate = ValidationGateResult(
        gate_name="relative_growth_error",
        status="pass" if growth_pass else "fail",
        passed=growth_pass,
        observed_value=metrics.relative_growth_mape,
        threshold=thresholds.maximum_relative_growth_mape,
        reason_code=("growth_mape_pass" if growth_pass else "growth_mape_above_gate"),
        detail="Held-out relative-growth median absolute percentage error.",
    )
    output_pass = (
        output_evaluable
        and metrics.output_direction_accuracy is not None
        and metrics.output_direction_accuracy
        >= thresholds.minimum_output_direction_accuracy
    )
    output_gate = ValidationGateResult(
        gate_name="output_fold_change_direction",
        status="pass"
        if output_pass
        else "fail"
        if output_evaluable
        else "not_evaluable",
        passed=output_pass,
        observed_value=metrics.output_direction_accuracy,
        threshold=thresholds.minimum_output_direction_accuracy,
        reason_code=(
            "output_direction_pass"
            if output_pass
            else "output_direction_below_gate"
            if output_evaluable
            else "output_prediction_missing"
        ),
        detail="Output direction requires explicit observed and model-predicted fold changes.",
    )
    interval_pass = (
        metrics.combined_interval_coverage is not None
        and metrics.combined_interval_coverage
        >= thresholds.minimum_prediction_interval_coverage
    )
    interval_gate = ValidationGateResult(
        gate_name="prediction_interval_coverage",
        status=(
            "pass"
            if interval_pass
            else "fail"
            if metrics.combined_interval_coverage is not None
            else "not_evaluable"
        ),
        passed=interval_pass,
        observed_value=metrics.combined_interval_coverage,
        threshold=thresholds.minimum_prediction_interval_coverage,
        reason_code=(
            "interval_coverage_pass"
            if interval_pass
            else "interval_coverage_below_gate"
            if metrics.combined_interval_coverage is not None
            else "prediction_intervals_missing"
        ),
        detail="Capacity and growth predictive intervals include observation noise.",
    )
    baseline_pass = (
        not thresholds.require_simple_baseline_improvement
        or metrics.relative_growth_mape < metrics.simple_baseline_relative_growth_mape
    )
    baseline_gate = ValidationGateResult(
        gate_name="simple_baseline_improvement",
        status="pass" if baseline_pass else "fail",
        passed=baseline_pass,
        observed_value=(
            metrics.simple_baseline_relative_growth_mape - metrics.relative_growth_mape
        ),
        threshold="> 0 improvement",
        reason_code=(
            "baseline_improvement_pass" if baseline_pass else "no_baseline_improvement"
        ),
        detail="The fitted model must improve growth MAPE over a training-median baseline.",
    )
    return (
        profile_gate,
        design_gate,
        ranking_gate,
        growth_gate,
        output_gate,
        interval_gate,
        baseline_gate,
    )


def _spearman(observed: list[float], predicted: list[float]) -> float | None:
    if len(observed) < 3:
        return None
    observed_ranks = _rankdata(observed)
    predicted_ranks = _rankdata(predicted)
    if np.std(observed_ranks) <= 0.0 or np.std(predicted_ranks) <= 0.0:
        return None
    return float(np.corrcoef(observed_ranks, predicted_ranks)[0, 1])


def _rankdata(values: list[float]) -> np.ndarray:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = np.empty(len(values), dtype=float)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        average_rank = (start + 1 + end) / 2.0
        for index in order[start:end]:
            ranks[index] = average_rank
        start = end
    return ranks


def _median_absolute_percentage_error(
    observed: list[float],
    predicted: list[float],
) -> float:
    errors = [
        abs(prediction - actual) / max(abs(actual), 1e-9)
        for actual, prediction in zip(observed, predicted)
    ]
    return float(statistics.median(errors))


def _fold_change_direction(value: float, tolerance: float) -> int:
    delta = value - 1.0
    if abs(delta) <= tolerance:
        return 0
    return 1 if delta > 0.0 else -1


def _report_id(
    profile: ResourceCalibrationProfile,
    split: ValidationSplit,
    validation_dataset_id: str,
    thresholds: HeldOutGateThresholds,
    predictions: tuple[HeldOutPrediction, ...],
    output_prediction_model_id: str,
) -> str:
    payload = {
        "profile_id": profile.profile_id,
        "split": split.to_dict(),
        "validation_dataset_id": validation_dataset_id,
        "thresholds": asdict(thresholds),
        "predictions": [item.to_dict() for item in predictions],
        "output_prediction_model_id": output_prediction_model_id,
        "validation_version": RESOURCE_VALIDATION_VERSION,
    }
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return (
        "resource_validation_"
        + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    )
