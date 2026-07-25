from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np

from benchmark_suite.resource_parameter_fitting import (
    PARAMETER_NAMES,
    ResourceFitObservation,
    fit_resource_competition_parameters,
    predict_resource_response,
)


RESOURCE_MODEL_ANALYSIS_VERSION = "resource_model_analysis_v0.1"
DEFAULT_RANGES = {
    "aggregate_demand_coefficient": (0.2, 3.0),
    "normalized_growth_coupling": (0.2, 2.0),
}
OUTPUT_NAMES = ("mean_capacity_loss", "mean_growth_loss")
CLAIM_BOUNDARY = (
    "Synthetic/local global-sensitivity and model-comparison diagnostics only; "
    "not evidence of absolute in-vivo prediction or justification for automatic "
    "model-family promotion."
)


def run_resource_model_analysis(
    workflow: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    resolved = dict(config or {})
    observations = tuple(
        _observation_from_dict(dict(item))
        for item in workflow.get("observations") or []
    )
    split = workflow.get("validation_split") or {}
    training_constructs = set(split.get("training_construct_ids") or [])
    validation_constructs = set(split.get("validation_construct_ids") or [])
    training = tuple(
        item for item in observations if item.construct_id in training_constructs
    )
    validation = tuple(
        item for item in observations if item.construct_id in validation_constructs
    )
    if not training or not validation:
        raise ValueError(
            "Stored workflow must contain non-empty frozen training and validation partitions."
        )
    ranges, range_source = _resolve_ranges(workflow, resolved.get("parameter_ranges"))
    demands = sorted({item.demand_index for item in training + validation})
    if len(demands) < 3:
        raise ValueError("At least three distinct demand levels are required for M6.")

    morris = run_resource_morris_screening(
        demands,
        parameter_ranges=ranges,
        trajectories=int(resolved.get("morris_trajectories", 24)),
        levels=int(resolved.get("morris_levels", 6)),
        seed=int(resolved.get("random_seed", 2606)),
    )
    sobol = run_resource_sobol_pilot(
        demands,
        parameter_ranges=ranges,
        sample_count=int(resolved.get("sobol_sample_count", 512)),
        seed=int(resolved.get("random_seed", 2606)) + 1,
    )
    comparison = compare_resource_model_families(
        training,
        validation,
        context_id=str((workflow.get("context") or {}).get("context_id") or ""),
        dataset_id=str(workflow.get("dataset_id") or ""),
        bootstrap_samples=int(resolved.get("fit_bootstrap_samples", 20)),
        bootstrap_seed=int(resolved.get("random_seed", 2606)) + 2,
    )
    validation_decision = str(
        (workflow.get("validation") or {}).get("decision") or "no_go"
    )
    recommendation = _model_recommendation(comparison, validation_decision)
    return {
        "analysis_version": RESOURCE_MODEL_ANALYSIS_VERSION,
        "status": "completed",
        "workflow_id": workflow.get("workflow_id"),
        "context_id": (workflow.get("context") or {}).get("context_id"),
        "parameter_ranges": {
            name: list(bounds) for name, bounds in ranges.items()
        },
        "parameter_range_source": range_source,
        "demand_levels": demands,
        "morris": morris,
        "sobol_pilot": sobol,
        "model_comparison": comparison,
        "recommendation": recommendation,
        "sbml_biocrnpyler_gate": {
            "decision": "no_go",
            "reason_codes": [
                "no_versioned_reaction_network_contract",
                "no_real_pilot_dataset_model_family_comparison",
                "no_external_solver_equivalence_evidence",
            ],
            "next_evidence": [
                "Define a minimal versioned reaction-network export contract.",
                "Compare coarse and CRN models on the same real frozen holdout.",
                "Record runtime, identifiability, and predictive improvement.",
            ],
        },
        "claim_boundary": CLAIM_BOUNDARY,
        "automatic_model_promotion": {
            "allowed": False,
            "reason": "M6 produces diagnostics and readiness gates only.",
        },
    }


def run_resource_morris_screening(
    demand_levels: Sequence[float],
    *,
    parameter_ranges: Mapping[str, tuple[float, float]] | None = None,
    trajectories: int = 24,
    levels: int = 6,
    seed: int = 2606,
) -> dict[str, Any]:
    demands = _validated_demands(demand_levels)
    ranges = _validated_ranges(parameter_ranges or DEFAULT_RANGES)
    if trajectories < 4:
        raise ValueError("Morris screening requires at least four trajectories.")
    if levels < 4 or levels % 2:
        raise ValueError("Morris levels must be an even integer of at least four.")
    rng = np.random.default_rng(seed)
    parameter_names = tuple(PARAMETER_NAMES)
    delta = levels / (2.0 * (levels - 1.0))
    effects: dict[str, dict[str, list[float]]] = {
        output: {name: [] for name in parameter_names} for output in OUTPUT_NAMES
    }
    grid = np.linspace(0.0, 1.0 - delta, levels - 1)
    for _ in range(trajectories):
        base = rng.choice(grid, size=len(parameter_names), replace=True)
        directions = rng.choice(np.asarray((-1.0, 1.0)), size=len(parameter_names))
        current = np.where(directions > 0, base, base + delta)
        order = rng.permutation(len(parameter_names))
        current_output = _evaluate_normalized(current, demands, ranges)
        for index in order:
            updated = current.copy()
            updated[index] += directions[index] * delta
            updated_output = _evaluate_normalized(updated, demands, ranges)
            for output_index, output_name in enumerate(OUTPUT_NAMES):
                effect = (
                    updated_output[output_index] - current_output[output_index]
                ) / (directions[index] * delta)
                effects[output_name][parameter_names[index]].append(float(effect))
            current = updated
            current_output = updated_output

    metrics: dict[str, dict[str, dict[str, float]]] = {}
    for output_name in OUTPUT_NAMES:
        metrics[output_name] = {}
        for parameter_name in parameter_names:
            values = np.asarray(effects[output_name][parameter_name], dtype=float)
            metrics[output_name][parameter_name] = {
                "mu": float(np.mean(values)),
                "mu_star": float(np.mean(np.abs(values))),
                "sigma": float(np.std(values, ddof=1)),
            }
    scores = _combined_sensitivity_scores(metrics, "mu_star")
    return {
        "method": "deterministic_morris_oat_v0.1",
        "status": "screening_only",
        "trajectories": trajectories,
        "levels": levels,
        "delta_normalized": delta,
        "seed": seed,
        "metrics": metrics,
        "ranking": _ranking(scores),
        "warnings": [
            "Morris effects are screening diagnostics and do not decompose variance.",
            "Parameter ranges condition the ranking and must be reviewed explicitly.",
        ],
    }


def run_resource_sobol_pilot(
    demand_levels: Sequence[float],
    *,
    parameter_ranges: Mapping[str, tuple[float, float]] | None = None,
    sample_count: int = 512,
    seed: int = 2607,
) -> dict[str, Any]:
    demands = _validated_demands(demand_levels)
    ranges = _validated_ranges(parameter_ranges or DEFAULT_RANGES)
    if sample_count < 128:
        raise ValueError("Sobol pilot requires at least 128 base samples.")
    rng = np.random.default_rng(seed)
    dimension = len(PARAMETER_NAMES)
    a = rng.random((sample_count, dimension))
    b = rng.random((sample_count, dimension))
    y_a = _evaluate_matrix(a, demands, ranges)
    y_b = _evaluate_matrix(b, demands, ranges)
    indices: dict[str, dict[str, dict[str, float | None]]] = {
        output: {} for output in OUTPUT_NAMES
    }
    for output_index, output_name in enumerate(OUTPUT_NAMES):
        variance = float(np.var(np.concatenate((y_a[:, output_index], y_b[:, output_index]))))
        for parameter_index, parameter_name in enumerate(PARAMETER_NAMES):
            if variance <= 1e-15:
                first_raw = None
                total_raw = None
            else:
                ab = a.copy()
                ab[:, parameter_index] = b[:, parameter_index]
                y_ab = _evaluate_matrix(ab, demands, ranges)[:, output_index]
                first_raw = float(
                    np.mean(y_b[:, output_index] * (y_ab - y_a[:, output_index]))
                    / variance
                )
                total_raw = float(
                    0.5 * np.mean((y_a[:, output_index] - y_ab) ** 2) / variance
                )
            indices[output_name][parameter_name] = {
                "first_order_raw": first_raw,
                "total_order_raw": total_raw,
                "first_order_clipped": _clip_index(first_raw),
                "total_order_clipped": _clip_index(total_raw),
            }
    scores = {
        parameter_name: float(
            np.mean(
                [
                    indices[output][parameter_name]["total_order_clipped"] or 0.0
                    for output in OUTPUT_NAMES
                ]
            )
        )
        for parameter_name in PARAMETER_NAMES
    }
    return {
        "method": "saltelli_style_monte_carlo_pilot_v0.1",
        "status": "pilot_not_release_grade",
        "base_sample_count": sample_count,
        "model_evaluation_count": sample_count * (2 + len(PARAMETER_NAMES)),
        "seed": seed,
        "indices": indices,
        "ranking": _ranking(scores),
        "warnings": [
            "This low-cost pilot does not report convergence intervals.",
            "Raw finite-sample first-order estimates may be negative; clipped values are for ranking only.",
        ],
    }


def compare_resource_model_families(
    training_observations: Sequence[ResourceFitObservation],
    validation_observations: Sequence[ResourceFitObservation],
    *,
    context_id: str,
    dataset_id: str,
    bootstrap_samples: int = 20,
    bootstrap_seed: int = 2608,
) -> dict[str, Any]:
    training = tuple(training_observations)
    validation = tuple(validation_observations)
    if not training or not validation:
        raise ValueError("Model comparison requires training and validation observations.")
    fit = fit_resource_competition_parameters(
        training,
        context_id=context_id,
        dataset_id=dataset_id,
        bootstrap_samples=bootstrap_samples,
        bootstrap_seed=bootstrap_seed,
    )
    profile = fit.profile
    parameter_by_name = {item.parameter_name: item.value for item in profile.parameters}
    validation_demand = np.asarray([item.demand_index for item in validation])
    observed_capacity = np.asarray([item.observed_capacity_loss for item in validation])
    observed_growth = np.asarray([item.observed_relative_growth for item in validation])
    train_demand = np.asarray([item.demand_index for item in training])
    train_capacity = np.asarray([item.observed_capacity_loss for item in training])
    train_growth = np.asarray([item.observed_relative_growth for item in training])

    nonlinear_capacity, nonlinear_growth = predict_resource_response(
        validation_demand,
        parameter_by_name["aggregate_demand_coefficient"],
        parameter_by_name["normalized_growth_coupling"],
    )
    capacity_linear = np.polyfit(train_demand, train_capacity, 1)
    growth_linear = np.polyfit(train_demand, train_growth, 1)
    predictions = {
        "constant_training_median": (
            np.full_like(validation_demand, np.median(train_capacity)),
            np.full_like(validation_demand, np.median(train_growth)),
        ),
        "linear_demand_clipped": (
            np.clip(np.polyval(capacity_linear, validation_demand), 0.0, 1.0),
            np.clip(np.polyval(growth_linear, validation_demand), 0.0, 1.5),
        ),
        "resource_competition_fit_v0.1": (nonlinear_capacity, nonlinear_growth),
    }
    models = []
    for model_name, (capacity, growth) in predictions.items():
        capacity_rmse = _rmse(observed_capacity, capacity)
        growth_rmse = _rmse(observed_growth, growth)
        models.append(
            {
                "model_name": model_name,
                "capacity_rmse": capacity_rmse,
                "relative_growth_rmse": growth_rmse,
                "combined_rmse": math.sqrt(
                    (capacity_rmse**2 + growth_rmse**2) / 2.0
                ),
                "parameter_count": (
                    2
                    if model_name in {
                        "constant_training_median",
                        "resource_competition_fit_v0.1",
                    }
                    else 4
                ),
            }
        )
    models.sort(key=lambda item: (item["combined_rmse"], item["model_name"]))
    for rank, item in enumerate(models, start=1):
        item["rank"] = rank
    nonlinear = next(
        item for item in models if item["model_name"] == "resource_competition_fit_v0.1"
    )
    best_simple = min(
        (
            item
            for item in models
            if item["model_name"] != "resource_competition_fit_v0.1"
        ),
        key=lambda item: item["combined_rmse"],
    )
    denominator = max(float(best_simple["combined_rmse"]), 1e-12)
    improvement = (
        float(best_simple["combined_rmse"]) - float(nonlinear["combined_rmse"])
    ) / denominator
    return {
        "comparison_version": "frozen_holdout_model_comparison_v0.1",
        "training_observation_count": len(training),
        "validation_observation_count": len(validation),
        "profile_id": profile.profile_id,
        "profile_usable_for_prediction": profile.usable_for_prediction,
        "models": models,
        "best_model": models[0]["model_name"],
        "nonlinear_improvement_over_best_simple_fraction": improvement,
        "claim_boundary": "Comparative fit on one frozen split; not universal model superiority.",
    }


def _model_recommendation(
    comparison: Mapping[str, Any], validation_decision: str
) -> dict[str, Any]:
    improvement = float(
        comparison.get("nonlinear_improvement_over_best_simple_fraction") or 0.0
    )
    if validation_decision != "go":
        decision = "repair_validation_before_model_expansion"
        reason = "The source workflow did not pass held-out validation."
    elif comparison.get("best_model") != "resource_competition_fit_v0.1":
        decision = "investigate_model_mismatch_before_model_expansion"
        reason = "A simpler baseline performed as well as or better on the frozen holdout."
    elif improvement >= 0.10:
        decision = "retain_coarse_model_richer_model_not_yet_justified"
        reason = "The coarse nonlinear model materially outperformed simple baselines."
    else:
        decision = "collect_more_discriminating_data"
        reason = "The frozen holdout does not clearly discriminate model families."
    return {
        "decision": decision,
        "reason": reason,
        "nonlinear_improvement_threshold": 0.10,
        "automatic_action": False,
    }


def _resolve_ranges(
    workflow: Mapping[str, Any], value: Any
) -> tuple[dict[str, tuple[float, float]], str]:
    if value:
        if not isinstance(value, Mapping):
            raise ValueError("parameter_ranges must be an object.")
        parsed = {}
        for name, bounds in value.items():
            if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
                raise ValueError(f"Range for {name!r} must contain two values.")
            parsed[str(name)] = (float(bounds[0]), float(bounds[1]))
        return _validated_ranges(parsed), "explicit_analysis_request"
    parameters = (
        ((workflow.get("fit") or {}).get("profile") or {}).get("parameters") or []
    )
    fitted = {
        str(item.get("parameter_name")): float(item.get("value"))
        for item in parameters
        if item.get("parameter_name") in PARAMETER_NAMES
    }
    if len(fitted) == len(PARAMETER_NAMES):
        return (
            _validated_ranges(
                {name: (max(value * 0.5, 1e-6), value * 1.5) for name, value in fitted.items()}
            ),
            "fitted_profile_half_to_one_and_half",
        )
    return dict(DEFAULT_RANGES), "fixed_research_preview_defaults"


def _validated_ranges(
    ranges: Mapping[str, tuple[float, float]],
) -> dict[str, tuple[float, float]]:
    if set(ranges) != set(PARAMETER_NAMES):
        raise ValueError(f"parameter_ranges must contain exactly {list(PARAMETER_NAMES)}.")
    parsed = {}
    for name in PARAMETER_NAMES:
        lower, upper = (float(item) for item in ranges[name])
        if not math.isfinite(lower + upper) or lower <= 0.0 or upper <= lower:
            raise ValueError(f"Invalid positive ordered range for {name!r}.")
        parsed[name] = (lower, upper)
    return parsed


def _validated_demands(values: Sequence[float]) -> np.ndarray:
    demands = np.asarray(tuple(float(value) for value in values), dtype=float)
    if demands.size < 3 or not np.all(np.isfinite(demands)) or np.any(demands < 0):
        raise ValueError("Demand levels must contain at least three finite non-negative values.")
    return demands


def _evaluate_normalized(
    normalized: np.ndarray,
    demands: np.ndarray,
    ranges: Mapping[str, tuple[float, float]],
) -> np.ndarray:
    values = np.asarray(
        [ranges[name][0] + normalized[index] * (ranges[name][1] - ranges[name][0])
         for index, name in enumerate(PARAMETER_NAMES)]
    )
    capacity, growth = predict_resource_response(demands, values[0], values[1])
    return np.asarray((np.mean(capacity), np.mean(1.0 - growth)), dtype=float)


def _evaluate_matrix(
    normalized: np.ndarray,
    demands: np.ndarray,
    ranges: Mapping[str, tuple[float, float]],
) -> np.ndarray:
    demand_parameter = ranges[PARAMETER_NAMES[0]][0] + normalized[:, 0] * (
        ranges[PARAMETER_NAMES[0]][1] - ranges[PARAMETER_NAMES[0]][0]
    )
    growth_parameter = ranges[PARAMETER_NAMES[1]][0] + normalized[:, 1] * (
        ranges[PARAMETER_NAMES[1]][1] - ranges[PARAMETER_NAMES[1]][0]
    )
    demand = demands[np.newaxis, :]
    capacity = demand_parameter[:, np.newaxis] * demand / (
        1.0 + demand_parameter[:, np.newaxis] * demand
    )
    growth = np.exp(-growth_parameter[:, np.newaxis] * capacity)
    return np.column_stack((np.mean(capacity, axis=1), np.mean(1.0 - growth, axis=1)))


def _combined_sensitivity_scores(
    metrics: Mapping[str, Mapping[str, Mapping[str, float]]], key: str
) -> dict[str, float]:
    scores = {name: 0.0 for name in PARAMETER_NAMES}
    for output in OUTPUT_NAMES:
        maximum = max(metrics[output][name][key] for name in PARAMETER_NAMES) or 1.0
        for name in PARAMETER_NAMES:
            scores[name] += metrics[output][name][key] / maximum / len(OUTPUT_NAMES)
    return scores


def _ranking(scores: Mapping[str, float]) -> list[dict[str, Any]]:
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [
        {"rank": rank, "parameter_name": name, "combined_score": float(score)}
        for rank, (name, score) in enumerate(ordered, start=1)
    ]


def _clip_index(value: float | None) -> float | None:
    return None if value is None else float(np.clip(value, 0.0, 1.0))


def _rmse(observed: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.sqrt(np.mean((observed - predicted) ** 2)))


def _observation_from_dict(payload: dict[str, Any]) -> ResourceFitObservation:
    payload["source_metric_ids"] = tuple(payload.get("source_metric_ids") or ())
    return ResourceFitObservation(**payload)
