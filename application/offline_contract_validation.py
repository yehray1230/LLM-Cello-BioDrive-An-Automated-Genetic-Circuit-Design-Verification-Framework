"""Run the frozen, offline software-contract validation matrix.

This module deliberately exercises deterministic project-local baselines and
fixture-defined stop decisions. It is not a natural-language-to-agent-to-Cello
end-to-end test and does not invoke Cello, Podman, a provider, or a paid service.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# LiteLLM otherwise attempts to refresh pricing metadata during import. Force
# the bundled map only across these imports, then restore the caller's process
# environment so importing this validation module has no persistent side effect.
_COST_MAP_ENV = "LITELLM_LOCAL_MODEL_COST_MAP"
_prior_cost_map = os.environ.get(_COST_MAP_ENV)
os.environ[_COST_MAP_ENV] = "true"
try:
    from application.demo_baseline import (
        make_reproducible_packet,
        run_canonical_task_baseline,
    )
    from benchmark_suite.design_task_dataset import load_design_task_set
    from schemas.simulation import canonical_payload_hash
finally:
    if _prior_cost_map is None:
        os.environ.pop(_COST_MAP_ENV, None)
    else:
        os.environ[_COST_MAP_ENV] = _prior_cost_map


TASK_SET_ID = "offline_contract_validation_v1"
POSITIVE_TASK_IDS = (
    "fsv_not_a_gfp_v1",
    "fsv_a_and_b_gfp_v1",
    "cello_a_and_not_b_gfp_v1",
)
NEGATIVE_TASK_IDS = (
    "fsv_ambiguous_sensor_v1",
    "fsv_clocked_counter_v1",
)

EXECUTION_BOUNDARY = {
    "mode": "offline_deterministic_fixture",
    "validation_scope": "software_contract_fixture",
    "agent_orchestration_executed": False,
    "external_cello_executed": False,
    "podman_started": False,
    "provider_calls_made": False,
    "paid_calls_made": False,
    "wet_lab_validated": False,
    "cello_mode": "not_run",
    "cello_claim_level": "not_mapped",
    "mapping_status": "not_mapped",
}


def run_offline_contract_validation(
    services: Any,
    *,
    output_dir: str | Path,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """Run all frozen cases and write one aggregate, reproducible packet."""

    task_set = load_design_task_set(TASK_SET_ID)
    output_root = Path(output_dir)
    case_root = output_root / "cases"
    case_root.mkdir(parents=True, exist_ok=True)
    cases: list[dict[str, Any]] = []

    for task in task_set.tasks:
        mode = str(task.expected.get("evaluation_mode") or "")
        if mode == "blocked_before_execution":
            cases.append(
                {
                    "task_id": task.task_id,
                    "category": task.category,
                    "evaluation_mode": mode,
                    "passed": True,
                    "execution_status": "blocked_before_execution",
                    "decision_source": "fixture_defined_stop_contract",
                    "reason_code": task.expected.get("reason_code"),
                    "simulation_status": "not_started",
                    "packet_hash": "",
                    "execution_boundary": dict(EXECUTION_BOUNDARY),
                }
            )
            continue

        packet = run_canonical_task_baseline(
            services,
            task.task_id,
            output_dir=case_root / task.task_id,
            timeout_seconds=timeout_seconds,
            task_set_id=TASK_SET_ID,
        )
        evaluation = dict(packet.get("evaluation") or {})
        research_run = dict(packet.get("research_run") or {})
        cases.append(
            {
                "task_id": task.task_id,
                "category": task.category,
                "evaluation_mode": mode,
                "passed": evaluation.get("passed") is True,
                "execution_status": (
                    "clarification_returned"
                    if mode == "clarification_required"
                    else "offline_simulation_completed"
                ),
                "decision_source": (
                    "fixture_defined_clarification_contract"
                    if mode == "clarification_required"
                    else "deterministic_topology_from_frozen_expected_contract"
                ),
                "reason_code": None,
                "simulation_status": research_run.get("simulation_status"),
                "packet_hash": packet.get("packet_hash"),
                "evaluation": evaluation,
                "artifacts": dict(packet.get("artifacts") or {}),
                "execution_boundary": dict(EXECUTION_BOUNDARY),
            }
        )

    all_passed = all(case["passed"] for case in cases)
    reproducible_result = {
        "task_set_id": task_set.task_set_id,
        "task_set_version": task_set.version,
        "task_set_content_hash": task_set.content_hash,
        "execution_boundary": EXECUTION_BOUNDARY,
        "cases": [
            {
                "task_id": case["task_id"],
                "evaluation_mode": case["evaluation_mode"],
                "passed": case["passed"],
                "execution_status": case["execution_status"],
                "decision_source": case["decision_source"],
                "reason_code": case["reason_code"],
                "simulation_status": case["simulation_status"],
                "packet_hash": case["packet_hash"],
            }
            for case in cases
        ],
        "all_passed": all_passed,
    }
    packet = {
        "packet_type": "offline_contract_validation",
        "packet_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Project-local deterministic baseline and fixture-defined stop evidence "
            "only; no natural-language agent orchestration, external Cello, "
            "biological, wet-lab, or production validation is claimed."
        ),
        **reproducible_result,
        "cases": cases,
        "summary": {
            "result": "PASS" if all_passed else "FAIL",
            "case_count": len(cases),
            "passed_case_count": sum(case["passed"] for case in cases),
            "positive_simulation_count": sum(
                case["task_id"] in POSITIVE_TASK_IDS and case["passed"]
                for case in cases
            ),
            "negative_case_count": sum(
                case["task_id"] in NEGATIVE_TASK_IDS for case in cases
            ),
        },
    }
    packet["stable_result_hash"] = canonical_payload_hash(
        make_reproducible_packet(reproducible_result)
    )

    output_root.mkdir(parents=True, exist_ok=True)
    packet_path = output_root / "offline_contract_validation_packet.json"
    summary_path = output_root / "offline_contract_validation_summary.md"
    packet["artifacts"] = {
        "packet_json": str(packet_path.resolve()),
        "summary_markdown": str(summary_path.resolve()),
    }
    packet_path.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary_path.write_text(_render_summary(packet), encoding="utf-8")
    return packet


def _render_summary(packet: dict[str, Any]) -> str:
    lines = [
        "# Offline contract validation",
        "",
        f"- Result: `{packet['summary']['result']}`",
        f"- Stable result hash: `{packet['stable_result_hash']}`",
        f"- Cases passed: `{packet['summary']['passed_case_count']}/{packet['summary']['case_count']}`",
        "- External Cello executed: `false`",
        "- Evidence level: computational synthetic fixture only",
        "- End-to-end agent/Cello execution: `false`",
        "",
        "## Cases",
        "",
        "| Task | Mode | Outcome | Simulation |",
        "|---|---|---|---|",
    ]
    for case in packet["cases"]:
        outcome = "PASS" if case["passed"] else "FAIL"
        lines.append(
            f"| `{case['task_id']}` | `{case['evaluation_mode']}` | "
            f"`{outcome}` | `{case['simulation_status']}` |"
        )
    lines.extend(
        [
            "",
            "## Claim boundary",
            "",
            packet["claim_boundary"],
            "",
        ]
    )
    return "\n".join(lines)
