from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from schemas.resource_calibration import DerivedMetric

try:
    from scipy.optimize import least_squares
except ModuleNotFoundError:  # pragma: no cover - depends on runtime extras
    least_squares = None


RESOURCE_FIT_MODEL_VERSION = "resource_competition_fit_v0.1"
RESOURCE_CALIBRATION_PROFILE_VERSION = "0.1.0"
PARAMETER_NAMES = (
    "aggregate_demand_coefficient",
    "normalized_growth_coupling",
)
DEFAULT_PARAMETERS = {
    "aggregate_demand_coefficient": 0.8,
    "normalized_growth_coupling": 1.0,
}
DEFAULT_BOUNDS = {
    "aggregate_demand_coefficient": (1e-3, 20.0),
    "normalized_growth_coupling": (1e-3, 10.0),
}


@dataclass(frozen=True)
class ResourceFitObservation:
    observation_id: str
    construct_id: str
    condition_id: str
    biological_replicate: str
    demand_index: float
    observed_capacity_loss: float
    observed_relative_growth: float
    capacity_sigma: float = 0.03
    growth_sigma: float = 0.03
    source_metric_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "observation_id",
            "construct_id",
            "condition_id",
            "biological_replicate",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must be non-empty.")
        for name in (
            "demand_index",
            "observed_capacity_loss",
            "observed_relative_growth",
            "capacity_sigma",
            "growth_sigma",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite.")
        if self.demand_index < 0.0:
            raise ValueError("demand_index must be non-negative.")
        if not 0.0 <= self.observed_capacity_loss <= 1.0:
            raise ValueError("observed_capacity_loss must be between 0 and 1.")
        if not 0.0 <= self.observed_relative_growth <= 1.5:
            raise ValueError("observed_relative_growth must be between 0 and 1.5.")
        if self.capacity_sigma <= 0.0 or self.growth_sigma <= 0.0:
            raise ValueError("Observation sigmas must be positive.")
        if len(set(self.source_metric_ids)) != len(self.source_metric_ids):
            raise ValueError("source_metric_ids must be unique.")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source_metric_ids"] = list(self.source_metric_ids)
        return payload


@dataclass(frozen=True)
class FittedResourceParameter:
    parameter_name: str
    value: float
    unit: str
    lower_bound: float
    upper_bound: float
    interval_lower: float | None
    interval_upper: float | None
    interval_method: str
    role: str = "calibrated"
    log_space: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParameterDefaultComparison:
    parameter_name: str
    default_value: float
    fitted_value: float
    fitted_to_default_ratio: float
    percent_change: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResourceFitDiagnostics:
    status: str
    optimizer: str
    optimizer_success: bool
    optimizer_message: str
    observation_count: int
    residual_count: int
    parameter_count: int
    degrees_of_freedom: int
    weighted_rmse: float
    capacity_rmse: float
    relative_growth_rmse: float
    jacobian_rank: int
    jacobian_condition_number: float | None
    unique_positive_demand_levels: int
    demand_dynamic_range: float | None
    parameters_at_bounds: tuple[str, ...]
    bootstrap_attempts: int
    bootstrap_successes: int
    bootstrap_seed: int
    interval_method: str
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["parameters_at_bounds"] = list(self.parameters_at_bounds)
        payload["reason_codes"] = list(self.reason_codes)
        return payload


@dataclass(frozen=True)
class ResourceCalibrationProfile:
    profile_id: str
    context_id: str
    dataset_id: str
    input_fingerprint: str
    model_version: str
    schema_version: str
    claim_state: str
    usable_for_prediction: bool
    parameters: tuple[FittedResourceParameter, ...]
    diagnostics: ResourceFitDiagnostics
    default_comparison: tuple[ParameterDefaultComparison, ...]
    fixed_assumptions: tuple[str, ...]
    source_observation_ids: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "context_id": self.context_id,
            "dataset_id": self.dataset_id,
            "input_fingerprint": self.input_fingerprint,
            "model_version": self.model_version,
            "schema_version": self.schema_version,
            "claim_state": self.claim_state,
            "usable_for_prediction": self.usable_for_prediction,
            "parameters": [item.to_dict() for item in self.parameters],
            "diagnostics": self.diagnostics.to_dict(),
            "default_comparison": [item.to_dict() for item in self.default_comparison],
            "fixed_assumptions": list(self.fixed_assumptions),
            "source_observation_ids": list(self.source_observation_ids),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ResourceFittedPoint:
    observation_id: str
    demand_index: float
    observed_capacity_loss: float
    predicted_capacity_loss: float
    observed_relative_growth: float
    predicted_relative_growth: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResourceParameterFitResult:
    profile: ResourceCalibrationProfile
    fitted_points: tuple[ResourceFittedPoint, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "fitted_points": [item.to_dict() for item in self.fitted_points],
        }


def predict_resource_response(
    demand_index: float | np.ndarray,
    aggregate_demand_coefficient: float,
    normalized_growth_coupling: float,
) -> tuple[np.ndarray, np.ndarray]:
    demand = np.asarray(demand_index, dtype=float)
    capacity_loss = (
        aggregate_demand_coefficient
        * demand
        / (1.0 + aggregate_demand_coefficient * demand)
    )
    relative_growth = np.exp(-normalized_growth_coupling * capacity_loss)
    return capacity_loss, relative_growth


def observations_from_derived_metrics(
    metrics: Sequence[DerivedMetric],
    *,
    demand_index_by_construct: Mapping[str, float] | None = None,
    demand_index_by_condition: Mapping[str, float] | None = None,
    baseline_construct_id: str,
    capacity_sigma: float = 0.03,
    growth_sigma: float = 0.03,
) -> tuple[ResourceFitObservation, ...]:
    if not demand_index_by_construct and not demand_index_by_condition:
        raise ValueError("At least one demand-index mapping must be provided.")
    grouped: dict[
        tuple[str, str, str, str, str, str, float, str],
        dict[str, DerivedMetric],
    ] = {}
    for metric in metrics:
        plate_id = str(metric.metadata.get("plate_id") or "")
        experiment_id = str(metric.metadata.get("experiment_id") or "")
        well = str(metric.metadata.get("well") or "")
        inducer_name = str(metric.metadata.get("inducer_name") or "")
        inducer_unit = str(metric.metadata.get("inducer_unit") or "")
        inducer_concentration = float(
            metric.metadata.get("inducer_concentration") or 0.0
        )
        key = (
            metric.construct_id,
            metric.biological_replicate,
            experiment_id,
            plate_id,
            well,
            inducer_name,
            inducer_concentration,
            inducer_unit,
        )
        grouped.setdefault(key, {})[metric.metric_name] = metric
    baseline_growth = [
        values["growth_rate_per_h"].value
        for key, values in grouped.items()
        if key[0] == baseline_construct_id and "growth_rate_per_h" in values
    ]
    if not baseline_growth:
        raise ValueError("No baseline growth-rate metrics were found.")
    baseline_growth_median = float(np.median(baseline_growth))
    if baseline_growth_median <= 0.0:
        raise ValueError("Baseline growth-rate median must be positive.")
    observations = []
    for key, values in sorted(grouped.items()):
        (
            construct_id,
            biological_replicate,
            experiment_id,
            plate_id,
            well,
            inducer_name,
            inducer_concentration,
            inducer_unit,
        ) = key
        condition_key = resource_condition_key(
            construct_id,
            inducer_name,
            inducer_concentration,
            inducer_unit,
        )
        demand_index = None
        if demand_index_by_condition:
            demand_index = demand_index_by_condition.get(condition_key)
        if demand_index is None and demand_index_by_construct:
            demand_index = demand_index_by_construct.get(construct_id)
        if demand_index is None:
            raise ValueError(f"Missing demand index for condition {condition_key!r}.")
        if "growth_rate_per_h" not in values or "capacity_loss_fraction" not in values:
            continue
        growth = values["growth_rate_per_h"]
        capacity = values["capacity_loss_fraction"]
        relative_growth = min(1.5, max(0.0, growth.value / baseline_growth_median))
        observations.append(
            ResourceFitObservation(
                observation_id=":".join(
                    (
                        "m3",
                        experiment_id,
                        plate_id,
                        construct_id,
                        biological_replicate,
                        well,
                        f"{inducer_concentration:g}",
                    )
                ),
                construct_id=construct_id,
                condition_id=condition_key,
                biological_replicate=biological_replicate,
                demand_index=float(demand_index),
                observed_capacity_loss=min(1.0, max(0.0, capacity.value)),
                observed_relative_growth=relative_growth,
                capacity_sigma=capacity_sigma,
                growth_sigma=growth_sigma,
                source_metric_ids=(capacity.metric_id, growth.metric_id),
            )
        )
    if not observations:
        raise ValueError("No paired growth and capacity-loss metrics were found.")
    return tuple(observations)


def resource_condition_key(
    construct_id: str,
    inducer_name: str = "",
    inducer_concentration: float = 0.0,
    inducer_unit: str = "",
) -> str:
    return "|".join(
        (
            construct_id,
            inducer_name,
            f"{float(inducer_concentration):g}",
            inducer_unit,
        )
    )


def fit_resource_competition_parameters(
    observations: Sequence[ResourceFitObservation],
    *,
    context_id: str,
    dataset_id: str,
    defaults: Mapping[str, float] | None = None,
    bounds: Mapping[str, tuple[float, float]] | None = None,
    bootstrap_samples: int = 100,
    bootstrap_seed: int = 1729,
    max_condition_number: float = 1e5,
) -> ResourceParameterFitResult:
    if least_squares is None:
        raise RuntimeError("SciPy least_squares is required for M3 resource fitting.")
    parsed = tuple(sorted(observations, key=lambda item: item.observation_id))
    if len(parsed) < 4:
        raise ValueError("At least four observations are required for joint fitting.")
    observation_ids = [item.observation_id for item in parsed]
    if len(set(observation_ids)) != len(observation_ids):
        raise ValueError("Observation IDs must be unique.")
    if not context_id.strip() or not dataset_id.strip():
        raise ValueError("context_id and dataset_id must be non-empty.")
    if bootstrap_samples < 0:
        raise ValueError("bootstrap_samples must be non-negative.")
    resolved_defaults = _validated_defaults(defaults or DEFAULT_PARAMETERS)
    resolved_bounds = _validated_bounds(bounds or DEFAULT_BOUNDS)
    outside_bounds = [
        name
        for name in PARAMETER_NAMES
        if not (
            resolved_bounds[name][0]
            <= resolved_defaults[name]
            <= resolved_bounds[name][1]
        )
    ]
    if outside_bounds:
        raise ValueError(f"Default parameters fall outside bounds: {outside_bounds}.")
    initial = np.asarray(
        [math.log(resolved_defaults[name]) for name in PARAMETER_NAMES],
        dtype=float,
    )
    lower = np.asarray(
        [math.log(resolved_bounds[name][0]) for name in PARAMETER_NAMES],
        dtype=float,
    )
    upper = np.asarray(
        [math.log(resolved_bounds[name][1]) for name in PARAMETER_NAMES],
        dtype=float,
    )
    fit = _least_squares_fit(parsed, initial, lower, upper)
    values = np.exp(fit.x)
    residuals = _weighted_residuals(fit.x, parsed)
    predicted_capacity, predicted_growth = predict_resource_response(
        np.asarray([item.demand_index for item in parsed]),
        values[0],
        values[1],
    )
    observed_capacity = np.asarray(
        [item.observed_capacity_loss for item in parsed], dtype=float
    )
    observed_growth = np.asarray(
        [item.observed_relative_growth for item in parsed], dtype=float
    )
    rank, condition_number = _jacobian_diagnostics(fit.jac)
    bound_hits = _parameters_at_bounds(fit.x, lower, upper)
    positive_levels = sorted(
        {item.demand_index for item in parsed if item.demand_index > 0}
    )
    dynamic_range = (
        max(positive_levels) / min(positive_levels) if positive_levels else None
    )
    reasons = []
    if not fit.success:
        reasons.append("optimizer_failed")
    if len(positive_levels) < 3:
        reasons.append("insufficient_distinct_positive_demand_levels")
    if dynamic_range is None or dynamic_range < 2.0:
        reasons.append("insufficient_demand_dynamic_range")
    if rank < len(PARAMETER_NAMES):
        reasons.append("rank_deficient_jacobian")
    if condition_number is None or condition_number > max_condition_number:
        reasons.append("ill_conditioned_jacobian")
    if bound_hits:
        reasons.append("parameter_at_bound")
    identifiable = not reasons
    bootstrap_values: list[np.ndarray] = []
    bootstrap_success_count = 0
    interval_method = "not_estimable"
    if identifiable and bootstrap_samples:
        bootstrap_values = _bootstrap_estimates(
            parsed,
            initial=fit.x,
            lower=lower,
            upper=upper,
            attempts=bootstrap_samples,
            seed=bootstrap_seed,
        )
        bootstrap_success_count = len(bootstrap_values)
        required_successes = max(10, math.ceil(bootstrap_samples * 0.70))
        if len(bootstrap_values) >= required_successes:
            interval_method = "replicate_stratified_bootstrap_95pct"
        else:
            reasons.append("insufficient_bootstrap_successes")
            identifiable = False
            bootstrap_values = []
    elif identifiable:
        reasons.append("interval_estimation_disabled")
        identifiable = False

    intervals: dict[str, tuple[float | None, float | None]] = {
        name: (None, None) for name in PARAMETER_NAMES
    }
    if bootstrap_values:
        samples = np.asarray(bootstrap_values, dtype=float)
        for index, name in enumerate(PARAMETER_NAMES):
            intervals[name] = (
                float(np.quantile(samples[:, index], 0.025)),
                float(np.quantile(samples[:, index], 0.975)),
            )
    status = "identifiable" if identifiable else "non_identifiable"
    diagnostics = ResourceFitDiagnostics(
        status=status,
        optimizer="scipy_least_squares_trf_log_space",
        optimizer_success=bool(fit.success),
        optimizer_message=str(fit.message),
        observation_count=len(parsed),
        residual_count=len(residuals),
        parameter_count=len(PARAMETER_NAMES),
        degrees_of_freedom=len(residuals) - len(PARAMETER_NAMES),
        weighted_rmse=float(np.sqrt(np.mean(residuals**2))),
        capacity_rmse=float(
            np.sqrt(np.mean((predicted_capacity - observed_capacity) ** 2))
        ),
        relative_growth_rmse=float(
            np.sqrt(np.mean((predicted_growth - observed_growth) ** 2))
        ),
        jacobian_rank=rank,
        jacobian_condition_number=condition_number,
        unique_positive_demand_levels=len(positive_levels),
        demand_dynamic_range=dynamic_range,
        parameters_at_bounds=bound_hits,
        bootstrap_attempts=bootstrap_samples,
        bootstrap_successes=bootstrap_success_count,
        bootstrap_seed=bootstrap_seed,
        interval_method=interval_method,
        reason_codes=tuple(reasons),
    )
    parameters = tuple(
        FittedResourceParameter(
            parameter_name=name,
            value=float(values[index]),
            unit="dimensionless",
            lower_bound=resolved_bounds[name][0],
            upper_bound=resolved_bounds[name][1],
            interval_lower=intervals[name][0],
            interval_upper=intervals[name][1],
            interval_method=interval_method,
        )
        for index, name in enumerate(PARAMETER_NAMES)
    )
    comparison = tuple(
        ParameterDefaultComparison(
            parameter_name=name,
            default_value=resolved_defaults[name],
            fitted_value=float(values[index]),
            fitted_to_default_ratio=float(values[index]) / resolved_defaults[name],
            percent_change=(
                (float(values[index]) - resolved_defaults[name])
                / resolved_defaults[name]
                * 100.0
            ),
        )
        for index, name in enumerate(PARAMETER_NAMES)
    )
    fingerprint = resource_observation_fingerprint(parsed)
    profile_id = _profile_id(
        context_id=context_id,
        dataset_id=dataset_id,
        input_fingerprint=fingerprint,
        parameters=parameters,
        bootstrap_seed=bootstrap_seed,
    )
    warnings = (
        ()
        if identifiable
        else (
            "Fit is non-identifiable and must not be applied as a predictive calibration profile.",
        )
    )
    profile = ResourceCalibrationProfile(
        profile_id=profile_id,
        context_id=context_id,
        dataset_id=dataset_id,
        input_fingerprint=fingerprint,
        model_version=RESOURCE_FIT_MODEL_VERSION,
        schema_version=RESOURCE_CALIBRATION_PROFILE_VERSION,
        claim_state=(
            "fitted_to_synthetic_or_local_dataset_not_heldout_validated"
            if identifiable
            else "non_identifiable"
        ),
        usable_for_prediction=identifiable,
        parameters=parameters,
        diagnostics=diagnostics,
        default_comparison=comparison,
        fixed_assumptions=(
            "capacity_loss=demand_coefficient*demand_index/(1+demand_coefficient*demand_index)",
            "relative_growth=exp(-growth_coupling*capacity_loss)",
            "RNAP/ribosome totals, Km values, and elongation rates are not fitted",
            "TX and TL demand are represented by one aggregate coefficient",
        ),
        source_observation_ids=tuple(observation_ids),
        warnings=warnings,
    )
    fitted_points = tuple(
        ResourceFittedPoint(
            observation_id=item.observation_id,
            demand_index=item.demand_index,
            observed_capacity_loss=item.observed_capacity_loss,
            predicted_capacity_loss=float(predicted_capacity[index]),
            observed_relative_growth=item.observed_relative_growth,
            predicted_relative_growth=float(predicted_growth[index]),
        )
        for index, item in enumerate(parsed)
    )
    return ResourceParameterFitResult(profile=profile, fitted_points=fitted_points)


def _least_squares_fit(
    observations: Sequence[ResourceFitObservation],
    initial: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
):
    return least_squares(
        _weighted_residuals,
        initial,
        args=(observations,),
        bounds=(lower, upper),
        method="trf",
        x_scale="jac",
        max_nfev=2000,
    )


def _weighted_residuals(
    log_parameters: np.ndarray,
    observations: Sequence[ResourceFitObservation],
) -> np.ndarray:
    values = np.exp(log_parameters)
    demand = np.asarray([item.demand_index for item in observations], dtype=float)
    capacity, growth = predict_resource_response(demand, values[0], values[1])
    capacity_residual = np.asarray(
        [
            (prediction - item.observed_capacity_loss) / item.capacity_sigma
            for prediction, item in zip(capacity, observations)
        ],
        dtype=float,
    )
    growth_residual = np.asarray(
        [
            (prediction - item.observed_relative_growth) / item.growth_sigma
            for prediction, item in zip(growth, observations)
        ],
        dtype=float,
    )
    return np.concatenate((capacity_residual, growth_residual))


def _bootstrap_estimates(
    observations: tuple[ResourceFitObservation, ...],
    *,
    initial: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    attempts: int,
    seed: int,
) -> list[np.ndarray]:
    groups: dict[tuple[str, float], list[ResourceFitObservation]] = {}
    for observation in observations:
        groups.setdefault(
            (observation.condition_id, observation.demand_index), []
        ).append(observation)
    if any(len(group) < 2 for group in groups.values()):
        return []
    rng = np.random.default_rng(seed)
    estimates = []
    for _ in range(attempts):
        sample = []
        for key in sorted(groups):
            group = groups[key]
            indices = rng.integers(0, len(group), size=len(group))
            sample.extend(group[int(index)] for index in indices)
        try:
            fit = _least_squares_fit(sample, initial, lower, upper)
        except Exception:
            continue
        if not fit.success or not np.all(np.isfinite(fit.x)):
            continue
        if _parameters_at_bounds(fit.x, lower, upper):
            continue
        estimates.append(np.exp(fit.x))
    return estimates


def _jacobian_diagnostics(jacobian: np.ndarray) -> tuple[int, float | None]:
    singular_values = np.linalg.svd(jacobian, compute_uv=False)
    if not len(singular_values) or singular_values[0] <= 0.0:
        return 0, None
    tolerance = max(jacobian.shape) * np.finfo(float).eps * singular_values[0]
    rank = int(np.sum(singular_values > tolerance))
    if rank < jacobian.shape[1] or singular_values[-1] <= 0.0:
        return rank, None
    return rank, float(singular_values[0] / singular_values[-1])


def _parameters_at_bounds(
    log_values: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    *,
    tolerance: float = 1e-4,
) -> tuple[str, ...]:
    hits = []
    for index, name in enumerate(PARAMETER_NAMES):
        span = upper[index] - lower[index]
        if (
            abs(log_values[index] - lower[index]) <= tolerance * span
            or abs(upper[index] - log_values[index]) <= tolerance * span
        ):
            hits.append(name)
    return tuple(hits)


def _validated_defaults(values: Mapping[str, float]) -> dict[str, float]:
    missing = set(PARAMETER_NAMES) - set(values)
    if missing:
        raise ValueError(f"Missing default parameters: {sorted(missing)}.")
    result = {name: float(values[name]) for name in PARAMETER_NAMES}
    if not all(math.isfinite(value) and value > 0.0 for value in result.values()):
        raise ValueError("Default parameters must be finite and positive.")
    return result


def _validated_bounds(
    values: Mapping[str, tuple[float, float]],
) -> dict[str, tuple[float, float]]:
    missing = set(PARAMETER_NAMES) - set(values)
    if missing:
        raise ValueError(f"Missing parameter bounds: {sorted(missing)}.")
    result = {}
    for name in PARAMETER_NAMES:
        lower, upper = (float(item) for item in values[name])
        if not (math.isfinite(lower) and math.isfinite(upper) and 0.0 < lower < upper):
            raise ValueError(f"Invalid positive bounds for {name!r}.")
        result[name] = (lower, upper)
    return result


def resource_observation_fingerprint(
    observations: Sequence[ResourceFitObservation],
) -> str:
    payload = [
        item.to_dict()
        for item in sorted(observations, key=lambda value: value.observation_id)
    ]
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _profile_id(
    *,
    context_id: str,
    dataset_id: str,
    input_fingerprint: str,
    parameters: tuple[FittedResourceParameter, ...],
    bootstrap_seed: int,
) -> str:
    payload = {
        "context_id": context_id,
        "dataset_id": dataset_id,
        "input_fingerprint": input_fingerprint,
        "model_version": RESOURCE_FIT_MODEL_VERSION,
        "parameters": [item.to_dict() for item in parameters],
        "bootstrap_seed": bootstrap_seed,
    }
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "resource_fit_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
