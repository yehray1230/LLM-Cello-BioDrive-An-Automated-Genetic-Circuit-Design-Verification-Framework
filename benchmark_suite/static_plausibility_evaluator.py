from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from benchmark_suite.base_evaluator import EvaluationResult
from benchmark_suite.candidate_values import maybe_float as _maybe_float
from benchmark_suite.score_values import clamp01 as _clamp01

DEFAULT_DEPTH_LIMIT = 4
REPEAT_DECAY = 0.18
DEPTH_DECAY = 0.22


def score_static_plausibility(candidate: dict[str, Any]) -> EvaluationResult:
    explicit = _maybe_float(candidate.get("plausibility_score"))
    has_structural_inputs = any(
        candidate.get(key) is not None
        for key in ("verilog", "verilog_code", "part_ids", "assigned_parts", "components", "logic_depth", "depth", "gate_count")
    )
    if explicit is not None and not has_structural_inputs:
        return EvaluationResult(
            score=_clamp01(explicit),
            details={
                "metric": "static_plausibility",
                "status": "explicit_only",
                "explicit_plausibility_score": explicit,
            },
        )

    repeated_parts = _repeated_part_count(candidate)
    depth = _logic_depth(candidate)
    repeat_penalty = 1.0 - math.exp(-REPEAT_DECAY * repeated_parts)
    depth_excess = max(0, depth - DEFAULT_DEPTH_LIMIT)
    depth_penalty = 1.0 - math.exp(-DEPTH_DECAY * depth_excess)
    structural_score = _clamp01((1.0 - repeat_penalty) * (1.0 - depth_penalty))
    score = structural_score if explicit is None else _clamp01(0.5 * explicit + 0.5 * structural_score)

    return EvaluationResult(
        score=score,
        details={
            "metric": "static_plausibility",
            "status": "ok",
            "repeated_part_count": repeated_parts,
            "logic_depth": depth,
            "repeat_penalty": repeat_penalty,
            "depth_penalty": depth_penalty,
            "explicit_plausibility_score": explicit,
        },
    )


def _repeated_part_count(candidate: dict[str, Any]) -> int:
    part_ids = candidate.get("part_ids") or candidate.get("assigned_parts") or candidate.get("components")
    if isinstance(part_ids, dict):
        values = [str(value) for value in part_ids.values()]
    elif isinstance(part_ids, list | tuple):
        values = [str(value) for value in part_ids]
    else:
        values = _part_tokens_from_verilog(str(candidate.get("verilog") or candidate.get("verilog_code") or ""))
    counts = Counter(value for value in values if value)
    return sum(count - 1 for count in counts.values() if count > 1)


def _part_tokens_from_verilog(verilog: str) -> list[str]:
    tokens = re.findall(r"//\s*(?:part|component|cello_constraint)\s*[:=]\s*([A-Za-z0-9_.-]+)", verilog, re.IGNORECASE)
    tokens.extend(re.findall(r"\b(?:promoter|rbs|terminator|repressor)_([A-Za-z0-9_.-]+)\b", verilog, re.IGNORECASE))
    return tokens


def _logic_depth(candidate: dict[str, Any]) -> int:
    explicit = _maybe_float(candidate.get("logic_depth", candidate.get("depth")))
    if explicit is not None:
        return max(0, int(explicit))
    verilog = str(candidate.get("verilog") or candidate.get("verilog_code") or "")
    if not verilog.strip():
        return max(0, int(_maybe_float(candidate.get("gate_count")) or 0))
    return _depth_from_verilog(verilog)


def _depth_from_verilog(verilog: str) -> int:
    source = _strip_comments(verilog)
    deps: dict[str, list[str]] = {}
    for target, expr in re.findall(r"\bassign\s+([A-Za-z_]\w*)\s*=\s*(.*?);", source, flags=re.DOTALL):
        deps[target] = re.findall(r"\b[A-Za-z_]\w*\b", expr)
    for gate, args_text in _primitive_calls(source):
        args = [arg.strip() for arg in args_text.split(",") if arg.strip()]
        if len(args) >= 2:
            deps[args[0]] = args[1:]
    if not deps:
        return 0

    memo: dict[str, int] = {}

    def depth(signal: str, trail: set[str] | None = None) -> int:
        if signal in memo:
            return memo[signal]
        trail = set() if trail is None else trail
        if signal in trail or signal not in deps:
            return 0
        child_depth = max((depth(dep, trail | {signal}) for dep in deps[signal]), default=0)
        memo[signal] = child_depth + 1
        return memo[signal]

    return max(depth(signal) for signal in deps)


def _primitive_calls(source: str) -> list[tuple[str, str]]:
    gate_names = "and|nand|or|nor|xor|xnor|not|buf"
    calls: list[tuple[str, str]] = []
    patterns = (
        re.compile(rf"\b({gate_names})\s*\(([^;]+?)\)\s*;", re.IGNORECASE),
        re.compile(rf"\b({gate_names})\s+(?:#\s*\([^;]*?\)\s*)?[A-Za-z_]\w*\s*\(([^;]+?)\)\s*;", re.IGNORECASE),
    )
    for pattern in patterns:
        calls.extend((match.group(1), match.group(2)) for match in pattern.finditer(source))
    return calls


def _strip_comments(verilog: str) -> str:
    without_block = re.sub(r"/\*.*?\*/", "", verilog, flags=re.DOTALL)
    return re.sub(r"//.*?$", "", without_block, flags=re.MULTILINE)
