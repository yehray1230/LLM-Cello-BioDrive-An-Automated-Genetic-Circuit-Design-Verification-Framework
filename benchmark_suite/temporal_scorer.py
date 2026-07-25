from __future__ import annotations

import math
from typing import Any

from benchmark_suite.base_evaluator import EvaluationResult
from benchmark_suite.candidate_values import candidate_float as _candidate_float
from benchmark_suite.candidate_values import maybe_float as _maybe_float
from benchmark_suite.score_values import clamp01 as _clamp01

DEFAULT_TARGET_RISE_TIME = 180.0
DEFAULT_GATE_DELAY = 35.0


def score_temporal(candidate: dict[str, Any]) -> EvaluationResult:
    rise_time, source = _rise_time(candidate)
    if rise_time is None:
        return EvaluationResult(
            score=1.0,
            details={
                "metric": "temporal",
                "status": "skipped",
                "reason": "No timing trace, rise_time, gate_count, or logic_depth was provided.",
            },
            temporal_score=1.0,
            rise_time=None,
        )

    target = _candidate_float(candidate, "target_rise_time", DEFAULT_TARGET_RISE_TIME)
    score = math.exp(-max(0.0, rise_time - target) / max(target, 1e-9))
    return EvaluationResult(
        score=_clamp01(score),
        details={
            "metric": "temporal",
            "status": "ok",
            "rise_time": rise_time,
            "target_rise_time": target,
            "source": source,
        },
        temporal_score=_clamp01(score),
        rise_time=rise_time,
    )


def _rise_time(candidate: dict[str, Any]) -> tuple[float | None, str]:
    explicit = _maybe_float(candidate.get("rise_time", candidate.get("response_time")))
    if explicit is not None:
        return max(0.0, explicit), "explicit"

    trace_time = candidate.get("time") or candidate.get("t")
    trace_output = candidate.get("output") or candidate.get("y") or candidate.get("output_trace")
    if isinstance(trace_time, list) and isinstance(trace_output, list):
        inferred = _rise_time_from_trace(trace_time, trace_output, _candidate_float(candidate, "threshold_on", 0.5))
        if inferred is not None:
            return inferred, "trace"

    depth = _maybe_float(candidate.get("logic_depth", candidate.get("depth")))
    if depth is None:
        depth = _maybe_float(candidate.get("gate_count"))
    if depth is not None:
        delay = _candidate_float(candidate, "gate_delay_seconds", DEFAULT_GATE_DELAY)
        return max(0.0, depth * delay), "depth_estimate"

    return None, "missing"


def _rise_time_from_trace(times: list[Any], outputs: list[Any], threshold: float) -> float | None:
    for time_value, output_value in zip(times, outputs):
        time_number = _maybe_float(time_value)
        output_number = _maybe_float(output_value)
        if time_number is None or output_number is None:
            continue
        if output_number >= threshold:
            return max(0.0, time_number)
    return None
