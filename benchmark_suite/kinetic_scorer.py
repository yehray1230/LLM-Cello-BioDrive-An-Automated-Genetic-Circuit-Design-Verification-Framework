from __future__ import annotations

from typing import Any

from schemas.simulation import SIMULATION_MODEL_VERSION, stable_seed

import numpy as np

from benchmark_suite.base_evaluator import EvaluationResult
from benchmark_suite.candidate_values import candidate_float as _candidate_float
from benchmark_suite.candidate_values import candidate_int as _candidate_int
from tools.ode_simulator import BatchODESimulator

DEFAULT_MONTE_CARLO_RUNS = 20
DEFAULT_NOISE_LEVEL = 0.10
SNR_SCALE = 10.0


def _has_simulation_inputs(candidate: dict[str, Any]) -> bool:
    return any(
        key in candidate
        for key in ("verilog", "verilog_code", "gate_count", "biokinetic_parameters")
    )


def _snr_to_score(snr: float) -> float:
    if not np.isfinite(snr):
        return 0.0
    return max(0.0, min(1.0, snr / (snr + SNR_SCALE)))


def score_kinetic(candidate: dict[str, Any]) -> EvaluationResult:
    if not _has_simulation_inputs(candidate):
        score = float(candidate.get("kinetic_score", candidate.get("score", 0.0)))
        return EvaluationResult(score=score, details={"metric": "kinetic"})

    monte_carlo_runs = max(
        1,
        _candidate_int(
            candidate,
            "monte_carlo_runs",
            _candidate_int(candidate, "monte_carlo_samples", DEFAULT_MONTE_CARLO_RUNS),
        ),
    )
    noise_level = _candidate_float(
        candidate,
        "noise_level",
        _candidate_float(candidate, "noise_fraction", DEFAULT_NOISE_LEVEL),
    )
    simulator = BatchODESimulator(
        simulation_time=_candidate_float(candidate, "simulation_time", 600.0),
        sample_count=max(8, _candidate_int(candidate, "sample_count", 80)),
        monte_carlo_samples=1,
        noise_level=noise_level,
    )
    rng = np.random.default_rng(_stable_seed(candidate, monte_carlo_runs, noise_level))
    on_values: list[float] = []
    off_values: list[float] = []
    failed_runs = 0

    for _ in range(monte_carlo_runs):
        response = simulator.simulate_noisy_response(candidate, noise_level=noise_level, rng=rng)
        if not response.get("success"):
            failed_runs += 1
            continue
        on_values.append(float(response["on_value"]))
        off_values.append(float(response["off_value"]))

    if not on_values or not off_values:
        return EvaluationResult(
            score=0.0,
            details={
                "metric": "kinetic",
                "status": "error",
                "error": "All noisy ODE robustness simulations failed.",
                "monte_carlo_runs": monte_carlo_runs,
                "failed_runs": failed_runs,
                "noise_level": noise_level,
            },
            robustness_score=0.0,
            signal_to_noise_ratio=0.0,
            monte_carlo_runs=monte_carlo_runs,
        )

    on_array = np.asarray(on_values, dtype=float)
    off_array = np.asarray(off_values, dtype=float)
    min_signal = float(np.min(on_array))
    max_noise = float(np.max(off_array))
    collapsed = bool(max_noise >= min_signal)
    mean_on = float(np.mean(on_array))
    mean_off = float(np.mean(off_array))
    std_on = float(np.std(on_array))
    std_off = float(np.std(off_array))
    snr = max(0.0, (mean_on - mean_off) / max(std_on + std_off, 1e-9))
    success_rate = 0.0 if collapsed else len(on_values) / max(1, monte_carlo_runs)
    robustness_score = 0.0 if collapsed else 0.5 * success_rate + 0.5 * _snr_to_score(snr)
    if failed_runs and not collapsed:
        robustness_score *= (monte_carlo_runs - failed_runs) / monte_carlo_runs
    robustness_score = max(0.0, min(1.0, robustness_score))

    return EvaluationResult(
        score=robustness_score,
        details={
            "metric": "kinetic",
            "status": "ok",
            "monte_carlo_runs": monte_carlo_runs,
            "noise_level": noise_level,
            "failed_runs": failed_runs,
            "collapsed": collapsed,
            "min_signal": min_signal,
            "max_noise": max_noise,
            "mean_on": mean_on,
            "mean_off": mean_off,
            "std_on": std_on,
            "std_off": std_off,
        },
        robustness_score=robustness_score,
        signal_to_noise_ratio=snr,
        monte_carlo_runs=monte_carlo_runs,
    )


def _stable_seed(candidate: dict[str, Any], monte_carlo_runs: int, noise_level: float) -> int:
    return stable_seed(
        {
            "model_version": SIMULATION_MODEL_VERSION,
            "verilog": candidate.get("verilog", candidate.get("verilog_code", "")),
            "gate_count": candidate.get("gate_count"),
            "biokinetic_parameters": candidate.get("biokinetic_parameters"),
            "monte_carlo_runs": monte_carlo_runs,
            "noise_level": round(float(noise_level), 6),
        }
    )
