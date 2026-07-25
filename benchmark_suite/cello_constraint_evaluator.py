from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from benchmark_suite.base_evaluator import EvaluationResult
from benchmark_suite.score_values import clamp01 as _clamp01

DEFAULT_ORTHOGONALITY_SCORE = 0.25
SEVERE_ORTHOGONALITY_SCORE = 0.05
BUILDABLE_ORTHOGONALITY_SCORE = 1.0

ASSIGNMENT_PATTERNS = (
    re.compile(r"\b(?:gate\s+)?assignment\s+score\b\s*[:=]\s*([-+]?\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"\bgate\s+score\b\s*[:=]\s*([-+]?\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"\bcello\s+score\b\s*[:=]\s*([-+]?\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(
        r"\bsimulatedannealing\b[^\r\n]*?\bscore\b\s*[:=]\s*([-+]?\d+(?:\.\d+)?)",
        re.IGNORECASE,
    ),
)
TOXICITY_PATTERNS = (
    re.compile(r"\btoxicity\b\s*[:=]\s*([-+]?\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"\btoxicity\s+score\b\s*[:=]\s*([-+]?\d+(?:\.\d+)?)", re.IGNORECASE),
)
SEVERE_ORTHOGONALITY_PATTERNS = (
    re.compile(r"\bnot\s+enough\s+gates\b", re.IGNORECASE),
    re.compile(r"\bnot\s+enough\s+(?:orthogonal\s+)?(?:parts|components|repressors)\b", re.IGNORECASE),
    re.compile(r"\bcrosstalk\b", re.IGNORECASE),
    re.compile(r"\bcross\s+talk\b", re.IGNORECASE),
)


def evaluate_cello_constraints(candidate: dict[str, Any]) -> dict[str, Any]:
    """Extract Cello assignment, toxicity, and orthogonality signals from a topology."""
    report_objects, report_text, source_errors = _collect_reports(candidate)
    mapping_status = str(candidate.get("mapping_status", "")).lower()
    cello_buildable = _coerce_bool(candidate.get("cello_buildable"), mapping_status == "mapped")
    has_output = bool(report_objects or report_text.strip())

    assignment_score = _first_number_from_reports(report_objects, ("gate_assignment_score", "assignment_score", "score"))
    if assignment_score is None:
        assignment_score = _last_regex_number(report_text, ASSIGNMENT_PATTERNS)

    toxicity = _first_number_from_reports(report_objects, ("toxicity", "toxicity_score"))
    if toxicity is None:
        toxicity = _first_regex_number(report_text, TOXICITY_PATTERNS)

    severe_constraint_error = _has_severe_orthogonality_error(candidate, report_text)
    if severe_constraint_error:
        orthogonality_score = SEVERE_ORTHOGONALITY_SCORE
        cello_buildable = False
    elif cello_buildable:
        orthogonality_score = _coerce_float(candidate.get("orthogonality_score"), BUILDABLE_ORTHOGONALITY_SCORE)
    elif has_output or _is_cello_failure(candidate):
        orthogonality_score = _coerce_float(candidate.get("orthogonality_score"), DEFAULT_ORTHOGONALITY_SCORE)
    else:
        orthogonality_score = _coerce_float(candidate.get("orthogonality_score"), DEFAULT_ORTHOGONALITY_SCORE)

    normalized_assignment_score = _normalize_score(assignment_score)
    if not cello_buildable and normalized_assignment_score == 0.0:
        normalized_assignment_score = _coerce_float(candidate.get("cello_assignment_score"), 0.0)

    status = "ok" if cello_buildable else "penalized"
    if severe_constraint_error:
        status = "constraint_failed"
    elif not has_output and not cello_buildable:
        status = "missing_output"
    elif source_errors:
        status = "partial"

    return {
        "metric": "cello_constraints",
        "status": status,
        "orthogonality_score": _clamp01(orthogonality_score),
        "cello_assignment_score": _clamp01(normalized_assignment_score),
        "cello_buildable": cello_buildable,
        "toxicity": toxicity,
        "toxicity_score": _toxicity_to_score(toxicity),
        "raw_assignment_score": assignment_score,
        "severe_constraint_error": severe_constraint_error,
        "source_errors": source_errors,
    }


def score_cello_constraints(candidate: dict[str, Any]) -> EvaluationResult:
    metrics = evaluate_cello_constraints(candidate)
    score = 0.5 * metrics["orthogonality_score"] + 0.5 * metrics["cello_assignment_score"]
    if not metrics["cello_buildable"]:
        score *= 0.5
    return EvaluationResult(
        score=_clamp01(score),
        details=metrics,
        orthogonality_score=metrics["orthogonality_score"],
        cello_assignment_score=metrics["cello_assignment_score"],
        cello_buildable=metrics["cello_buildable"],
    )


def _collect_reports(candidate: dict[str, Any]) -> tuple[list[dict[str, Any]], str, list[str]]:
    objects: list[dict[str, Any]] = []
    text_parts: list[str] = []
    errors: list[str] = []

    for key in ("cello_report", "cello_json_report", "assignment_report"):
        value = candidate.get(key)
        if isinstance(value, dict):
            objects.append(value)
            text_parts.append(json.dumps(value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    objects.append(item)
            text_parts.append(json.dumps(value))
        elif value:
            text_parts.append(str(value))

    for key in (
        "cello_stdout",
        "cello_stderr",
        "raw_error_log",
        "mapping_error_summary",
        "cello_log",
        "log",
    ):
        value = candidate.get(key)
        if value:
            text_parts.append(str(value))

    for key in ("cello_report_path", "cello_output_report_path", "report_path"):
        path_value = candidate.get(key)
        if not path_value:
            continue
        path = Path(str(path_value))
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{key}: {exc}")
            continue
        text_parts.append(content)
        if path.suffix.lower() == ".json":
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as exc:
                errors.append(f"{key}: invalid JSON report: {exc}")
                continue
            if isinstance(parsed, dict):
                objects.append(parsed)
            elif isinstance(parsed, list):
                objects.extend(item for item in parsed if isinstance(item, dict))

    return objects, "\n".join(text_parts), errors


def _first_number_from_reports(reports: list[dict[str, Any]], keys: tuple[str, ...]) -> float | None:
    for report in reports:
        value = _find_nested_number(report, keys)
        if value is not None:
            return value
    return None


def _find_nested_number(value: Any, keys: tuple[str, ...]) -> float | None:
    if isinstance(value, dict):
        normalized = {_normalize_key(key): item for key, item in value.items()}
        for key in keys:
            if key in normalized:
                number = _try_float(normalized[key])
                if number is not None:
                    return number
        for item in value.values():
            number = _find_nested_number(item, keys)
            if number is not None:
                return number
    elif isinstance(value, list):
        for item in value:
            number = _find_nested_number(item, keys)
            if number is not None:
                return number
    return None


def _first_regex_number(text: str, patterns: tuple[re.Pattern[str], ...]) -> float | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return _try_float(match.group(1))
    return None


def _last_regex_number(text: str, patterns: tuple[re.Pattern[str], ...]) -> float | None:
    for pattern in patterns:
        matches = list(pattern.finditer(text))
        if matches:
            return _try_float(matches[-1].group(1))
    return None


def _has_severe_orthogonality_error(candidate: dict[str, Any], report_text: str) -> bool:
    text = "\n".join(
        str(value)
        for value in (
            report_text,
            candidate.get("mapping_error_category", ""),
            candidate.get("mapping_error_summary", ""),
            candidate.get("last_error", ""),
        )
        if value
    )
    return any(pattern.search(text) for pattern in SEVERE_ORTHOGONALITY_PATTERNS)


def _is_cello_failure(candidate: dict[str, Any]) -> bool:
    mapping_status = str(candidate.get("mapping_status", "")).lower()
    return (
        mapping_status in {"failed", "mapping_failed", "unmapped"}
        or candidate.get("return_code") not in (None, 0)
        or candidate.get("mapping_error_category") is not None
    )


def _normalize_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")


def _normalize_score(value: float | None) -> float:
    if value is None:
        return 0.0
    if value > 1.0:
        return _clamp01(value / 100.0)
    return _clamp01(value)


def _toxicity_to_score(value: float | None) -> float:
    if value is None:
        return 1.0
    normalized = _normalize_score(value)
    return _clamp01(1.0 - normalized)


def _coerce_float(value: Any, default: float) -> float:
    number = _try_float(value)
    return default if number is None else number


def _try_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "mapped", "success", "successful"}:
            return True
        if lowered in {"0", "false", "no", "n", "failed", "unmapped"}:
            return False
        return default
    return bool(value)
