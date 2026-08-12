from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any

from application.demo_constants import DEMO_BASELINE_VERILOG
from benchmark_suite.design_task_dataset import (
    DesignTask,
    load_design_task_set,
    validate_exp003_task_set,
)
from schemas.run_manifest import payload_sha256
from schemas import TemporalEvaluatorConfig, DEFAULT_TEMPORAL_CONFIG, PhaseWindow



EXP003_TASK_SET_ID = "exp003_design_tasks_v1"
RUNNER_VERSION = "1.0"
DEFAULT_PROFILE_ID = "research-v2-preview"
DEFAULT_RANDOM_SEED = 20260629


def run_exp003_design_task_benchmark(
    services: Any,
    *,
    output_dir: str | Path,
    timeout_seconds: float = 60.0,
    profile_id: str = DEFAULT_PROFILE_ID,
    evaluator_config: TemporalEvaluatorConfig | None = None,
) -> dict[str, Any]:
    task_set = load_design_task_set(EXP003_TASK_SET_ID)
    errors = validate_exp003_task_set(task_set)
    if errors:
        raise ValueError("Invalid EXP-003 task set: " + " ".join(errors))

    config = evaluator_config or DEFAULT_TEMPORAL_CONFIG

    results = [
        _run_task(
            services,
            task,
            task_set_metadata={
                "task_set_id": task_set.task_set_id,
                "version": task_set.version,
                "content_hash": task_set.content_hash,
            },
            timeout_seconds=timeout_seconds,
            profile_id=profile_id,
            evaluator_config=config,
        )
        for task in task_set.tasks
    ]
    summary = _batch_summary(results)
    batch = {
        "packet_type": "exp003_design_task_benchmark",
        "packet_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runner": {
            "name": "deterministic_design_task_runner",
            "version": RUNNER_VERSION,
            "configuration": "deterministic_fixture_v1",
            "profile_id": profile_id,
            "random_seed": DEFAULT_RANDOM_SEED,
            "temporal_evaluator_version": config.version,
            "temporal_evaluator_config": config.to_dict(),
        },

        "task_set": {
            "task_set_id": task_set.task_set_id,
            "version": task_set.version,
            "content_hash": task_set.content_hash,
            "task_count": len(task_set.tasks),
        },
        "claim_boundary": (
            "Deterministic computational benchmark scaffolding only. The runner "
            "does not establish wet-lab validity or LLM orchestration quality."
        ),
        "summary": summary,
        "results": results,
    }
    batch["stable_result_hash"] = stable_batch_hash(batch)
    batch["artifacts"] = _write_batch_artifacts(batch, output_dir)
    return batch


def stable_batch_hash(batch: dict[str, Any]) -> str:
    import re

    stable_results = []
    for result in batch.get("results", []):
        if not isinstance(result, dict):
            continue
        research = result.get("research_run")
        if not isinstance(research, dict):
            research = {}
        stable_results.append(
            {
                "task_id": result.get("task_id"),
                "category": result.get("category"),
                "status": result.get("status"),
                "passed": result.get("passed"),
                "execution_mode": result.get("execution_mode"),
                "candidate_generated": result.get("candidate_generated"),
                "evaluation": result.get("evaluation"),
                "blocking_reason": result.get("blocking_reason"),
                "response": result.get("response"),
                "research": {
                    "simulation_status": research.get("simulation_status"),
                    "configuration_hash": research.get("configuration_hash"),
                    "result_hash": research.get("result_hash"),
                    "scoring_profile": research.get("scoring_profile"),
                    "scoring_version": research.get("scoring_version"),
                    "weighted_total_score": research.get("weighted_total_score"),
                },
            }
        )
    stable_payload = {
        "packet_type": batch.get("packet_type"),
        "packet_version": batch.get("packet_version"),
        "runner": batch.get("runner"),
        "task_set": batch.get("task_set"),
        "summary": batch.get("summary"),
        "results": stable_results,
    }

    # Recursive sanitizer to strip timestamps, run IDs, and absolute paths
    def sanitize(val: Any) -> Any:
        if isinstance(val, dict):
            cleaned = {}
            for k, v in val.items():
                if k in {"run_id", "timestamp", "created_at", "started_at", "finished_at", "absolute_path"}:
                    continue
                cleaned[k] = sanitize(v)
            return cleaned
        elif isinstance(val, list):
            return [sanitize(item) for item in val]
        elif isinstance(val, str):
            # Windows absolute path
            val = re.sub(r'[a-zA-Z]:[\\/][^:\n\r]*', '[ABSOLUTE_PATH]', val)
            # Unix absolute path
            val = re.sub(r'/[a-zA-Z0-9_\.\-]+(?:/[a-zA-Z0-9_\.\-]+)+', '[ABSOLUTE_PATH]', val)
            # ISO timestamps
            val = re.sub(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?', '[TIMESTAMP]', val)
            # UUIDs (Run IDs)
            val = re.sub(r'[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}', '[RUN_ID]', val)
            return val
        return val

    sanitized_payload = sanitize(stable_payload)
    return payload_sha256(sanitized_payload)


def _run_task(
    services: Any,
    task: DesignTask,
    *,
    task_set_metadata: dict[str, Any],
    timeout_seconds: float,
    profile_id: str,
    evaluator_config: TemporalEvaluatorConfig = DEFAULT_TEMPORAL_CONFIG,
) -> dict[str, Any]:
    mode = str(task.expected.get("evaluation_mode") or "")
    if mode == "clarification_required":
        return _clarification_result(task)
    if mode not in {"combinational_logic", "stateful_temporal", "oscillatory_temporal"}:
        return _unsupported_temporal_result(task)

    topology = _deterministic_topology(task)

    simulation_time = 120.0
    sample_count = 24
    temporal_inputs = None

    if mode == "stateful_temporal":
        simulation_time = 5000.0
        sample_count = 101
        temporal_inputs = {
            "SET": [
                {"start": 0.0, "end": 1000.0, "value": 200.0},
                {"start": 1000.0, "end": 2000.0, "value": 0.0},
                {"start": 2000.0, "end": 3000.0, "value": 0.0},
                {"start": 3000.0, "end": 4000.0, "value": 0.0},
                {"start": 4000.0, "end": 5000.0, "value": 200.0},
            ],
            "RESET": [
                {"start": 0.0, "end": 1000.0, "value": 0.0},
                {"start": 1000.0, "end": 2000.0, "value": 0.0},
                {"start": 2000.0, "end": 3000.0, "value": 200.0},
                {"start": 3000.0, "end": 4000.0, "value": 0.0},
                {"start": 4000.0, "end": 5000.0, "value": 200.0},
            ],
        }
    elif mode == "oscillatory_temporal":
        simulation_time = 8000.0
        sample_count = 1601

    request = {
        "topology": topology,
        "simulation_time": simulation_time,
        "sample_count": sample_count,
        "monte_carlo_samples": 1,
        "noise_fraction": 0.15,
        "random_seed": DEFAULT_RANDOM_SEED,
        "profile_id": profile_id,
        "user_intent": task.request,
        "temporal_inputs": temporal_inputs,
        "datasets": [
            {
                **task_set_metadata,
                "task_id": task.task_id,
            }
        ],
    }
    started = services.research.start_simulation(request)
    research_result = _wait_for_research_result(
        services,
        str(started["run_id"]),
        timeout_seconds=timeout_seconds,
    )

    if mode == "combinational_logic":
        evaluation = _evaluate_combinational_task(task, research_result)
    elif mode == "stateful_temporal":
        evaluation = _evaluate_stateful_temporal_task(task, research_result, config=evaluator_config)
    elif mode == "oscillatory_temporal":
        evaluation = _evaluate_oscillatory_temporal_task(task, research_result, config=evaluator_config)

    else:
        evaluation = {"passed": False}

    simulation = dict(research_result.get("simulation_result") or {})
    score = dict(research_result.get("evaluation") or {})

    # Compute status and passed based on mode and evaluation results
    if mode == "oscillatory_temporal":
        if evaluation.get("passed"):
            if (evaluator_config.oscillator_profile.maximum_period_cv is None or
                evaluator_config.oscillator_profile.minimum_amplitude_retention is None):
                status = "provisional"
                passed = False
            else:
                status = "passed"
                passed = True
        else:
            status = "failed"
            passed = False
    else:
        status = "passed" if evaluation.get("passed") else "failed"
        passed = evaluation.get("passed") or False

    return {
        "task_id": task.task_id,
        "category": task.category,
        "status": status,
        "passed": passed,
        "execution_mode": "deterministic_fixture",
        "candidate_generated": True,
        "evaluation": evaluation,
        "research_run": {
            "run_id": research_result.get("research_run_id"),
            "run_manifest_path": started.get("run_manifest_path"),
            "simulation_status": simulation.get("status"),
            "configuration_hash": simulation.get("configuration_hash"),
            "result_hash": simulation.get("result_hash"),
            "scoring_profile": score.get("scoring_profile"),
            "scoring_version": score.get("scoring_version"),
            "weighted_total_score": score.get("weighted_total_score"),
            "artifacts": dict(research_result.get("artifacts") or {}),
        },
    }


def _deterministic_topology(task: DesignTask) -> dict[str, Any]:
    verilog_by_task = {
        "fsv_not_a_gfp_v1": (
            "module fsv_not_a(input A, output GFP); "
            "assign GFP = ~A; endmodule"
        ),
        "fsv_a_and_b_gfp_v1": (
            "module fsv_a_and_b(input A, input B, output GFP); "
            "assign GFP = A & B; endmodule"
        ),
        "reporter_a_or_b_v1": (
            "module reporter_a_or_b(input A, input B, output reporter); "
            "assign reporter = A | B; endmodule"
        ),
        "cello_a_and_not_b_gfp_v1": DEMO_BASELINE_VERILOG,
        "toggle_set_reset_v1": (
            "module toggle_switch(input SET, input RESET, output GFP, output Qbar); "
            "nor g1(GFP, RESET, Qbar); nor g2(Qbar, SET, GFP); endmodule"
        ),
        "oscillator_repressilator_v1": (
            "module repressilator(output GFP); "
            "wire B, C; not g1(GFP, C); not g2(B, GFP); not g3(C, B); endmodule"
        ),
    }
    verilog = verilog_by_task.get(task.task_id)
    if not verilog:
        raise ValueError(
            f"No deterministic topology fixture is registered for {task.task_id}."
        )

    biokinetic_parameters = {}
    chassis = "Escherichia coli"

    if task.task_id == "toggle_set_reset_v1":
        chassis = "Synthetic Toggle Host"
        biokinetic_parameters = {
            "parameters": {
                "protein_degradation_rate": {"value": 0.008, "unit": "1/s"},
                "transcription_rate": {"value": 0.5, "unit": "nM/s"},
            }
        }
    elif task.task_id == "oscillator_repressilator_v1":
        chassis = "Synthetic Oscillator Host"
        biokinetic_parameters = {
            "parameters": {
                "protein_degradation_rate": {"value": 0.008, "unit": "1/s"},
                "transcription_rate": {"value": 0.5, "unit": "nM/s"},
                "hill_coefficient": {"value": 3.0, "unit": "dimensionless"},
                "kd_GFP": {"value": 30.0, "unit": "nM"},
                "kd_B": {"value": 50.0, "unit": "nM"},
                "kd_C": {"value": 70.0, "unit": "nM"},
            }
        }

    return {
        "topology_id": f"benchmark_{task.task_id}",
        "benchmark_task_id": task.task_id,
        "user_intent": task.request,
        "verilog": verilog,
        "truth_table": deepcopy(task.expected.get("truth_table") or []),
        "chassis": chassis,
        "copy_number": 3,
        "biokinetic_parameters": biokinetic_parameters,
        "cello_mode": "not_run",
        "cello_claim_level": "not_mapped",
        "mapping_status": "not_mapped",
        "cello_warning": (
            "Deterministic task benchmark uses direct topology simulation; "
            "Cello mapping is not claimed."
        ),
    }


def _evaluate_combinational_task(
    task: DesignTask,
    research_result: dict[str, Any],
) -> dict[str, Any]:
    candidate = dict(research_result.get("candidate") or {})
    simulation = dict(research_result.get("simulation_result") or {})
    scoring = dict(research_result.get("evaluation") or {})
    expected_truth_table = _normalized_truth_table(task.expected.get("truth_table"))
    actual_truth_table = _normalized_truth_table(candidate.get("truth_table"))
    truth_table_match = actual_truth_table == expected_truth_table
    simulation_completed = simulation.get("status") == "simulated"
    functional_score = float(
        dict(scoring.get("component_scores") or {}).get("functional", 0.0)
    )
    passed = truth_table_match and simulation_completed and functional_score == 1.0
    return {
        "evaluation_mode": "combinational_logic",
        "passed": passed,
        "truth_table_match": truth_table_match,
        "expected_row_count": len(expected_truth_table),
        "actual_row_count": len(actual_truth_table),
        "simulation_completed": simulation_completed,
        "functional_score": functional_score,
        "logic_expression": task.expected.get("logic_expression"),
        "claim_level": "deterministic_fixture_evaluation",
    }


def _evaluate_stateful_temporal_task(
    task: DesignTask,
    research_result: dict[str, Any],
    config: TemporalEvaluatorConfig = DEFAULT_TEMPORAL_CONFIG,
) -> dict[str, Any]:
    candidate = dict(research_result.get("candidate") or {})
    simulation = dict(research_result.get("simulation_result") or {})
    simulation_completed = simulation.get("status") == "simulated"

    ode_trace = candidate.get("ode_trace") or {}
    times = ode_trace.get("time") or []
    outputs = ode_trace.get("output_protein") or []

    passed = False
    details = []
    actual_hold_margin = None
    simultaneous_policy = None

    if simulation_completed and times and outputs:
        p_windows = config.toggle_profile.phase_windows
        high_threshold = config.toggle_profile.high_threshold
        low_threshold = config.toggle_profile.low_threshold
        minimum_hold_margin = config.toggle_profile.minimum_hold_margin

        # Helper to compute phase details
        def get_phase_metrics(name: str, w: PhaseWindow, is_high: bool) -> dict[str, Any]:
            vals = [val for t, val in zip(times, outputs) if w.start <= t <= w.end]
            count = len(vals)
            thresh = high_threshold if is_high else low_threshold

            if count == 0:
                return {
                    "phase": name,
                    "window": [w.start, w.end],
                    "sample_count": 0,
                    "minimum": 0.0,
                    "maximum": 0.0,
                    "terminal_value": 0.0,
                    "threshold": thresh,
                    "margin": -thresh,
                    "passed": False
                }

            p_min = float(min(vals))
            p_max = float(max(vals))
            p_term = float(vals[-1])

            if is_high:
                p_margin = p_min - thresh
                p_passed = p_margin >= 0.0
            else:
                p_margin = thresh - p_max
                p_passed = p_margin >= 0.0

            return {
                "phase": name,
                "window": [w.start, w.end],
                "sample_count": count,
                "minimum": round(p_min, 4),
                "maximum": round(p_max, 4),
                "terminal_value": round(p_term, 4),
                "threshold": thresh,
                "margin": round(p_margin, 4),
                "passed": p_passed
            }

        # Evaluate 4 primary phases
        p1_res = get_phase_metrics("SET", p_windows["phase1_end"], is_high=True)
        p2_res = get_phase_metrics("HOLD_SET", p_windows["phase2"], is_high=True)
        p3_res = get_phase_metrics("RESET", p_windows["phase3_end"], is_high=False)
        p4_res = get_phase_metrics("HOLD_RESET", p_windows["phase4"], is_high=False)

        details = [p1_res, p2_res, p3_res, p4_res]

        # Calculate actual hold margin (difference between minimum high value and maximum low value in windows)
        min_high = min(p1_res["minimum"], p2_res["minimum"]) if (p1_res["sample_count"] > 0 and p2_res["sample_count"] > 0) else 0.0
        max_low = max(p3_res["maximum"], p4_res["maximum"]) if (p3_res["sample_count"] > 0 and p4_res["sample_count"] > 0) else 0.0
        actual_hold_margin = min_high - max_low
        margin_ok = actual_hold_margin >= minimum_hold_margin

        # Evaluate simultaneous SET/RESET phase
        sim_window = p_windows.get("simultaneous")
        if sim_window:
            sim_vals = [val for t, val in zip(times, outputs) if sim_window.start <= t <= sim_window.end]
            if len(sim_vals) > 0:
                sim_term = sim_vals[-1]
                if sim_term < low_threshold:
                    simultaneous_policy = "dominant_reset"
                elif sim_term > high_threshold:
                    simultaneous_policy = "dominant_set"
                else:
                    simultaneous_policy = "undefined_state"
            else:
                simultaneous_policy = "invalid_input"
        else:
            simultaneous_policy = "undefined_state"

        # Overall pass requires all 4 phases to pass and global hold margin to be satisfied
        primary_phases_ok = p1_res["passed"] and p2_res["passed"] and p3_res["passed"] and p4_res["passed"]
        passed = primary_phases_ok and margin_ok and (simultaneous_policy is not None)

    return {
        "evaluation_mode": "stateful_temporal",
        "passed": passed,
        "simulation_completed": simulation_completed,
        "details": details,
        "claim_level": "deterministic_fixture_evaluation",
        "evaluator_version": config.version,
        "evaluator_config": config.toggle_profile.to_dict(),
        "actual_hold_margin": actual_hold_margin,
        "simultaneous_policy": simultaneous_policy,
    }




def _evaluate_oscillatory_temporal_task(
    task: DesignTask,
    research_result: dict[str, Any],
    config: TemporalEvaluatorConfig = DEFAULT_TEMPORAL_CONFIG,
) -> dict[str, Any]:
    candidate = dict(research_result.get("candidate") or {})
    simulation = dict(research_result.get("simulation_result") or {})
    simulation_completed = simulation.get("status") == "simulated"

    ode_trace = candidate.get("ode_trace") or {}
    times = ode_trace.get("time") or []
    outputs = ode_trace.get("output_protein") or []

    passed = False
    peaks = []
    valleys = []
    classification = "non-oscillatory"
    periods = []
    mean_period = 0.0
    period_cv = 0.0
    amplitudes = []
    amplitude_retention = 0.0
    trace_validation_errors = []

    transient_cutoff = config.oscillator_profile.transient_cutoff
    minimum_peak_count = config.oscillator_profile.minimum_peak_count
    minimum_amplitude = config.oscillator_profile.minimum_amplitude

    if len(times) != len(outputs):
        trace_validation_errors.append(
            "ode_trace.time and ode_trace.output_protein must have equal lengths."
        )
        classification = "invalid-trace"

    trace_valid = not trace_validation_errors
    if simulation_completed and trace_valid and len(times) > 3:
        for i in range(1, len(times) - 1):
            t = times[i]
            if t < transient_cutoff:
                continue
            val = outputs[i]
            prev_val = outputs[i - 1]
            next_val = outputs[i + 1]

            if val > prev_val and val > next_val:
                peaks.append((t, val))
            elif val < prev_val and val < next_val:
                valleys.append((t, val))

        peak_count = len(peaks)

        # Compute periods, mean period, and period CV if we have at least 2 peaks
        if peak_count >= 2:
            periods = [peaks[j][0] - peaks[j-1][0] for j in range(1, peak_count)]
            mean_period = sum(periods) / len(periods)
            if len(periods) >= 2:
                variance = sum((p - mean_period) ** 2 for p in periods) / len(periods)
                std_period = variance ** 0.5
                period_cv = std_period / mean_period if mean_period > 0 else 0.0
            else:
                period_cv = 0.0

        # Compute cycle amplitudes (subsequent valley difference)
        for pt, pv in peaks:
            subsequent_valleys = [vv for vt, vv in valleys if vt > pt]
            if subsequent_valleys:
                amplitudes.append(pv - subsequent_valleys[0])

        if len(amplitudes) >= 2:
            amplitude_retention = amplitudes[-1] / amplitudes[0] if amplitudes[0] > 0 else 0.0
        elif len(amplitudes) == 1:
            amplitude_retention = 1.0
        else:
            amplitude_retention = 0.0

        # Backward compatible combined amplitudes for threshold checking
        combined_amplitudes = list(amplitudes)
        for pt, pv in peaks:
            prior_valleys = [vv for vt, vv in valleys if vt < pt]
            if prior_valleys:
                combined_amplitudes.append(pv - prior_valleys[-1])

        amplitude_ok = bool(combined_amplitudes) and all(
            amp >= minimum_amplitude for amp in combined_amplitudes
        )

        if peak_count < minimum_peak_count:
            classification = "non-oscillatory"
        elif not amplitude_ok:
            classification = "non-oscillatory"
        else:
            # Evaluate CV and retention constraints
            period_cv_ok = True
            if config.oscillator_profile.maximum_period_cv is not None:
                if peak_count < 3:
                    period_cv_ok = False
                else:
                    period_cv_ok = period_cv <= config.oscillator_profile.maximum_period_cv

            retention_ok = True
            if config.oscillator_profile.minimum_amplitude_retention is not None:
                if len(amplitudes) < 2:
                    retention_ok = False
                else:
                    retention_ok = amplitude_retention >= config.oscillator_profile.minimum_amplitude_retention

            if not period_cv_ok:
                classification = "irregular"
            elif not retention_ok:
                classification = "damped"
            else:
                classification = "sustained"

        passed = (peak_count >= minimum_peak_count) and amplitude_ok and (classification == "sustained")

    return {
        "evaluation_mode": "oscillatory_temporal",
        "passed": passed,
        "simulation_completed": simulation_completed,
        "classification": classification,
        "evaluation_window": [
            transient_cutoff,
            float(times[-1]) if trace_valid and times else 0.0,
        ],
        "peak_count": len(peaks),
        "periods": [round(p, 4) for p in periods],
        "mean_period": round(mean_period, 4),
        "period_cv": round(period_cv, 4),
        "amplitudes": [round(a, 4) for a in amplitudes],
        "amplitude_retention": round(amplitude_retention, 4),
        "trace_valid": trace_valid,
        "trace_validation_errors": trace_validation_errors,
        "claim_level": "deterministic_fixture_evaluation",
        "evaluator_version": config.version,
        "evaluator_config": config.oscillator_profile.to_dict(),
    }



def _clarification_result(task: DesignTask) -> dict[str, Any]:
    clarifications = [
        str(value) for value in task.expected.get("required_clarifications", [])
    ]
    response = {
        "status": "clarification_required",
        "questions": clarifications,
        "message": (
            "The request is underspecified; no candidate was generated until "
            "the required biological context is provided."
        ),
    }
    passed = task.expected.get("candidate_generation_allowed") is False and bool(
        clarifications
    )
    return {
        "task_id": task.task_id,
        "category": task.category,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "execution_mode": "clarification_gate",
        "candidate_generated": False,
        "evaluation": {
            "evaluation_mode": "clarification_required",
            "passed": passed,
            "required_clarification_count": len(clarifications),
            "unsupported_defaults_introduced": False,
        },
        "response": response,
    }


def _unsupported_temporal_result(task: DesignTask) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "category": task.category,
        "status": "unsupported",
        "passed": False,
        "execution_mode": "capability_gate",
        "candidate_generated": False,
        "evaluation": {
            "evaluation_mode": task.expected.get("evaluation_mode"),
            "passed": False,
            "required_behaviors": list(task.expected.get("required_behaviors") or []),
        },
        "blocking_reason": {
            "code": "TASK_LEVEL_TEMPORAL_EVALUATOR_UNAVAILABLE",
            "message": (
                "The current harness does not yet validate state retention or "
                "sustained oscillation at the design-task level."
            ),
        },
    }


def _normalized_truth_table(value: Any) -> list[dict[str, int]]:
    if not isinstance(value, list):
        return []
    normalized = []
    for row in value:
        if not isinstance(row, dict):
            continue
        normalized.append(
            {
                str(key): int(str(item).strip().lower() in {"1", "true", "on"})
                for key, item in sorted(row.items())
            }
        )
    return normalized


def _wait_for_research_result(
    services: Any,
    run_id: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    future = getattr(services.research.run_store, "_futures", {}).get(run_id)
    if future is not None:
        future.result(timeout=max(0.1, deadline - time.monotonic()))
        return services.research.result(run_id)
    while time.monotonic() < deadline:
        status = services.research.status(run_id)
        if status.get("status") in {"completed", "failed", "error", "cancelled"}:
            return services.research.result(run_id)
        time.sleep(0.1)
    raise TimeoutError(
        f"Design-task benchmark run did not finish within {timeout_seconds} seconds."
    )


def _batch_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(result.get("status") == "passed" for result in results)
    unsupported = sum(result.get("status") == "unsupported" for result in results)
    provisional = sum(result.get("status") == "provisional" for result in results)
    failed = total - passed - unsupported - provisional
    return {
        "task_count": total,
        "passed_count": passed,
        "failed_count": failed,
        "unsupported_count": unsupported,
        "provisional_count": provisional,
        "pass_rate": round(passed / total, 6) if total else 0.0,
        "all_tasks_executed": len(results) == 5,
        "all_tasks_supported": unsupported == 0,
    }


def _write_batch_artifacts(
    batch: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = output_root / (f"exp003_{timestamp}_{batch['stable_result_hash'][:12]}")
    run_dir.mkdir(parents=True, exist_ok=False)
    json_path = run_dir / "benchmark_packet.json"
    markdown_path = run_dir / "benchmark_summary.md"
    artifact_paths = {
        "packet_json": str(json_path.resolve()),
        "summary_markdown": str(markdown_path.resolve()),
    }
    persisted = deepcopy(batch)
    persisted["artifacts"] = artifact_paths
    json_path.write_text(
        json.dumps(persisted, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(_batch_markdown(batch), encoding="utf-8")
    return artifact_paths


def _batch_markdown(batch: dict[str, Any]) -> str:
    summary = batch["summary"]
    lines = [
        "# EXP-003 Design-Task Benchmark",
        "",
        f"- Task set: `{batch['task_set']['task_set_id']}@{batch['task_set']['version']}`",
        f"- Stable result hash: `{batch['stable_result_hash']}`",
        f"- Passed: `{summary['passed_count']}/{summary['task_count']}`",
        f"- Unsupported: `{summary['unsupported_count']}`",
        f"- Claim boundary: {batch['claim_boundary']}",
        "",
        "| Task | Category | Status | Passed | Execution |",
        "| :--- | :--- | :--- | :---: | :--- |",
    ]
    for result in batch["results"]:
        lines.append(
            "| {task_id} | {category} | {status} | {passed} | {mode} |".format(
                task_id=result["task_id"],
                category=result["category"],
                status=result["status"],
                passed="yes" if result["passed"] else "no",
                mode=result["execution_mode"],
            )
        )
    lines.extend(["", "## Capability Gaps", ""])
    gaps = [result for result in batch["results"] if result["status"] == "unsupported"]
    if gaps:
        for result in gaps:
            lines.append(
                f"- `{result['task_id']}`: "
                f"{result['blocking_reason']['code']} — "
                f"{result['blocking_reason']['message']}"
            )
    else:
        lines.append("- None.")
    lines.append("")
    return "\n".join(lines)
