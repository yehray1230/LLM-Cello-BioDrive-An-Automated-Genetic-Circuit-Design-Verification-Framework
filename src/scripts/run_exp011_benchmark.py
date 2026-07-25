from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import platform
import re
import statistics
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for import_root in (ROOT, SRC):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from schemas.workflow_evidence import (  # noqa: E402
    SimulationEvidenceV1,
    WORKFLOW_EVIDENCE_CONTRACT_VERSION,
    WorkflowEvidenceEnvelopeV1,
)

TASK_SET_ID = "exp003_design_tasks_v1"
CONFIG_NAMES = ("Single-Model", "Routed-Model")
ACCEPTANCE_PROFILE = "acceptance_matrix"
CALIBRATION_PROFILE = "calibration_non_acceptance"
CALIBRATION_TASK_COUNT = 1
EVALUATOR_ADAPTER_CONTRACT = "exp011-live-evaluator-adapter-v3"
EVIDENCE_SERIALIZER_CONTRACT = "exp011-evidence-serializer-v1"
RATE_CARD_SOURCE = "https://ai.google.dev/gemini-api/docs/pricing"
RATE_CARD_ACCESSED = "2026-07-23"

# Standard-tier list prices in USD per one million text/image/video tokens.
# Unknown models are intentionally rejected: silently substituting a price destroys
# the cost comparison.
RATE_CARD_PER_MILLION: dict[str, dict[str, float]] = {
    "gemini/gemini-3.5-flash": {"input": 1.50, "output": 9.00},
    "gemini/gemini-2.5-flash": {"input": 0.30, "output": 2.50},
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _tree_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(item for item in path.rglob("*") if item.is_file())
    if not files:
        return ""
    for path in sorted(set(files), key=lambda item: item.as_posix()):
        try:
            relative = path.relative_to(ROOT).as_posix()
        except ValueError:
            relative = path.as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _git_snapshot() -> dict[str, Any]:
    def run_git(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return completed.stdout.strip()

    head = run_git("rev-parse", "HEAD")
    status = run_git("status", "--short")
    return {
        "head": head,
        "status_available": status is not None,
        "dirty": bool(status) if status is not None else None,
        "status_sha256": _sha256_bytes(status.encode("utf-8")) if status is not None else None,
    }


def get_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    try:
        rate = RATE_CARD_PER_MILLION[model]
    except KeyError as exc:
        raise ValueError(f"No frozen EXP-011 rate card for model: {model}") from exc
    return (
        prompt_tokens * rate["input"] / 1_000_000
        + completion_tokens * rate["output"] / 1_000_000
    )


def _routed_role_models(routed_model: str) -> dict[str, str]:
    """Resolve the frozen Gemini route without importing provider clients.

    The router source hash in the freeze packet detects drift against the runtime
    implementation. Keeping preflight pure prevents an evidence-only command from
    attempting LiteLLM network initialization.
    """
    if not routed_model.startswith("routed:"):
        raise ValueError("routed model must start with 'routed:'")
    base_model = routed_model.removeprefix("routed:")
    if "gemini" not in base_model.lower():
        raise ValueError("EXP-011 v3 rate card and route are frozen to Gemini")
    return {
        "pm": "gemini/gemini-2.5-flash",
        "builder": "gemini/gemini-2.5-flash",
        "translator": "gemini/gemini-2.5-flash",
        "critic": base_model,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the evidence-gated EXP-011 single-versus-routed comparison."
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--single-model", default="gemini/gemini-3.5-flash")
    parser.add_argument("--routed-model", default="routed:gemini/gemini-3.5-flash")
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / ".codex_test_logs" / "exp011_v3"),
    )
    parser.add_argument("--revision", default="exp011_v3")
    parser.add_argument("--compute-budget", type=int, default=5)
    parser.add_argument(
        "--max-total-cost-usd",
        type=float,
        default=None,
        help="Required soft stop for a paid live run; checked between workflow runs.",
    )
    parser.add_argument(
        "--authorize-paid-live-run",
        action="store_true",
        help="Explicitly acknowledge that the provider run may incur charges.",
    )
    parser.add_argument(
        "--calibration-only",
        action="store_true",
        help=(
            "Run the separate non-acceptance calibration profile: the first "
            "canonical task, both configurations, and exactly one repeat."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true")
    mode.add_argument("--preflight-only", action="store_true")
    return parser


def _validate_args(args: argparse.Namespace) -> list[str]:
    errors: list[str] = []
    if args.repeats < 1:
        errors.append("repeats_must_be_positive")
    if args.compute_budget < 1:
        errors.append("compute_budget_must_be_positive")
    if not args.routed_model.startswith("routed:"):
        errors.append("routed_model_must_start_with_routed_prefix")
    if args.calibration_only:
        if args.offline:
            errors.append("calibration_profile_does_not_support_offline_mode")
        if args.repeats != 1:
            errors.append("calibration_profile_requires_exactly_one_repeat")
        if args.revision == "exp011_v3":
            errors.append("calibration_profile_requires_distinct_revision")
        default_output = (ROOT / ".codex_test_logs" / "exp011_v3").resolve()
        calibration_output = Path(args.output_dir).resolve()
        if (
            calibration_output == default_output
            or default_output in calibration_output.parents
        ):
            errors.append("calibration_profile_requires_distinct_output_root")
        if args.max_total_cost_usd is None or args.max_total_cost_usd <= 0:
            errors.append("positive_calibration_cost_soft_stop_required")

    try:
        role_models = _routed_role_models(args.routed_model)
    except Exception as exc:  # preflight must retain the exact import/config failure
        errors.append(f"routing_resolution_failed:{type(exc).__name__}")
        role_models = {}

    priced_models = {args.single_model, *role_models.values()}
    for model in sorted(priced_models):
        if model not in RATE_CARD_PER_MILLION:
            errors.append(f"missing_rate_card:{model}")

    if not args.offline and not args.preflight_only:
        if not args.calibration_only and args.repeats < 3:
            errors.append("live_run_requires_at_least_three_repeats")
        if not args.authorize_paid_live_run:
            errors.append("paid_live_run_not_authorized")
        if args.max_total_cost_usd is None or args.max_total_cost_usd <= 0:
            errors.append("positive_live_cost_soft_stop_required")
    return errors


def _freeze_packet(args: argparse.Namespace, task_set: Any) -> dict[str, Any]:
    task_file = ROOT / "benchmark_suite" / "task_sets" / f"{TASK_SET_ID}.json"
    evaluator_file = ROOT / "application" / "design_task_benchmark.py"
    functional_scorer_file = ROOT / "benchmark_suite" / "functional_scorer.py"
    serializer_file = ROOT / "src" / "mcp_server" / "serializers.py"
    evidence_contract_file = ROOT / "src" / "schemas" / "workflow_evidence.py"
    router_file = ROOT / "src" / "utils" / "llm_utils.py"
    runner_file = Path(__file__).resolve()
    prompt_tree = ROOT / "src" / "agents"
    role_models = _routed_role_models(args.routed_model)
    execution_profile = (
        CALIBRATION_PROFILE if args.calibration_only else ACCEPTANCE_PROFILE
    )
    selected_tasks = (
        list(task_set.tasks[:CALIBRATION_TASK_COUNT])
        if args.calibration_only
        else list(task_set.tasks)
    )
    settings_path = ROOT / "outputs" / "api_data" / "settings.json"
    secret_path = ROOT / "outputs" / "api_data" / "settings.secret"
    public_settings: dict[str, Any] = {}
    if settings_path.is_file():
        try:
            raw_settings = json.loads(settings_path.read_text(encoding="utf-8"))
            public_settings = {
                "provider": raw_settings.get("provider"),
                "model_name": raw_settings.get("model_name"),
                "api_base_configured": bool(raw_settings.get("api_base")),
            }
        except (OSError, json.JSONDecodeError):
            public_settings = {"settings_read_error": True}

    config = {
        "task_set_id": task_set.task_set_id,
        "task_set_content_hash": task_set.content_hash,
        "task_file_sha256": _sha256_file(task_file),
        "evaluator_sha256": _sha256_file(evaluator_file),
        "functional_scorer_sha256": _sha256_file(functional_scorer_file),
        "evaluator_adapter_contract": EVALUATOR_ADAPTER_CONTRACT,
        "evidence_serializer_sha256": _sha256_file(serializer_file),
        "evidence_serializer_contract": EVIDENCE_SERIALIZER_CONTRACT,
        "workflow_evidence_contract": WORKFLOW_EVIDENCE_CONTRACT_VERSION,
        "workflow_evidence_contract_sha256": _sha256_file(evidence_contract_file),
        "router_sha256": _sha256_file(router_file),
        "runner_sha256": _sha256_file(runner_file),
        "prompt_tree_sha256": _tree_hash([prompt_tree]),
        "single_model": args.single_model,
        "routed_model": args.routed_model,
        "routed_role_models": role_models,
        "repeats": args.repeats,
        "execution_profile": execution_profile,
        "selected_task_ids": [task.task_id for task in selected_tasks],
        "expected_workflow_rows": len(selected_tasks) * len(CONFIG_NAMES) * args.repeats,
        "acceptance_contract": not args.calibration_only,
        "calibration_scope": (
            {
                "selection": "canonical_task_order_prefix",
                "task_count": CALIBRATION_TASK_COUNT,
                "purpose": "provider-path and observed-cost calibration only",
                "comparison_eligible": False,
            }
            if args.calibration_only
            else None
        ),
        "compute_budget": args.compute_budget,
        "temperature": "provider_or_workflow_default_frozen_by_source_hash",
        "seed": "not_exposed_by_current_workflow",
        "retry_policy": "workflow_default_frozen_by_source_hash; all provider errors retained",
        "tool_permissions": {
            "rag": True,
            "ode": True,
            "skill_extraction": False,
        },
        "execution_order": "task order; repeat order; config order alternates by pair parity",
        "invalid_pair_policy": [
            "missing_or_duplicate_run",
            "configuration_or_hash_drift",
            "untraceable_exception",
            "provider_or_credential_failure",
        ],
        "real_mapping_gate": "mock source, mock cello_mode, or unmapped mapping_status cannot pass",
        "primary_metric": "strict canonical task pass rate",
        "secondary_metrics": ["estimated_cost_usd", "latency_seconds", "llm_calls"],
        "decision_rule": {
            "quality_margin": 0.05,
            "cost_margin_when_quality_tied": 0.10,
        },
        "rate_card": {
            "currency": "USD",
            "tier": "standard",
            "unit": "per_million_tokens",
            "source": RATE_CARD_SOURCE,
            "accessed": RATE_CARD_ACCESSED,
            "models": RATE_CARD_PER_MILLION,
        },
        "live_cost_soft_stop_usd": args.max_total_cost_usd,
    }
    return {
        "schema_version": "exp011-freeze-v1",
        "revision": args.revision,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config": config,
        "config_sha256": _sha256_bytes(
            json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "git": _git_snapshot(),
            "public_provider_settings": public_settings,
            "secret_file_exists": secret_path.is_file(),
            "credential_value_recorded": False,
        },
    }


def _walk_evidence(value: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from _walk_evidence(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_evidence(item)


def _mapping_gate(value: Any) -> dict[str, Any]:
    sources: set[str] = set()
    cello_modes: set[str] = set()
    mapping_statuses: set[str] = set()
    for key, item in _walk_evidence(value):
        if not isinstance(item, (str, int, float, bool)):
            continue
        rendered = str(item)
        if key == "source" and "cello" in rendered.lower():
            sources.add(rendered)
        elif key == "cello_mode":
            cello_modes.add(rendered)
        elif key == "mapping_status":
            mapping_statuses.add(rendered)
    invalid = (
        any("mock" in item.lower() for item in sources)
        or any(item.lower() == "mock" for item in cello_modes)
        or any(item.lower() == "unmapped" for item in mapping_statuses)
    )
    return {
        "observed": bool(sources or cello_modes or mapping_statuses),
        "eligible_for_real_mapping_claim": not invalid,
        "sources": sorted(sources),
        "cello_modes": sorted(cello_modes),
        "mapping_statuses": sorted(mapping_statuses),
    }


@contextmanager
def _capture_litellm_calls() -> Iterator[list[dict[str, Any]]]:
    import litellm

    calls: list[dict[str, Any]] = []
    original_completion = litellm.completion

    def tracked_completion(*args: Any, **kwargs: Any) -> Any:
        model = str(kwargs.get("model") or "")
        started = time.perf_counter()
        try:
            response = original_completion(*args, **kwargs)
            usage = getattr(response, "usage", None)
            calls.append(
                {
                    "model": model,
                    "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                    "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                    "latency_seconds": round(time.perf_counter() - started, 3),
                    "status": "success",
                }
            )
            return response
        except Exception as exc:
            calls.append(
                {
                    "model": model,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "latency_seconds": round(time.perf_counter() - started, 3),
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error_sha256": _sha256_bytes(str(exc).encode("utf-8")),
                }
            )
            raise

    litellm.completion = tracked_completion
    try:
        yield calls
    finally:
        litellm.completion = original_completion


def _summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for config_name in CONFIG_NAMES:
        selected = [run for run in runs if run.get("config_name") == config_name]
        latencies = [float(run["latency_seconds"]) for run in selected]
        costs = [float(run["estimated_cost_usd"]) for run in selected]
        passed = sum(bool(run.get("passed")) for run in selected)
        summary[config_name] = {
            "total_runs": len(selected),
            "passed": passed,
            "pass_rate": passed / len(selected) if selected else 0.0,
            "total_calls": sum(int(run.get("llm_calls", 0)) for run in selected),
            "total_estimated_cost_usd": round(sum(costs), 6),
            "latency_seconds": {
                "median": round(statistics.median(latencies), 3) if latencies else None,
                "minimum": min(latencies, default=None),
                "maximum": max(latencies, default=None),
            },
            "estimated_cost_usd": {
                "median": round(statistics.median(costs), 6) if costs else None,
                "minimum": min(costs, default=None),
                "maximum": max(costs, default=None),
            },
        }
    return summary


def validate_run_matrix(
    runs: list[dict[str, Any]], task_ids: list[str], repeats: int, *, offline: bool
) -> list[str]:
    reasons: list[str] = []
    if offline:
        reasons.append("offline_synthetic_harness")
    expected = {
        (task_id, config_name, rep)
        for task_id in task_ids
        for config_name in CONFIG_NAMES
        for rep in range(1, repeats + 1)
    }
    observed = [
        (str(run.get("task_id")), str(run.get("config_name")), int(run.get("rep", 0)))
        for run in runs
    ]
    if len(observed) != len(set(observed)):
        reasons.append("duplicate_run_keys")
    if set(observed) != expected:
        reasons.append("run_matrix_mismatch")
    if any(run.get("error") for run in runs):
        reasons.append("run_error_present")
    if any(not run.get("artifact_manifest_sha256") for run in runs):
        reasons.append("missing_artifact_manifest_hash")
    return sorted(set(reasons))


def compute_exp011_decision(
    all_runs: list[dict[str, Any]], *, comparison_eligible: bool = True
) -> str:
    if any(run.get("eval_details", {}).get("offline_mock") for run in all_runs):
        return "offline_synthetic_only"
    if not comparison_eligible:
        return "not_comparable"
    single_runs = [run for run in all_runs if run.get("config_name") == "Single-Model"]
    routed_runs = [run for run in all_runs if run.get("config_name") == "Routed-Model"]
    if not single_runs or not routed_runs:
        return "not_comparable"
    single_rate = sum(bool(run.get("passed")) for run in single_runs) / len(single_runs)
    routed_rate = sum(bool(run.get("passed")) for run in routed_runs) / len(routed_runs)
    if routed_rate - single_rate > 0.05:
        return "routed_favored"
    if single_rate - routed_rate > 0.05:
        return "single_favored"
    routed_cost = statistics.mean(float(run["estimated_cost_usd"]) for run in routed_runs)
    single_cost = statistics.mean(float(run["estimated_cost_usd"]) for run in single_runs)
    if routed_cost < single_cost * 0.9:
        return "routed_favored"
    if single_cost < routed_cost * 0.9:
        return "single_favored"
    return "no_meaningful_difference"


def _write_preflight(output_dir: Path, packet: dict[str, Any], errors: list[str]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    is_calibration = packet["config"]["execution_profile"] == CALIBRATION_PROFILE
    payload = {
        "provenance": {
            "revision": packet["revision"],
            "evidence_class": (
                "calibration_preflight_only" if is_calibration else "preflight_only"
            ),
            "comparison_eligible": False,
            "acceptance_eligible": False,
            "decision": "not_executed",
            "claim_limit": (
                "Non-acceptance calibration freeze only; no provider call and no "
                "comparative, mapping, biological, external-tool, production, or "
                "wet-lab claim."
                if is_calibration
                else "Configuration freeze only; no model quality, latency, cost, or biological claim."
            ),
        },
        "preflight": {
            "status": "ready" if not errors else "blocked",
            "errors": errors,
            "freeze_packet": packet,
        },
    }
    path = output_dir / f"{packet['revision']}_preflight.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _canonical_truth_table(
    rows: list[dict[str, Any]], inputs: list[str], output: str
) -> list[tuple[int, ...]]:
    normalized: list[tuple[int, ...]] = []
    for row in rows:
        if any(key not in row for key in [*inputs, output]):
            return []
        normalized.append(
            tuple(int(bool(row[key])) for key in [*inputs, output])
        )
    return sorted(normalized)


def _extract_verilog_signals(verilog: str) -> tuple[set[str], set[str]]:
    found: dict[str, set[str]] = {"input": set(), "output": set()}
    for keyword in found:
        pattern = rf"\b{keyword}\b\s*(?:\[[^\]]+\]\s*)?([^;);]+)"
        for match in re.finditer(pattern, verilog, re.IGNORECASE):
            segment = re.split(
                r"\b(?:input|output|wire|module|endmodule)\b",
                match.group(1),
                flags=re.IGNORECASE,
            )[0]
            for raw_name in segment.split(","):
                name_match = re.search(r"[A-Za-z_]\w*", raw_name)
                if name_match:
                    found[keyword].add(name_match.group(0))
        direct_pattern = rf"\b{keyword}\b\s*(?:\[[^\]]+\]\s*)?([A-Za-z_]\w*)"
        for match in re.finditer(direct_pattern, verilog, re.IGNORECASE):
            found[keyword].add(match.group(1))
    return found["input"], found["output"]


def _eval_boolean_expression(expr: str, env: dict[str, bool]) -> bool:
    token_pattern = re.compile(
        r"\s*(?:(1'b[01]|[01])|([A-Za-z_]\w*)|(&&|\|\||[!~&|^()]))",
        re.IGNORECASE,
    )
    tokens: list[str] = []
    position = 0
    while position < len(expr):
        match = token_pattern.match(expr, position)
        if not match:
            if expr[position:].strip():
                raise ValueError("unsupported_boolean_expression_token")
            break
        tokens.append(next(group for group in match.groups() if group is not None))
        position = match.end()

    cursor = 0

    def peek() -> str | None:
        return tokens[cursor] if cursor < len(tokens) else None

    def take() -> str:
        nonlocal cursor
        token = peek()
        if token is None:
            raise ValueError("unexpected_end_of_boolean_expression")
        cursor += 1
        return token

    def primary() -> bool:
        token = take()
        if token == "(":
            value = disjunction()
            if take() != ")":
                raise ValueError("unbalanced_boolean_expression")
            return value
        lowered = token.lower()
        if lowered in {"0", "1'b0"}:
            return False
        if lowered in {"1", "1'b1"}:
            return True
        if re.fullmatch(r"[A-Za-z_]\w*", token):
            if token not in env:
                raise KeyError(token)
            return bool(env[token])
        raise ValueError("unsupported_boolean_expression_primary")

    def unary() -> bool:
        if peek() in {"!", "~"}:
            take()
            return not unary()
        return primary()

    def conjunction() -> bool:
        value = unary()
        while peek() in {"&", "&&"}:
            take()
            value = unary() and value
        return value

    def exclusive_or() -> bool:
        value = conjunction()
        while peek() == "^":
            take()
            value = conjunction() != value
        return value

    def disjunction() -> bool:
        value = exclusive_or()
        while peek() in {"|", "||"}:
            take()
            value = exclusive_or() or value
        return value

    result = disjunction()
    if cursor != len(tokens):
        raise ValueError("unsupported_boolean_expression_structure")
    return result


def _simulate_adapter_verilog(
    verilog: str, inputs: dict[str, bool], output_key: str
) -> tuple[bool | None, str | None]:
    from benchmark_suite.functional_scorer import (
        _eval_gate,
        _primitive_calls,
        _strip_comments,
    )

    source = _strip_comments(verilog)
    env = {key: bool(value) for key, value in inputs.items()}
    assignments = re.findall(
        r"\bassign\s+([A-Za-z_]\w*)\s*=\s*(.*?);", source, flags=re.DOTALL
    )
    primitive_calls = _primitive_calls(source)
    if not assignments and not primitive_calls:
        return None, "verilog_contains_no_supported_combinational_statements"

    for _ in range(max(1, len(assignments) + len(primitive_calls) + 1)):
        changed = False
        for gate, args_text in primitive_calls:
            args = [arg.strip() for arg in args_text.split(",") if arg.strip()]
            if len(args) < 2 or any(
                not re.fullmatch(r"[A-Za-z_]\w*", arg) for arg in args
            ):
                return None, "unsupported_verilog_primitive_arguments"
            if any(arg not in env for arg in args[1:]):
                continue
            value = _eval_gate(gate.lower(), [bool(env[arg]) for arg in args[1:]])
            if env.get(args[0]) is not value:
                env[args[0]] = value
                changed = True

        for target, expr in assignments:
            try:
                value = _eval_boolean_expression(expr, env)
            except KeyError:
                continue
            except ValueError as exc:
                return None, str(exc)
            if env.get(target) is not value:
                env[target] = value
                changed = True
        if not changed:
            break

    if output_key not in env:
        return None, "verilog_output_not_resolved"
    return bool(env[output_key]), None


def _adapt_combinational_evidence(
    task: Any, best_topology: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected_inputs = [str(item) for item in task.expected.get("inputs") or []]
    expected_outputs = [str(item) for item in task.expected.get("outputs") or []]
    expected_rows = list(task.expected.get("truth_table") or [])
    verilog = str(best_topology.get("verilog") or "")
    verilog_inputs, verilog_outputs = _extract_verilog_signals(verilog)
    details: dict[str, Any] = {
        "contract_version": EVALUATOR_ADAPTER_CONTRACT,
        "truth_table_source": "deterministic_exp011_verilog_adapter",
        "supported_verilog_subset": (
            "primitive gates plus scalar Boolean assign expressions using "
            "!, ~, &, &&, ^, |, ||, and parentheses"
        ),
        "expected_inputs": expected_inputs,
        "expected_outputs": expected_outputs,
        "verilog_inputs": sorted(verilog_inputs),
        "verilog_outputs": sorted(verilog_outputs),
    }

    candidate = dict(best_topology)
    observed_rows: list[dict[str, Any]] = []
    adapter_errors: list[str] = []
    if not verilog:
        adapter_errors.append("missing_verilog")
    if not expected_inputs or not set(expected_inputs).issubset(verilog_inputs):
        adapter_errors.append("task_inputs_not_found_in_verilog")
    if len(expected_outputs) != 1:
        adapter_errors.append("task_requires_exactly_one_output")
    if len(verilog_outputs) != 1:
        adapter_errors.append("verilog_requires_exactly_one_output")

    if not adapter_errors:
        task_output = expected_outputs[0]
        verilog_output = next(iter(verilog_outputs))
        details["output_mapping"] = {
            "task_output": task_output,
            "verilog_output": verilog_output,
        }
        for values in itertools.product((0, 1), repeat=len(expected_inputs)):
            inputs = dict(zip(expected_inputs, values))
            observed, simulation_error = _simulate_adapter_verilog(
                verilog,
                {key: bool(value) for key, value in inputs.items()},
                verilog_output,
            )
            if observed is None:
                adapter_errors.append(
                    f"verilog_simulation_error:{simulation_error or 'unresolved_output'}"
                )
                observed_rows = []
                break
            observed_rows.append({**inputs, task_output: int(observed)})
        if observed_rows and not adapter_errors:
            candidate["truth_table"] = observed_rows

    task_output = expected_outputs[0] if len(expected_outputs) == 1 else ""
    exact_truth_table_match = bool(observed_rows) and (
        _canonical_truth_table(observed_rows, expected_inputs, task_output)
        == _canonical_truth_table(expected_rows, expected_inputs, task_output)
    )
    details["truth_table_rows"] = len(observed_rows)
    details["truth_table_exact_match"] = exact_truth_table_match
    details["adapter_errors"] = adapter_errors

    simulation_evidence = SimulationEvidenceV1.from_topology(best_topology)
    simulation_evidence_complete = simulation_evidence.combinational_complete
    simulation = simulation_evidence.evaluator_result(
        complete=simulation_evidence_complete,
        incomplete_reason="simulated_status_requires_scenarios_or_ode_trace",
    )
    details["simulation_source"] = (
        "best_topology.simulation_result"
        if simulation_evidence.raw_result
        else "missing_simulation_result"
    )
    details["simulation_evidence_complete"] = simulation_evidence_complete
    details["simulation_scenario_count"] = simulation_evidence.scenario_count
    details["ode_trace_present"] = simulation_evidence.ode_trace.present
    details["ode_trace_valid"] = simulation_evidence.ode_trace.is_valid
    details["ode_trace_errors"] = list(simulation_evidence.ode_trace.errors)

    scoring = dict(best_topology.get("benchmark_report") or {})
    component_scores = dict(scoring.get("component_scores") or {})
    component_scores["functional"] = 1.0 if exact_truth_table_match else 0.0
    scoring["component_scores"] = component_scores
    scoring["functional_score_source"] = (
        "exact_deterministic_verilog_truth_table_match"
    )
    return {
        "candidate": candidate,
        "simulation_result": simulation,
        "evaluation": scoring,
    }, details


def _adapt_temporal_evidence(
    best_topology: dict[str, Any], mode: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate = dict(best_topology)
    simulation_evidence = SimulationEvidenceV1.from_topology(best_topology)
    errors = list(simulation_evidence.ode_trace.errors)
    if simulation_evidence.status != "simulated":
        errors.append("simulation_status_not_simulated")
    evidence_complete = simulation_evidence.temporal_complete
    simulation = simulation_evidence.evaluator_result(
        complete=evidence_complete,
        incomplete_reason="temporal_trace_contract_not_satisfied",
    )

    details = {
        "contract_version": EVALUATOR_ADAPTER_CONTRACT,
        "mode": mode,
        "status": "evidence_ready" if evidence_complete else "incomplete_evidence",
        "source": "best_topology.ode_trace",
        "trace_sample_count": simulation_evidence.ode_trace.sample_count,
        "evidence_complete": evidence_complete,
        "adapter_errors": errors,
        "synthetic_trace_generated": False,
    }
    return {
        "candidate": candidate,
        "simulation_result": simulation,
        "evaluation": dict(
            best_topology.get("benchmark_report") or best_topology
        ),
    }, details


def _evaluate_task(task: Any, result: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    from application.design_task_benchmark import (
        _evaluate_combinational_task,
        _evaluate_oscillatory_temporal_task,
        _evaluate_stateful_temporal_task,
    )
    from schemas import DEFAULT_TEMPORAL_CONFIG

    envelope = WorkflowEvidenceEnvelopeV1.from_service_payload(result)
    data = envelope.data
    best_topology = envelope.best_topology
    if task.expected.get("evaluation_mode") == "clarification_required":
        passed = data.get("status") == "needs_human_input"
        return passed, {"passed": passed, "status": data.get("status")}
    if not isinstance(best_topology, dict):
        return False, {"passed": False, "reason": "missing_best_topology"}
    mode = task.expected.get("evaluation_mode")
    if mode == "combinational_logic":
        research_result, adapter_details = _adapt_combinational_evidence(
            task, best_topology
        )
        details = _evaluate_combinational_task(task, research_result)
    elif mode == "stateful_temporal":
        research_result, adapter_details = _adapt_temporal_evidence(
            best_topology, "stateful_temporal"
        )
        details = _evaluate_stateful_temporal_task(
            task, research_result, config=DEFAULT_TEMPORAL_CONFIG
        )
    elif mode == "oscillatory_temporal":
        research_result, adapter_details = _adapt_temporal_evidence(
            best_topology, "oscillatory_temporal"
        )
        details = _evaluate_oscillatory_temporal_task(
            task, research_result, config=DEFAULT_TEMPORAL_CONFIG
        )
    else:
        details = {"passed": False, "reason": f"unknown_evaluation_mode:{mode}"}
        adapter_details = {
            "contract_version": EVALUATOR_ADAPTER_CONTRACT,
            "status": "unsupported_evaluation_mode",
        }
    return bool(details.get("passed")), {
        **details,
        "evaluator_adapter": adapter_details,
    }


def _normalize_live_workflow_result(result: dict[str, Any]) -> dict[str, Any]:
    """Adapt the current top-level service payload to the benchmark contract."""
    return WorkflowEvidenceEnvelopeV1.from_service_payload(
        result
    ).to_benchmark_payload()


def _run_live_workflow(
    task: Any,
    model_string: str,
    task_output: Path,
    compute_budget: int,
    api_key: str,
    api_base: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], float, str | None]:
    from mcp_server.service import design_circuit_quick

    started = time.perf_counter()
    error: str | None = None
    result: dict[str, Any] = {}
    with _capture_litellm_calls() as calls:
        try:
            raw_result = design_circuit_quick(
                user_intent=task.request,
                model_name=model_string,
                api_key=api_key,
                api_base=api_base,
                output_dir=str(task_output),
                compute_budget=compute_budget,
                enable_rag=True,
                enable_ode=True,
                enable_skill_extraction=False,
            )
            result = _normalize_live_workflow_result(raw_result)
            if result.get("status") != "success":
                observed_status = result.get("service_status") or result.get("status")
                error = str(
                    result.get("error") or f"workflow_status_not_success:{observed_status}"
                )
        except Exception as exc:
            error = f"{type(exc).__name__}:{exc}"
    return result, calls, time.perf_counter() - started, error


def _load_live_settings() -> tuple[str | None, str | None]:
    from application.settings import SettingsService

    settings = SettingsService(ROOT / "outputs" / "api_data" / "settings.json").load_settings()
    return settings.get("api_key"), settings.get("api_base")


def _write_report(
    output_dir: Path,
    packet: dict[str, Any],
    runs: list[dict[str, Any]],
    invalid_reasons: list[str],
) -> tuple[Path, Path]:
    comparison_eligible = not invalid_reasons
    decision = compute_exp011_decision(runs, comparison_eligible=comparison_eligible)
    summary = _summarize_runs(runs)
    is_calibration = packet["config"]["execution_profile"] == CALIBRATION_PROFILE
    provenance = {
        "revision": packet["revision"],
        "date": datetime.now(timezone.utc).isoformat(),
        "evidence_class": (
            "live_calibration_non_acceptance"
            if is_calibration
            else (
                "synthetic_harness"
                if any(run.get("eval_details", {}).get("offline_mock") for run in runs)
                else "live_paired_run"
            )
        ),
        "comparison_eligible": comparison_eligible,
        "acceptance_eligible": comparison_eligible and not is_calibration,
        "invalid_reasons": invalid_reasons,
        "decision": decision,
        "freeze_config_sha256": packet["config_sha256"],
        "claim_limit": (
            "Observed provider-path and cost-calibration evidence only; this "
            "revision cannot accept EXP-011 or support comparative model-quality, "
            "mapping, biological, external-tool, production, or wet-lab claims."
            if is_calibration
            else (
                "Synthetic/report-plumbing evidence only; no comparative or biological claim."
                if decision == "offline_synthetic_only"
                else "Five-task provider-specific research-preview comparison only; no broad or biological superiority claim."
            )
        ),
    }
    payload = {
        "schema_version": "exp011-report-v3",
        "provenance": provenance,
        "freeze_packet": packet,
        "summary": summary,
        "excluded_runs": [],
        "runs": runs,
    }
    json_path = output_dir / f"{packet['revision']}_comparison.json"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        f"# {packet['revision'].upper()} comparison report",
        "",
        f"Decision: **`{decision}`**",
        f"Comparison eligible: **{str(comparison_eligible).lower()}**",
        f"Freeze hash: `{packet['config_sha256']}`",
        "",
    ]
    if invalid_reasons:
        lines.extend(["## Invalid or non-comparative reasons", ""])
        lines.extend(f"- `{reason}`" for reason in invalid_reasons)
        lines.append("")
    lines.extend(
        [
            "## Aggregate summary",
            "",
            "| Configuration | Strict pass | Median latency | Latency range | Median cost | Cost range | Calls |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for name in CONFIG_NAMES:
        item = summary[name]
        latency = item["latency_seconds"]
        cost = item["estimated_cost_usd"]
        lines.append(
            f"| {name} | {item['passed']}/{item['total_runs']} "
            f"({item['pass_rate']:.1%}) | {latency['median']}s | "
            f"{latency['minimum']}–{latency['maximum']}s | ${cost['median']:.6f} | "
            f"${cost['minimum']:.6f}–${cost['maximum']:.6f} | {item['total_calls']} |"
        )
    lines.extend(
        [
            "",
            "> This is a bounded 0.x research-preview result. Mock/unmapped topology output cannot count as a real mapping success.",
            "",
        ]
    )
    md_path = output_dir / f"{packet['revision']}_comparison.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    args = build_parser().parse_args()
    errors = _validate_args(args)

    from benchmark_suite.design_task_dataset import load_design_task_set

    task_set = load_design_task_set(TASK_SET_ID)
    packet = _freeze_packet(args, task_set)
    output_dir = Path(args.output_dir)
    preflight_path = _write_preflight(output_dir, packet, errors)
    if args.preflight_only:
        print(f"EXP-011 preflight written to {preflight_path}")
        return 0 if not errors else 2
    if errors:
        print("ERROR: EXP-011 preflight blocked: " + ", ".join(errors))
        return 2

    api_key: str | None = None
    api_base: str | None = None
    if not args.offline:
        api_key, api_base = _load_live_settings()
        if not api_key:
            print("ERROR: API key is not configured in settings.secret.")
            return 2

    configs = {
        "Single-Model": args.single_model,
        "Routed-Model": args.routed_model,
    }
    selected_tasks = (
        list(task_set.tasks[:CALIBRATION_TASK_COUNT])
        if args.calibration_only
        else list(task_set.tasks)
    )
    runs: list[dict[str, Any]] = []
    cumulative_cost = 0.0
    for task_index, task in enumerate(selected_tasks):
        for rep in range(1, args.repeats + 1):
            ordered_names = list(CONFIG_NAMES)
            if (task_index + rep) % 2:
                ordered_names.reverse()
            pair_id = f"{task.task_id}:rep{rep}"
            for config_name in ordered_names:
                task_output = output_dir / "runs" / task.task_id / config_name / f"rep{rep}"
                task_output.mkdir(parents=True, exist_ok=True)
                if args.offline:
                    latency = 0.0
                    calls = [
                        {
                            "model": args.single_model,
                            "prompt_tokens": 10,
                            "completion_tokens": 5,
                            "latency_seconds": 0.0,
                            "status": "synthetic",
                        }
                    ]
                    result = {"status": "synthetic"}
                    error = None
                    passed = True
                    eval_details = {"passed": True, "offline_mock": True}
                    mapping_gate = {
                        "observed": True,
                        "eligible_for_real_mapping_claim": False,
                        "sources": ["offline_mock"],
                        "cello_modes": [],
                        "mapping_statuses": [],
                    }
                else:
                    result, calls, latency, error = _run_live_workflow(
                        task,
                        configs[config_name],
                        task_output,
                        args.compute_budget,
                        str(api_key),
                        api_base,
                    )
                    passed, eval_details = _evaluate_task(task, result)
                    mapping_gate = _mapping_gate(result)
                    if passed and not mapping_gate["eligible_for_real_mapping_claim"]:
                        passed = False
                        eval_details = {
                            **eval_details,
                            "passed": False,
                            "real_mapping_gate_failed": True,
                        }

                call_costs = [
                    get_cost(
                        str(call.get("model") or ""),
                        int(call.get("prompt_tokens", 0)),
                        int(call.get("completion_tokens", 0)),
                    )
                    for call in calls
                ]
                estimated_cost = sum(call_costs)
                cumulative_cost += estimated_cost
                artifact_hash = _tree_hash([task_output])
                model_counts: dict[str, int] = {}
                for call in calls:
                    model = str(call.get("model") or "")
                    model_counts[model] = model_counts.get(model, 0) + 1
                data = result.get("data") if isinstance(result.get("data"), dict) else {}
                runs.append(
                    {
                        "pair_id": pair_id,
                        "task_id": task.task_id,
                        "category": task.category,
                        "config_name": config_name,
                        "configured_model": configs[config_name],
                        "rep": rep,
                        "execution_position": len(runs) + 1,
                        "passed": passed,
                        "is_completed": bool(data.get("is_completed")),
                        "latency_seconds": round(latency, 3),
                        "used_budget": int(data.get("used_budget") or 0),
                        "iteration_count": int(data.get("iteration_count") or 0),
                        "llm_calls": len(calls),
                        "model_counts": model_counts,
                        "estimated_cost_usd": round(estimated_cost, 6),
                        "cost_method": "recorded_tokens_times_frozen_standard_rate_card",
                        "call_trace": calls,
                        "eval_details": eval_details,
                        "mapping_gate": mapping_gate,
                        "artifact_manifest_sha256": artifact_hash,
                        "error": error,
                    }
                )
                if (
                    not args.offline
                    and args.max_total_cost_usd is not None
                    and cumulative_cost >= args.max_total_cost_usd
                ):
                    invalid = ["live_cost_soft_stop_reached_before_matrix_completion"]
                    if args.calibration_only:
                        invalid.append("calibration_non_acceptance_scope")
                    _write_report(output_dir, packet, runs, invalid)
                    print("EXP-011 stopped at the configured live-cost soft boundary.")
                    return 3

    invalid_reasons = validate_run_matrix(
        runs,
        [task.task_id for task in selected_tasks],
        args.repeats,
        offline=args.offline,
    )
    if args.calibration_only:
        invalid_reasons = sorted(
            {*invalid_reasons, "calibration_non_acceptance_scope"}
        )
    json_path, md_path = _write_report(output_dir, packet, runs, invalid_reasons)
    print(f"EXP-011 report written to {json_path} and {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
