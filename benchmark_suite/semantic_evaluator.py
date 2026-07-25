from __future__ import annotations

import json
from typing import Any

from benchmark_suite.base_evaluator import BaseEvaluator, EvaluationResult
from benchmark_suite.score_values import clamp01 as _clamp01
from utils.llm_utils import call_llm

SEMANTIC_SYSTEM_PROMPT = """You are a strict test engineer for genetic circuit translation.
Your task is to compare the user's original natural-language requirement against the Builder/Translator Verilog.

Checklist:
- Enumerate every explicit condition in the original requirement.
- Infer important edge cases, boundary conditions, polarity assumptions, input combinations, and default behavior.
- Check whether the Verilog logic and comments fully implement each condition.
- Penalize omissions, polarity inversions, missing input cases, ambiguous outputs, or behavior that is only implied by comments but not implemented.

Output ONLY a valid JSON object:
{
  "score": 0.0,
  "missed_conditions": []
}

`score` must be a float from 0.0 to 1.0.
`missed_conditions` must be an array of strings. Use an empty array if nothing is missed.
"""


class SemanticFaithfulnessEvaluator(BaseEvaluator):
    def __init__(
        self,
        api_key: str | None = None,
        model_name: str = "mock",
        api_base: str | None = None,
    ):
        self.api_key = api_key
        self.model_name = model_name
        self.api_base = api_base

    def evaluate(self, candidate: dict[str, Any]) -> EvaluationResult:
        original_prompt = _candidate_text(
            candidate,
            "user_intent",
            "original_prompt",
            "natural_language_prompt",
            "prompt",
        )
        verilog = _candidate_text(candidate, "verilog", "verilog_code", "current_topology")

        if not original_prompt or not verilog:
            missed = ["Semantic evaluation skipped because original prompt or Verilog is missing."]
            return EvaluationResult(
                score=0.0,
                details={
                    "metric": "semantic_faithfulness",
                    "status": "missing_input",
                    "missed_conditions": missed,
                },
                semantic_faithfulness_score=0.0,
                missed_edge_cases=missed,
            )

        response = call_llm(
            api_key=str(candidate.get("api_key", self.api_key) or ""),
            model_name=str(candidate.get("model_name", self.model_name)),
            system_prompt=SEMANTIC_SYSTEM_PROMPT,
            user_content=(
                "Original natural-language prompt:\n"
                f"{original_prompt}\n\n"
                "Builder/Translator Verilog and comments:\n"
                f"{verilog}\n"
            ),
            api_base=candidate.get("api_base", self.api_base),
            temperature=0.0,
        )

        parsed = _parse_llm_json(response)
        if parsed is None:
            missed = [f"Semantic evaluator returned non-JSON output: {response[:300]}"]
            return EvaluationResult(
                score=0.0,
                details={
                    "metric": "semantic_faithfulness",
                    "status": "parse_error",
                    "raw_response": response,
                    "missed_conditions": missed,
                },
                semantic_faithfulness_score=0.0,
                missed_edge_cases=missed,
            )

        score = _clamp01(_coerce_float(parsed.get("score"), 0.0))
        missed_conditions = _coerce_str_list(parsed.get("missed_conditions"))
        return EvaluationResult(
            score=score,
            details={
                "metric": "semantic_faithfulness",
                "status": "ok",
                "missed_conditions": missed_conditions,
            },
            semantic_faithfulness_score=score,
            missed_edge_cases=missed_conditions,
        )


def evaluate_semantic_faithfulness(
    candidate: dict[str, Any],
    api_key: str | None = None,
    model_name: str = "mock",
    api_base: str | None = None,
) -> dict[str, Any]:
    result = SemanticFaithfulnessEvaluator(
        api_key=api_key,
        model_name=model_name,
        api_base=api_base,
    ).evaluate(candidate)
    return {
        "semantic_faithfulness_score": result.semantic_faithfulness_score,
        "missed_edge_cases": result.missed_edge_cases or [],
        "missed_conditions": result.missed_edge_cases or [],
        "details": result.details,
    }


def score_semantic_faithfulness(
    candidate: dict[str, Any],
    api_key: str | None = None,
    model_name: str = "mock",
    api_base: str | None = None,
) -> EvaluationResult:
    return SemanticFaithfulnessEvaluator(
        api_key=api_key,
        model_name=model_name,
        api_base=api_base,
    ).evaluate(candidate)


def _candidate_text(candidate: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = candidate.get(key)
        if value:
            return str(value)
    return ""


def _parse_llm_json(response: str) -> dict[str, Any] | None:
    if not response or response.startswith("ERROR:"):
        return None
    start = response.find("{")
    end = response.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(response[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _coerce_float(value: Any, default: float) -> float:
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    return [str(value)]
