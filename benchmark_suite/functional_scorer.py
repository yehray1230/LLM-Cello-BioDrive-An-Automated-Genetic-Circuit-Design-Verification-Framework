from __future__ import annotations

import math
import re
from typing import Any

from benchmark_suite.base_evaluator import EvaluationResult
from benchmark_suite.candidate_values import candidate_float as _candidate_float
from benchmark_suite.candidate_values import maybe_float as _maybe_float
from benchmark_suite.score_values import clamp01 as _clamp01

DEFAULT_OUTPUT_KEYS = ("Y", "OUT", "OUTPUT", "Z")
PRIMITIVE_GATES = {"and", "nand", "or", "nor", "xor", "xnor", "not", "buf"}


def score_functional(candidate: dict[str, Any]) -> EvaluationResult:
    truth_table = _truth_table(candidate)
    verilog = _verilog(candidate)
    fallback = _candidate_float(candidate, "functional_score", _candidate_float(candidate, "score", 0.0))

    logic_score: float | None = None
    logic_details: dict[str, Any] = {}
    if truth_table and verilog:
        logic_score, logic_details = _score_truth_table(verilog, truth_table)

    fold_change_score = _fold_change_score(candidate)
    margin_score = _margin_score(candidate)
    components = [
        score
        for score in (logic_score, fold_change_score, margin_score)
        if score is not None
    ]
    if components:
        score = sum(components) / len(components)
        status = "ok" if logic_score is not None else "analog_only"
    else:
        score = fallback
        status = "fallback"

    return EvaluationResult(
        score=_clamp01(score),
        details={
            "metric": "functional",
            "status": status,
            "logic_compliance_score": logic_score,
            "fold_change_score": fold_change_score,
            "margin_score": margin_score,
            **logic_details,
        },
    )


def _truth_table(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    raw = candidate.get("truth_table") or candidate.get("truth_table_or_logic_matrix") or candidate.get("logic_matrix")
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    return []


def _verilog(candidate: dict[str, Any]) -> str:
    return str(candidate.get("verilog") or candidate.get("verilog_code") or candidate.get("verilog_draft") or "")


def _score_truth_table(verilog: str, truth_table: list[dict[str, Any]]) -> tuple[float, dict[str, Any]]:
    correct = 0
    checked = 0
    failures: list[dict[str, Any]] = []
    for row in truth_table:
        output_key = _output_key(row)
        if output_key is None:
            continue
        expected = _as_bool(row[output_key])
        inputs = {key: _as_bool(value) for key, value in row.items() if key != output_key}
        actual = _simulate_verilog(verilog, inputs, output_key)
        checked += 1
        if actual is expected:
            correct += 1
        else:
            failures.append(
                {
                    "inputs": inputs,
                    "output": output_key,
                    "expected": expected,
                    "actual": actual,
                }
            )
    if checked == 0:
        return 0.0, {"truth_table_rows_checked": 0, "logic_failures": ["No checkable truth table rows."]}
    return correct / checked, {"truth_table_rows_checked": checked, "logic_failures": failures}


def _simulate_verilog(verilog: str, inputs: dict[str, bool], output_key: str) -> bool | None:
    source = _strip_comments(verilog)
    env = {key: bool(value) for key, value in inputs.items()}

    assignments = re.findall(r"\bassign\s+([A-Za-z_]\w*)\s*=\s*(.*?);", source, flags=re.DOTALL)
    primitive_calls = _primitive_calls(source)

    for _ in range(max(1, len(assignments) + len(primitive_calls) + 1)):
        changed = False
        for gate, args_text in primitive_calls:
            args = [arg.strip() for arg in args_text.split(",") if arg.strip()]
            if len(args) < 2:
                continue
            out = args[0]
            values = [bool(env.get(arg, False)) for arg in args[1:]]
            value = _eval_gate(gate.lower(), values)
            if env.get(out) is not value:
                env[out] = value
                changed = True

        for target, expr in assignments:
            value = _eval_expr(expr, env)
            if env.get(target) is not value:
                env[target] = value
                changed = True

        if not changed:
            break

    return env.get(output_key)


def _primitive_calls(source: str) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    gate_names = "|".join(PRIMITIVE_GATES)
    patterns = (
        re.compile(rf"\b({gate_names})\s*\(([^;]+?)\)\s*;", re.IGNORECASE),
        re.compile(rf"\b({gate_names})\s+(?:#\s*\([^;]*?\)\s*)?[A-Za-z_]\w*\s*\(([^;]+?)\)\s*;", re.IGNORECASE),
    )
    for pattern in patterns:
        calls.extend((match.group(1), match.group(2)) for match in pattern.finditer(source))
    return calls


def _eval_expr(expr: str, env: dict[str, bool]) -> bool:
    text = expr.strip()
    text = re.sub(r"\b1'b1\b|\b1\b", " True ", text)
    text = re.sub(r"\b1'b0\b|\b0\b", " False ", text)
    text = text.replace("&&", " and ").replace("||", " or ")
    text = text.replace("&", " and ").replace("|", " or ").replace("^", " != ")
    text = re.sub(r"~\s*([A-Za-z_]\w*)", r" not \1", text)
    text = re.sub(r"!\s*([A-Za-z_]\w*)", r" not \1", text)
    names = sorted(set(re.findall(r"\b[A-Za-z_]\w*\b", text)) - {"and", "or", "not", "True", "False"})
    safe_env = {name: bool(env.get(name, False)) for name in names}
    try:
        return bool(eval(text, {"__builtins__": {}}, safe_env))
    except Exception:
        return False


def _eval_gate(gate: str, values: list[bool]) -> bool:
    if gate == "not":
        return not values[0]
    if gate == "buf":
        return values[0]
    if gate == "and":
        return all(values)
    if gate == "nand":
        return not all(values)
    if gate == "or":
        return any(values)
    if gate == "nor":
        return not any(values)
    if gate == "xor":
        return sum(1 for value in values if value) % 2 == 1
    if gate == "xnor":
        return sum(1 for value in values if value) % 2 == 0
    return False


def _output_key(row: dict[str, Any]) -> str | None:
    for key in DEFAULT_OUTPUT_KEYS:
        if key in row:
            return key
    for key in row:
        if str(key).lower() in {"output", "out", "result"}:
            return key
    return next(reversed(row), None) if row else None


def _fold_change_score(candidate: dict[str, Any]) -> float | None:
    min_on = _maybe_float(candidate.get("min_on", candidate.get("on_min")))
    max_off = _maybe_float(candidate.get("max_off", candidate.get("off_max")))
    fold_change = _maybe_float(candidate.get("fold_change"))
    if fold_change is None and min_on is not None and max_off is not None:
        fold_change = min_on / max(max_off, 1e-9)
    if fold_change is None:
        return None
    return _clamp01(math.log1p(max(0.0, fold_change)) / math.log1p(100.0))


def _margin_score(candidate: dict[str, Any]) -> float | None:
    min_on = _maybe_float(candidate.get("min_on", candidate.get("on_min")))
    max_off = _maybe_float(candidate.get("max_off", candidate.get("off_max")))
    if min_on is None or max_off is None:
        return None
    margin = min_on - max_off
    scale = max(abs(min_on), abs(max_off), 1.0)
    return _clamp01(0.5 + 0.5 * margin / scale)


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "high", "on"}
    return bool(value)


def _strip_comments(verilog: str) -> str:
    without_block = re.sub(r"/\*.*?\*/", "", verilog, flags=re.DOTALL)
    return re.sub(r"//.*?$", "", without_block, flags=re.MULTILINE)
