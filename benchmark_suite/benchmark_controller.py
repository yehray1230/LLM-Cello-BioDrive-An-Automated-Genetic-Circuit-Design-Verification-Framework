from __future__ import annotations

from typing import Any

from benchmark_suite.candidate_values import candidate_float as _candidate_float
from benchmark_suite.candidate_values import candidate_int as _candidate_int
from benchmark_suite.cello_constraint_evaluator import score_cello_constraints
from benchmark_suite.functional_scorer import score_functional
from benchmark_suite.kinetic_scorer import score_kinetic
from benchmark_suite.metabolic_scorer import score_metabolic_burden
from benchmark_suite.score_values import clamp01 as _clamp_score
from benchmark_suite.static_plausibility_evaluator import score_static_plausibility
from benchmark_suite.temporal_scorer import score_temporal
from benchmark_suite.scoring_profiles import (
    LEGACY_PROFILE,
    RESEARCH_PROFILE,
    SIMULATION_RESEARCH_PROFILE,
    get_scoring_profile,
)
from utils.boolean_values import defaulted_bool

SCORE_WEIGHTS = LEGACY_PROFILE.dimension_weights

RESEARCH_V2_BIOPHYSICAL_WEIGHTS = dict(
    SIMULATION_RESEARCH_PROFILE.biophysical_weights or {}
)


def _candidate_bool(candidate: dict[str, Any], key: str, default: bool) -> bool:
    return defaulted_bool(candidate.get(key), default)


def _candidate_str_list(candidate: dict[str, Any], key: str, default: list[str]) -> list[str]:
    value = candidate.get(key)
    if value is None:
        return default
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    return default


def evaluate_candidate(
    candidate: dict[str, Any],
    *,
    profile_id: str | None = None,
) -> dict[str, Any]:
    profile = get_scoring_profile(profile_id)
    results = [
        score_functional(candidate),
        score_kinetic(candidate),
        score_static_plausibility(candidate),
        score_metabolic_burden(candidate),
        score_temporal(candidate),
        score_cello_constraints(candidate),
    ]
    component_scores = {
        str(result.details.get("metric", "unknown")): _clamp_score(result.score)
        for result in results
    }
    metabolic_result = next(
        result for result in results if result.details.get("metric") == "metabolic_burden"
    )
    kinetic_result = next(
        result for result in results if result.details.get("metric") == "kinetic"
    )
    cello_result = next(
        result for result in results if result.details.get("metric") == "cello_constraints"
    )
    temporal_result = next(
        result for result in results if result.details.get("metric") == "temporal"
    )
    robustness_score = _candidate_float(
        candidate,
        "robustness_score",
        kinetic_result.robustness_score,
    )
    orthogonality_score = cello_result.orthogonality_score
    cello_assignment_score = _candidate_float(
        candidate,
        "cello_assignment_score",
        cello_result.cello_assignment_score,
    )
    cello_buildable = cello_result.cello_buildable
    temporal_score = temporal_result.temporal_score
    rise_time = temporal_result.rise_time
    semantic_faithfulness_score = _candidate_float(candidate, "semantic_faithfulness_score", 1.0)
    missed_edge_cases = _candidate_str_list(
        candidate,
        "missed_edge_cases",
        _candidate_str_list(candidate, "missed_conditions", []),
    )
    component_scores["robustness"] = _clamp_score(robustness_score)
    component_scores["temporal"] = _clamp_score(temporal_score)
    component_scores["orthogonality"] = _clamp_score(orthogonality_score)
    component_scores["cello_assignment"] = _clamp_score(cello_assignment_score)
    dimension_scores, applicability = _dimension_scores(
        candidate,
        component_scores,
    )

    diagnostic_tags = []
    if _candidate_float(candidate, "retroactivity_max", 0.0) > 0.3:
        diagnostic_tags.append("HIGH_RETROACTIVITY")
    if candidate.get("rbs_blocking_detected"):
        diagnostic_tags.append("RBS_HAIRPIN_DETECTED")
    cv = _candidate_float(candidate, "monte_carlo_terminal_output_cv", _candidate_float(candidate, "metrics_cv", 0.0))
    if cv > 0.5:
        diagnostic_tags.append("NOISE_FLIP_RISK")

    research_profiles = {
        RESEARCH_PROFILE.profile_id,
        SIMULATION_RESEARCH_PROFILE.profile_id,
    }
    applied_score_weights = profile.dimension_weights
    biophysical_component_scores = None
    if profile.profile_id == "research-v2-preview" and candidate.get("ode_status") == "simulated":
        component_scores_v2 = {
            "logic": _clamp_score(semantic_faithfulness_score),
            "noise_resilience": 1.0 - _clamp_score(cv * cv),
            "retroactivity_resilience": 1.0
            - _clamp_score(_candidate_float(candidate, "retroactivity_max", 0.0)),
            "rbs_accessibility": 0.0
            if candidate.get("rbs_blocking_detected")
            else 1.0,
            "resource_burden": _clamp_score(metabolic_result.score),
        }
        biophysical_component_scores = component_scores_v2
        applied_score_weights = dict(profile.biophysical_weights or {})
        biophysical_score = sum(
            applied_score_weights[name] * component_score
            for name, component_score in component_scores_v2.items()
        )
        score = _clamp_score(biophysical_score)
    elif profile.profile_id in research_profiles:
        score = _weighted_score(dimension_scores, profile.dimension_weights)
    else:
        score = _weighted_score(component_scores, profile.dimension_weights)
    return {
        "score": score,
        "weighted_total_score": score,
        "computational_design_score": score,
        "grade": _grade(score),
        "diagnostic_tags": diagnostic_tags,
        "metabolic_burden_score": metabolic_result.metabolic_burden_score,
        "gate_count": metabolic_result.gate_count,
        "complexity_penalty": metabolic_result.complexity_penalty,
        "robustness_score": robustness_score,
        "signal_to_noise_ratio": _candidate_float(
            candidate,
            "signal_to_noise_ratio",
            _candidate_float(candidate, "snr", 0.0),
        ),
        "monte_carlo_runs": _candidate_int(
            candidate,
            "monte_carlo_runs",
            _candidate_int(candidate, "monte_carlo_samples", 0),
        ),
        "temporal_score": temporal_score,
        "rise_time": rise_time,
        "orthogonality_score": orthogonality_score,
        "cello_assignment_score": cello_assignment_score,
        "cello_buildable": cello_buildable,
        "toxicity": cello_result.details.get("toxicity"),
        "toxicity_score": cello_result.details.get("toxicity_score"),
        "semantic_faithfulness_score": semantic_faithfulness_score,
        "missed_edge_cases": missed_edge_cases,
        "component_scores": component_scores,
        "score_weights": applied_score_weights,
        "biophysical_component_scores": biophysical_component_scores,
        "dimension_scores": dimension_scores,
        "dimension_applicability": applicability,
        "scoring_profile": profile.profile_id,
        "scoring_version": profile.version,
        "scoring_configuration_hash": profile.configuration_hash,
        "simulation_model_version": candidate.get("simulation_model_version"),
        "simulation_configuration_hash": (
            candidate.get("simulation_spec", {}).get("configuration_hash")
            if isinstance(candidate.get("simulation_spec"), dict)
            else None
        ),
        "details": [
            result.details
            | {
                "score": result.score,
                "weight": profile.dimension_weights.get(
                    str(result.details.get("metric", "")),
                    0.0,
                ),
                "metabolic_burden_score": result.metabolic_burden_score,
                "gate_count": result.gate_count,
                "complexity_penalty": result.complexity_penalty,
                "robustness_score": result.robustness_score,
                "signal_to_noise_ratio": result.signal_to_noise_ratio,
                "monte_carlo_runs": result.monte_carlo_runs,
                "temporal_score": result.temporal_score,
                "rise_time": result.rise_time,
                "orthogonality_score": result.orthogonality_score,
                "cello_assignment_score": result.cello_assignment_score,
                "cello_buildable": result.cello_buildable,
                "semantic_faithfulness_score": result.semantic_faithfulness_score,
                "missed_edge_cases": result.missed_edge_cases or [],
            }
            for result in results
        ],
        "scoring_model": (
            "multidimensional_research_score"
            if profile.profile_id in research_profiles
            else "weighted_total_score"
        ),
    }


def _dimension_scores(
    candidate: dict[str, Any],
    components: dict[str, float],
) -> tuple[dict[str, float], dict[str, str]]:
    evidence_quality, evidence_status = _evidence_quality(candidate)
    completeness, completeness_status = _data_completeness(candidate)
    semantic_faithfulness = _candidate_float(candidate, "semantic_faithfulness_score", 1.0)
    dimensions = {
        "logic_function": components.get("functional", 0.0),
        "dynamic_behavior": _mean(
            components.get("kinetic", 0.0),
            components.get("temporal", 0.0),
        ),
        "robustness": components.get("robustness", 0.0),
        "resource_burden": components.get("metabolic_burden", 0.0),
        "buildability": _mean(
            components.get("static_plausibility", 0.0),
            components.get("orthogonality", 0.0),
            components.get("cello_assignment", 0.0),
        ),
        "evidence_quality": evidence_quality,
        "data_completeness": completeness,
        "semantic_faithfulness": semantic_faithfulness,
    }
    applicability = {
        "logic_function": "measured_or_derived",
        "dynamic_behavior": "measured_or_derived",
        "robustness": "measured_or_derived",
        "resource_burden": "derived",
        "buildability": "derived",
        "evidence_quality": evidence_status,
        "data_completeness": completeness_status,
        "semantic_faithfulness": "explicit" if "semantic_faithfulness_score" in candidate else "defaulted",
    }
    return (
        {key: _clamp_score(value) for key, value in dimensions.items()},
        applicability,
    )


def _evidence_quality(candidate: dict[str, Any]) -> tuple[float, str]:
    explicit = _optional_score(candidate.get("evidence_quality"))
    if explicit is not None:
        return explicit, "explicit"
    statuses = candidate.get("evidence_statuses")
    if not isinstance(statuses, list):
        statuses = candidate.get("field_provenance")
        if isinstance(statuses, list):
            statuses = [
                item.get("status")
                for item in statuses
                if isinstance(item, dict)
            ]
    if not isinstance(statuses, list) or not statuses:
        return 0.0, "not_reported"
    levels = {
        "explicit": 1.0,
        "derived": 0.75,
        "inferred": 0.5,
        "assumed": 0.25,
        "defaulted": 0.2,
        "unknown": 0.0,
        "not_reported": 0.0,
    }
    values = [levels.get(str(status).lower(), 0.0) for status in statuses]
    return _mean(*values), "derived"


def _data_completeness(candidate: dict[str, Any]) -> tuple[float, str]:
    explicit = _optional_score(
        candidate.get("data_completeness", candidate.get("completeness"))
    )
    if explicit is not None:
        return explicit, "explicit"
    fields = (
        "functional_score",
        "kinetic_score",
        "robustness_score",
        "plausibility_score",
        "orthogonality_score",
        "cello_assignment_score",
        "evidence_quality",
    )
    available = sum(candidate.get(key) is not None for key in fields)
    return available / len(fields), "derived"


def _weighted_score(
    scores: dict[str, float],
    weights: dict[str, float],
) -> float:
    return round(
        sum(_clamp_score(scores.get(metric, 0.0)) * weight for metric, weight in weights.items()),
        10,
    )


def _optional_score(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return _clamp_score(float(value))
    except (TypeError, ValueError):
        return None


def _mean(*values: float) -> float:
    return sum(values) / len(values) if values else 0.0


def _grade(score: float) -> str:
    scaled = score * 100.0
    if scaled >= 80.0:
        return "Excellent"
    if scaled >= 60.0:
        return "Pass"
    return "Fail"
