from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from application.case01_evidence import build_case01_evidence_manifest
from application.demo_constants import (
    DEMO_BASELINE_CLAIM,
    DEMO_BASELINE_TASK_ID,
    DEMO_BASELINE_TASK_SET_ID,
    DEMO_BASELINE_TRUTH_TABLE,
    DEMO_BASELINE_VERILOG,
)
from benchmark_suite.design_task_dataset import load_design_task_set
from benchmark_suite.readiness_evaluator import evaluate_readiness
from schemas.assembly_plan import AssemblyFragment, AssemblyJunction, AssemblyPlan
from schemas.design_ir import topology_to_design_ir
from schemas.design_migrations import migrate_design_payload_to_v2
from schemas.simulation import canonical_payload_hash
from tools.sequence_analyzer import analyze_design_sequences
from tools.tool_adapters import inspect_capabilities


_DEMO_TASK_SET = load_design_task_set(DEMO_BASELINE_TASK_SET_ID)
_DEMO_TASK = _DEMO_TASK_SET.task(DEMO_BASELINE_TASK_ID)

DEMO_BASELINE_INTENT = _DEMO_TASK.request


def demo_baseline_topology() -> dict[str, Any]:
    return {
        "topology_id": "demo_a_and_not_b_gfp",
        "user_intent": DEMO_BASELINE_INTENT,
        "verilog": DEMO_BASELINE_VERILOG,
        "truth_table": list(DEMO_BASELINE_TRUTH_TABLE),
        "chassis": "Escherichia coli",
        "copy_number": 3,
        "cello_mode": "not_run",
        "cello_claim_level": "not_mapped",
        "cello_warning": "Fixed baseline uses direct topology simulation; Cello mapping is not claimed.",
        "mapping_status": "not_mapped",
    }


def demo_baseline_request() -> dict[str, Any]:
    return {
        "topology": demo_baseline_topology(),
        "simulation_time": 120.0,
        "sample_count": 24,
        "monte_carlo_samples": 1,
        "noise_fraction": 0.15,
        "random_seed": 20260625,
        "profile_id": "research-v2-preview",
        "user_intent": DEMO_BASELINE_INTENT,
    }


def run_canonical_task_baseline(
    services: Any,
    task_id: str,
    *,
    output_dir: str | Path,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    from application.design_task_benchmark import (
        _deterministic_topology,
        _evaluate_combinational_task,
        _evaluate_stateful_temporal_task,
        _evaluate_oscillatory_temporal_task,
        _wait_for_research_result,
        _clarification_result,
        DEFAULT_RANDOM_SEED,
    )

    task_set = load_design_task_set(DEMO_BASELINE_TASK_SET_ID)
    task = task_set.task(task_id)
    if not task:
        raise ValueError(
            f"Task '{task_id}' not found in task set '{DEMO_BASELINE_TASK_SET_ID}'."
        )

    mode = str(task.expected.get("evaluation_mode") or "")
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    if mode == "clarification_required":
        clarification_res = _clarification_result(task)
        packet = {
            "packet_type": "demo_research_baseline_freeze",
            "packet_version": "1.0",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "intent": task.request,
            "claim_boundary": DEMO_BASELINE_CLAIM,
            "fixed_demo": {
                "task_set_id": task_set.task_set_id,
                "task_set_version": task_set.version,
                "task_set_license": task_set.license,
                "task_set_content_hash": task_set.content_hash,
                "task_id": task.task_id,
                "category": task.category,
                "verilog": "",
                "truth_table": [],
                "chassis": "",
                "copy_number": 0,
            },
            "research_run": {
                "run_id": f"research_skipped_{task.task_id}",
                "status": "completed",
                "simulation_status": "skipped",
                "simulation_model": "none",
                "configuration_hash": "",
                "result_hash": "",
                "scoring_profile": "",
                "scoring_version": "",
                "weighted_total_score": 0.0,
                "grade": "Fail",
            },
            "evaluation": clarification_res["evaluation"],
            "response": clarification_res["response"],
            "readiness": {
                "readiness_status": "conceptual",
                "next_required_stage": "design_conceptualized",
                "domain_scores": {
                    "logic_score": 0.0,
                    "dynamic_score": 0.0,
                    "sequence_quality_score": 0.0,
                    "assembly_plan_score": 0.0,
                    "primer_readiness_score": 0.0,
                    "experimental_readiness_score": None,
                },
            },
            "artifacts": {
                "research": {},
                "benchmark": {},
            },
        }
        packet["packet_hash"] = canonical_payload_hash(make_reproducible_packet(packet))
        packet_dir = output_root / f"demo_baseline_{packet['packet_hash'][:12]}"
        packet_dir.mkdir(parents=True, exist_ok=True)
        packet_json = packet_dir / "demo_baseline_packet.json"
        packet_markdown = packet_dir / "demo_baseline_summary.md"
        packet["artifacts"]["packet_json"] = str(packet_json.resolve())
        packet["artifacts"]["packet_markdown"] = str(packet_markdown.resolve())
        packet_json.write_text(
            json.dumps(packet, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        packet_markdown.write_text(_packet_markdown(packet), encoding="utf-8")
        return packet

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
        "profile_id": "research-v2-preview",
        "user_intent": task.request,
        "temporal_inputs": temporal_inputs,
        "datasets": [
            {
                "task_set_id": task_set.task_set_id,
                "version": task_set.version,
                "content_hash": task_set.content_hash,
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
        evaluation = _evaluate_stateful_temporal_task(task, research_result)
    elif mode == "oscillatory_temporal":
        evaluation = _evaluate_oscillatory_temporal_task(task, research_result)
    else:
        evaluation = {"passed": False}

    design_v1 = topology_to_design_ir(
        topology,
        host_organism=topology.get("chassis", "Escherichia coli"),
        design_id=f"demo_{task.task_id}",
    )
    design_v2 = migrate_design_payload_to_v2(design_v1.to_dict()).design
    _apply_demo_sequences(design_v2)
    sequence_analysis = analyze_design_sequences(design_v2).to_dict()
    sequence_evidence = _sequence_evidence_report(sequence_analysis)
    assembly_plan = _abstract_assembly_plan(design_v2)
    primer_readiness = _primer_readiness_report(assembly_plan)
    readiness = evaluate_readiness(
        design_v2,
        assembly_report=sequence_evidence,
        assembly_plan=assembly_plan,
        primer_result=primer_readiness,
        computational_evaluation=research_result.get("evaluation", {}),
    ).to_dict()

    run_manifest = json.loads(
        Path(str(started["run_manifest_path"])).read_text(encoding="utf-8")
    )
    tool_capabilities = inspect_capabilities()

    packet = {
        "packet_type": "demo_research_baseline_freeze",
        "packet_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "intent": task.request,
        "claim_boundary": DEMO_BASELINE_CLAIM,
        "fixed_demo": {
            "task_set_id": task_set.task_set_id,
            "task_set_version": task_set.version,
            "task_set_license": task_set.license,
            "task_set_content_hash": task_set.content_hash,
            "task_id": task.task_id,
            "category": task.category,
            "verilog": topology["verilog"],
            "truth_table": list(topology.get("truth_table") or []),
            "chassis": topology.get("chassis", "Escherichia coli"),
            "copy_number": topology.get("copy_number", 3),
        },
        "research_run": _research_summary(research_result, started),
        "sequence_analysis": sequence_analysis,
        "sequence_evidence_report": sequence_evidence,
        "assembly_plan": assembly_plan,
        "primer_readiness": primer_readiness,
        "readiness": readiness,
        "tool_capabilities": tool_capabilities,
        "run_manifest": run_manifest,
        "evaluation": evaluation,
    }

    if task_id == "cello_a_and_not_b_gfp_v1":
        benchmark_result = services.evaluations.run_benchmark(
            "research_smoke_v1",
            profile_id="research-v1.8",
        )
        packet["benchmark_run"] = _benchmark_summary(benchmark_result)
        packet["artifacts"] = {
            "research": dict(research_result.get("artifacts") or {}),
            "benchmark": dict(benchmark_result.get("artifacts") or {}),
            "run_manifest_json": started.get("run_manifest_path"),
        }
    else:
        packet["artifacts"] = {
            "research": dict(research_result.get("artifacts") or {}),
            "run_manifest_json": started.get("run_manifest_path"),
        }

    if task_id == DEMO_BASELINE_TASK_ID:
        packet["evidence_manifest"] = build_case01_evidence_manifest(packet)

    packet["packet_hash"] = canonical_payload_hash(make_reproducible_packet(packet))

    packet_dir = output_root / f"demo_baseline_{packet['packet_hash'][:12]}"
    packet_dir.mkdir(parents=True, exist_ok=True)
    packet_json = packet_dir / "demo_baseline_packet.json"
    packet_markdown = packet_dir / "demo_baseline_summary.md"
    sequence_json = packet_dir / "sequence_analysis.json"
    sequence_evidence_json = packet_dir / "sequence_evidence_report.json"
    assembly_plan_json = packet_dir / "assembly_plan.json"
    evidence_manifest_json = packet_dir / "evidence_manifest.json"
    primer_readiness_json = packet_dir / "primer_readiness.json"

    sequence_json.write_text(
        json.dumps(sequence_analysis, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    sequence_evidence_json.write_text(
        json.dumps(sequence_evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    assembly_plan_json.write_text(
        json.dumps(assembly_plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    primer_readiness_json.write_text(
        json.dumps(primer_readiness, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if packet.get("evidence_manifest"):
        evidence_manifest_json.write_text(
            json.dumps(packet["evidence_manifest"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    packet["artifacts"]["packet_json"] = str(packet_json.resolve())
    packet["artifacts"]["packet_markdown"] = str(packet_markdown.resolve())
    packet["artifacts"]["sequence_analysis_json"] = str(sequence_json.resolve())
    packet["artifacts"]["sequence_evidence_json"] = str(
        sequence_evidence_json.resolve()
    )
    packet["artifacts"]["assembly_plan_json"] = str(assembly_plan_json.resolve())
    if packet.get("evidence_manifest"):
        packet["artifacts"]["evidence_manifest_json"] = str(
            evidence_manifest_json.resolve()
        )
    packet["artifacts"]["primer_readiness_json"] = str(primer_readiness_json.resolve())

    packet_json.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    packet_markdown.write_text(_packet_markdown(packet), encoding="utf-8")
    return packet


def run_demo_baseline_freeze(
    services: Any,
    *,
    output_dir: str | Path,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    return run_canonical_task_baseline(
        services,
        task_id=DEMO_BASELINE_TASK_ID,
        output_dir=output_dir,
        timeout_seconds=timeout_seconds,
    )


def _apply_demo_sequences(design: Any) -> None:
    sequence_by_type = {
        "sensor": "ATCGTACGATCGTACGATCGTACG",
        "promoter": "TTGACATGTACTGACTAGCTACGATCGTACGATCGATACGATATAAT",
        "rbs": "AGGAGG",
        "cds": "ATGGCTGAACGTAAACCGTTCGATGGTCTGACCCAGTACGCCATTGACTAA",
        "terminator": "GCATCGTACGATCGATTCGATCGTACGCAT",
        "unknown": "ATCGTACGATCG",
    }
    for part in design.parts:
        part_type = str(part.part_type or "unknown").lower()
        part.sequence = sequence_by_type.get(part_type, sequence_by_type["unknown"])
        part.evidence_level = "illustrative"
        part.host_compatibility = ["Escherichia coli"]
        part.source = "demo_sequence_fixture"
        part.metadata = {
            **dict(part.metadata or {}),
            "sequence_claim_level": "illustrative_sequence_complete_fixture",
        }


def _sequence_evidence_report(sequence_analysis: dict[str, Any]) -> dict[str, Any]:
    issues = [
        {
            "code": str(issue.get("code") or "SEQUENCE_WARNING"),
            "message": str(issue.get("message") or ""),
            "severity": str(issue.get("severity") or "warning"),
            "subject_id": issue.get("subject_id"),
        }
        for result in sequence_analysis.get("results", [])
        if isinstance(result, dict)
        for issue in result.get("issues", [])
        if isinstance(issue, dict)
    ]
    status = str(sequence_analysis.get("status") or "unknown")
    blocked = status == "blocked" or any(
        issue["severity"] == "error" for issue in issues
    )
    return {
        "status": "blocked" if blocked else "sequence_complete",
        "readiness_status": "conceptual" if blocked else "sequence_complete",
        "report_type": "demo_sequence_evidence",
        "schema_version": "1.0",
        "design_id": sequence_analysis.get("design_id"),
        "sequence_analysis_status": status,
        "summary": dict(sequence_analysis.get("summary") or {}),
        "issues": issues,
    }


def _abstract_assembly_plan(design: Any) -> dict[str, Any]:
    ordered_parts = _ordered_construct_parts(design)
    target_sequence = "".join(str(part.sequence or "") for part in ordered_parts)
    fragments = [
        AssemblyFragment(
            fragment_id=f"fragment_{index}_{part.id}",
            name=part.name,
            source_type=part.part_type,
            sequence=str(part.sequence or ""),
            core_sequence=str(part.sequence or ""),
            metadata={
                "part_id": part.id,
                "claim_level": "abstract_sequence_ordering_only",
            },
        )
        for index, part in enumerate(ordered_parts, start=1)
    ]
    junctions = [
        AssemblyJunction(
            junction_id=f"junction_{index}_{left.id}_{right.id}",
            left_fragment_id=f"fragment_{index}_{left.id}",
            right_fragment_id=f"fragment_{index + 1}_{right.id}",
            junction_type="abstract_adjacency",
            sequence=(str(left.sequence or "")[-8:] + str(right.sequence or "")[:8]),
            unique=True,
            direction_valid=True,
            metadata={
                "claim_level": "computational_part_order_check",
                "experimental_method": "not_selected",
            },
        )
        for index, (left, right) in enumerate(
            zip(ordered_parts, ordered_parts[1:]),
            start=1,
        )
    ]
    plan = AssemblyPlan(
        plan_id="demo_abstract_assembly_plan_v1",
        design_id=design.design_id,
        plasmid_id="demo_plasmid_placeholder",
        method="abstract_non_experimental_ordering",
        status="ready" if fragments and junctions else "blocked",
        backbone_id="not_selected",
        backbone_version="not_selected",
        insertion_region_id="not_selected",
        target_length=len(target_sequence),
        target_checksum=canonical_payload_hash(
            {
                "design_id": design.design_id,
                "target_sequence": target_sequence,
                "part_ids": [part.id for part in ordered_parts],
            }
        ),
        fragments=fragments,
        junctions=junctions,
        method_details={
            "claim_boundary": (
                "Abstract assembly planning evidence only; no backbone, "
                "primer, enzyme, or wet-lab protocol has been selected."
            ),
            "part_count": len(ordered_parts),
            "experimental_method_selected": False,
        },
    )
    return plan.to_dict()


def _primer_readiness_report(assembly_plan: dict[str, Any]) -> dict[str, Any]:
    fragments = [
        fragment
        for fragment in assembly_plan.get("fragments", [])
        if isinstance(fragment, dict)
    ]
    junctions = [
        junction
        for junction in assembly_plan.get("junctions", [])
        if isinstance(junction, dict)
    ]
    blocking_reasons: list[dict[str, Any]] = []
    if assembly_plan.get("status") != "ready":
        blocking_reasons.append(
            {
                "code": "ASSEMBLY_PLAN_NOT_READY",
                "message": "Primer readiness requires a ready assembly plan.",
            }
        )
    if not fragments:
        blocking_reasons.append(
            {
                "code": "NO_FRAGMENTS",
                "message": "No assembly fragments are available for primer readiness review.",
            }
        )
    if fragments and not junctions:
        blocking_reasons.append(
            {
                "code": "NO_JUNCTIONS",
                "message": "No assembly junctions are available for primer readiness review.",
            }
        )
    if any(not junction.get("direction_valid", False) for junction in junctions):
        blocking_reasons.append(
            {
                "code": "JUNCTION_DIRECTION_INVALID",
                "message": "One or more assembly junctions failed direction checks.",
            }
        )
    status = "ready" if not blocking_reasons else "blocked"
    return {
        "status": status,
        "report_type": "demo_primer_readiness_gate",
        "schema_version": "1.0",
        "experimental_evidence": False,
        "claim_boundary": (
            "Readiness gate only. This report does not contain primer "
            "sequences, oligo order information, PCR conditions, or a wet-lab protocol."
        ),
        "fragment_count": len(fragments),
        "junction_count": len(junctions),
        "checks": {
            "assembly_plan_ready": assembly_plan.get("status") == "ready",
            "fragments_available": bool(fragments),
            "junctions_available": bool(junctions),
            "junction_directions_valid": all(
                junction.get("direction_valid", False) for junction in junctions
            ),
            "actual_primer_sequences_generated": False,
        },
        "blocking_reasons": blocking_reasons,
        "next_action": (
            "Select a concrete backbone, assembly method, and policy-approved "
            "primer design workflow before generating actionable oligos."
        ),
    }


def _ordered_construct_parts(design: Any) -> list[Any]:
    by_id = {part.id: part for part in design.parts}
    if design.constructs:
        selected = []
        for instance in sorted(
            design.constructs[0].part_instances,
            key=lambda item: item.order,
        ):
            part = by_id.get(instance.part_id)
            if part is not None:
                selected.append(part)
        if selected:
            return selected
    return list(design.parts)


def _wait_for_research_result(
    services: Any,
    run_id: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    future = getattr(services.research.run_store, "_futures", {}).get(run_id)
    if future is not None:
        remaining = max(0.1, deadline - time.monotonic())
        future.result(timeout=remaining)
        return services.research.result(run_id)
    while time.monotonic() < deadline:
        status = services.research.status(run_id)
        if status.get("status") in {"completed", "failed", "error", "cancelled"}:
            return services.research.result(run_id)
        time.sleep(0.1)
    raise TimeoutError(
        f"Research baseline run did not finish within {timeout_seconds} seconds."
    )


def _research_summary(
    result: dict[str, Any], started: dict[str, Any]
) -> dict[str, Any]:
    simulation = result.get("simulation_result", {})
    evaluation = result.get("evaluation", {})
    candidate = result.get("candidate", {})
    return {
        "run_id": result.get("research_run_id") or started.get("run_id"),
        "status": result.get("status"),
        "run_manifest_path": started.get("run_manifest_path"),
        "simulation_status": simulation.get("status"),
        "simulation_model": f"{simulation.get('model_id')}@{simulation.get('model_version')}",
        "configuration_hash": simulation.get("configuration_hash"),
        "result_hash": simulation.get("result_hash"),
        "scoring_profile": evaluation.get("scoring_profile"),
        "scoring_version": evaluation.get("scoring_version"),
        "weighted_total_score": evaluation.get("weighted_total_score"),
        "grade": evaluation.get("grade"),
        "dynamic_margin": candidate.get("dynamic_margin"),
        "signal_to_noise_ratio": candidate.get("signal_to_noise_ratio"),
        "kinetic_score": candidate.get("kinetic_score"),
        "parameter_provenance": candidate.get("parameter_provenance"),
    }


def _benchmark_summary(result: dict[str, Any]) -> dict[str, Any]:
    summary = result.get("summary", {})
    return {
        "benchmark_run_id": result.get("benchmark_run_id"),
        "dataset_id": result.get("dataset", {}).get("dataset_id"),
        "dataset_version": result.get("dataset", {}).get("version"),
        "dataset_license": result.get("dataset", {}).get("license"),
        "profile_id": result.get("profile_id"),
        "scoring_version": result.get("scoring_version"),
        "case_count": summary.get("case_count"),
        "passed_count": summary.get("passed_count"),
        "failed_count": summary.get("failed_count"),
        "pass_rate": summary.get("pass_rate"),
        "mean_score": summary.get("mean"),
        "result_hash": result.get("result_hash"),
    }


def make_reproducible_packet(packet: dict[str, Any]) -> dict[str, Any]:
    transient_keys = {
        "created_at",
        "generated_at",
        "run_id",
        "run_manifest_path",
        "run_manifest_json",
        "benchmark_run_id",
        "packet_hash",
        "timestamp",
        "date",
        "time",
        "research_run_id",
        "run_manifest_path",
        "checked_at",
        "started_at",
        "finished_at",
        "sha256",
        "result_sha256",
    }

    def _normalize(k: str, v: Any) -> Any:
        if k in transient_keys:
            return f"<masked_{k}>"
        if isinstance(v, dict):
            return {
                nk: (
                    "<masked_result_hash>"
                    if k == "benchmark_run" and nk == "result_hash"
                    else _normalize(nk, nv)
                )
                for nk, nv in v.items()
            }
        if isinstance(v, list):
            return [_normalize(k, item) for item in v]
        if isinstance(v, str):
            is_win_path = len(v) > 2 and v[1] == ":" and (v[2] == "\\" or v[2] == "/")
            is_unix_path = v.startswith("/") and not v.startswith("//")
            if is_win_path or is_unix_path:
                return Path(v).name
        return v

    return {key: _normalize(key, val) for key, val in packet.items()}


def _packet_markdown(packet: dict[str, Any]) -> str:
    research = packet["research_run"]
    benchmark = packet.get("benchmark_run")
    sequence = packet.get("sequence_analysis")
    assembly = packet.get("assembly_plan")
    primer = packet.get("primer_readiness")
    readiness = packet["readiness"]
    artifacts = packet["artifacts"]

    lines = [
        "# Demo / Research Baseline Freeze",
        "",
        f"- Intent: {packet['intent']}",
        f"- Packet hash: `{packet['packet_hash']}`",
        f"- Claim boundary: {packet['claim_boundary']}",
        "",
        "## Fixed Demo",
        "",
        "```verilog",
        packet["fixed_demo"]["verilog"],
        "```",
        "",
        "## Research Run",
        "",
        f"- Run: `{research['run_id']}`",
        f"- Simulation: `{research.get('simulation_model', 'none')}`",
        f"- Configuration: `{research.get('configuration_hash', '')}`",
        f"- Result: `{research.get('result_hash', '')}`",
        f"- Scoring: `{research.get('scoring_profile', '')}@{research.get('scoring_version', '')}`",
        f"- Score: `{research.get('weighted_total_score', 0.0)}`",
        f"- Grade: `{research.get('grade', 'Fail')}`",
        "",
    ]

    if benchmark:
        lines.extend(
            [
                "## Benchmark Run",
                "",
                f"- Dataset: `{benchmark['dataset_id']}@{benchmark['dataset_version']}`",
                f"- Cases: `{benchmark['case_count']}`",
                f"- Pass rate: `{benchmark['pass_rate']}`",
                f"- Mean score: `{benchmark['mean_score']}`",
                "",
            ]
        )

    if sequence:
        lines.extend(
            [
                "## Sequence Evidence",
                "",
                f"- Status: `{sequence['status']}`",
                f"- Parts analyzed: `{sequence['summary']['part_count']}`",
                f"- Blocked parts: `{sequence['summary']['blocked_count']}`",
                f"- Warning count: `{sequence['summary']['warning_count']}`",
                "",
            ]
        )

    if assembly:
        lines.extend(
            [
                "## Assembly Plan",
                "",
                f"- Method: `{assembly['method']}`",
                f"- Status: `{assembly['status']}`",
                f"- Fragments: `{len(assembly['fragments'])}`",
                f"- Junctions: `{len(assembly['junctions'])}`",
                f"- Target length: `{assembly['target_length']}`",
                "",
            ]
        )

    if primer:
        lines.extend(
            [
                "## Primer Readiness",
                "",
                f"- Status: `{primer['status']}`",
                f"- Fragments reviewed: `{primer['fragment_count']}`",
                f"- Junctions reviewed: `{primer['junction_count']}`",
                f"- Actual primer sequences generated: `{primer['checks']['actual_primer_sequences_generated']}`",
                "",
            ]
        )

    lines.extend(
        [
            "## Readiness",
            "",
            f"- Status: `{readiness['readiness_status']}`",
            f"- Next required stage: `{readiness['next_required_stage']}`",
            f"- Experimental readiness: `{readiness['domain_scores']['experimental_readiness_score']}`",
            "",
            "## Artifacts",
            "",
            f"- Research summary: `{artifacts.get('research', {}).get('summary_markdown')}`",
        ]
    )

    if benchmark:
        lines.append(
            f"- Benchmark summary: `{artifacts.get('benchmark', {}).get('summary_markdown')}`"
        )

    lines.extend(
        [
            f"- Sequence analysis: `{artifacts.get('sequence_analysis_json')}`",
            f"- Assembly plan: `{artifacts.get('assembly_plan_json')}`",
            f"- Primer readiness: `{artifacts.get('primer_readiness_json')}`",
            f"- Evidence manifest: `{artifacts.get('evidence_manifest_json')}`",
            f"- Run manifest: `{artifacts.get('run_manifest_json')}`",
            "",
        ]
    )

    return "\n".join(lines)
