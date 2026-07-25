from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import statistics
from typing import Any
from uuid import uuid4

from benchmark_suite.benchmark_controller import evaluate_candidate
from benchmark_suite.dataset import BenchmarkDataset
from benchmark_suite.reporting import write_benchmark_report
from tools.tool_adapters import inspect_capabilities
from utils.hashing import stable_json_sha256


def run_benchmark_dataset(
    dataset: BenchmarkDataset,
    *,
    profile_id: str = "research-v1.8",
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    case_results = []
    for case in dataset.cases:
        evaluation = evaluate_candidate(case.candidate, profile_id=profile_id)
        checks = _check_expectations(evaluation, case.expected)
        case_results.append(
            {
                "case_id": case.case_id,
                "name": case.name,
                "tags": list(case.tags),
                "source": dict(case.source),
                "expected": dict(case.expected),
                "evaluation": evaluation,
                "checks": checks,
                "passed": all(item["passed"] for item in checks),
            }
        )

    scores = [
        float(item["evaluation"]["weighted_total_score"])
        for item in case_results
    ]
    dimension_names = sorted(
        {
            key
            for item in case_results
            for key in item["evaluation"].get("dimension_scores", {})
        }
    )
    dimension_summary = {
        key: _summary(
            [
                float(item["evaluation"]["dimension_scores"][key])
                for item in case_results
                if key in item["evaluation"].get("dimension_scores", {})
            ]
        )
        for key in dimension_names
    }
    run_id = f"benchmark_{uuid4().hex[:12]}"
    result = {
        "benchmark_run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": dataset.to_dict(include_cases=False),
        "profile_id": profile_id,
        "scoring_version": (
            case_results[0]["evaluation"]["scoring_version"]
            if case_results
            else None
        ),
        "scoring_configuration_hash": (
            case_results[0]["evaluation"]["scoring_configuration_hash"]
            if case_results
            else None
        ),
        "summary": {
            **_summary(scores),
            "case_count": len(case_results),
            "passed_count": sum(item["passed"] for item in case_results),
            "failed_count": sum(not item["passed"] for item in case_results),
            "pass_rate": (
                sum(item["passed"] for item in case_results) / len(case_results)
                if case_results
                else 0.0
            ),
            "grade_counts": _grade_counts(case_results),
            "calibration": _calibration_metrics(case_results),
            "dimensions": dimension_summary,
        },
        "cases": case_results,
        "tools": [
            tool
            for tool in inspect_capabilities().get("tools", [])
            if tool.get("capability") in {"logic_synthesis", "ode_simulation"}
        ],
    }
    result["result_hash"] = _payload_hash(result)
    if output_dir is not None:
        result["artifacts"] = write_benchmark_report(result, output_dir)
    return result


def compare_benchmark_runs(
    runs: list[dict[str, Any]],
) -> dict[str, Any]:
    if len(runs) < 2:
        raise ValueError("At least two benchmark runs are required.")
    rows = []
    for run in runs:
        summary = run.get("summary", {})
        rows.append(
            {
                "benchmark_run_id": run.get("benchmark_run_id"),
                "profile_id": run.get("profile_id"),
                "scoring_version": run.get("scoring_version"),
                "dataset_id": run.get("dataset", {}).get("dataset_id"),
                "dataset_version": run.get("dataset", {}).get("version"),
                "mean_score": summary.get("mean"),
                "pass_rate": summary.get("pass_rate"),
                "case_count": summary.get("case_count"),
                "dimension_means": {
                    key: value.get("mean")
                    for key, value in summary.get("dimensions", {}).items()
                },
            }
        )
    ranked = sorted(
        rows,
        key=lambda item: (
            float(item.get("pass_rate") or 0.0),
            float(item.get("mean_score") or 0.0),
        ),
        reverse=True,
    )
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank
    baseline = ranked[-1]
    for row in ranked:
        row["mean_score_delta_vs_baseline"] = round(
            float(row.get("mean_score") or 0.0)
            - float(baseline.get("mean_score") or 0.0),
            10,
        )
        row["pass_rate_delta_vs_baseline"] = round(
            float(row.get("pass_rate") or 0.0)
            - float(baseline.get("pass_rate") or 0.0),
            10,
        )
    return {
        "best_run_id": ranked[0]["benchmark_run_id"],
        "ranked_runs": ranked,
        "warning": (
            "Scores from different profile versions are shown side by side "
            "but should not be treated as directly calibrated equivalents."
            if len({row["scoring_version"] for row in ranked}) > 1
            else None
        ),
    }


def _calibration_metrics(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    tp = fp = tn = fn = 0
    for case in case_results:
        expected_score = case.get("expected", {}).get("functional_score", 1.0)
        expected_functional = expected_score >= 0.70
        actual_passed = case.get("passed", False)

        if expected_functional and actual_passed:
            tp += 1
        elif expected_functional and not actual_passed:
            fn += 1
        elif not expected_functional and actual_passed:
            fp += 1
        else:
            tn += 1

    total = tp + fp + tn + fn
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
    fnr = fn / (fn + tp) if (fn + tp) > 0 else 0.0
    accuracy = (tp + tn) / total if total > 0 else 1.0

    return {
        "true_positives": tp,
        "false_positives": fp,
        "true_negatives": tn,
        "false_negatives": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "false_positive_rate": round(fpr, 4),
        "false_negative_rate": round(fnr, 4),
        "accuracy": round(accuracy, 4),
    }


def _check_expectations(
    evaluation: dict[str, Any],
    expected: dict[str, Any],
) -> list[dict[str, Any]]:
    checks = []
    score = float(evaluation["weighted_total_score"])
    if expected.get("grade") is not None:
        actual = evaluation.get("grade")
        target = expected["grade"]
        checks.append(
            {
                "check": "grade",
                "expected": target,
                "actual": actual,
                "passed": actual == target,
            }
        )
    for key, comparator in (("min_score", "minimum"), ("max_score", "maximum")):
        if expected.get(key) is None:
            continue
        target = float(expected[key])
        passed = score >= target if key == "min_score" else score <= target
        checks.append(
            {
                "check": comparator,
                "expected": target,
                "actual": score,
                "passed": passed,
            }
        )
    return checks


def _summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "median": 0.0, "min": 0.0, "max": 0.0}
    return {
        "mean": round(statistics.fmean(values), 10),
        "median": round(statistics.median(values), 10),
        "min": min(values),
        "max": max(values),
    }


def _grade_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in results:
        grade = str(item["evaluation"].get("grade") or "Unknown")
        counts[grade] = counts.get(grade, 0) + 1
    return counts


def _payload_hash(payload: dict[str, Any]) -> str:
    return stable_json_sha256(payload)
