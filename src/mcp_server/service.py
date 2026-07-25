from __future__ import annotations

import os
import json
import uuid
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.consolidator_agent import ConsolidatorAgent
from agents.data_miner_agent import DataMinerAgent
from benchmark_suite.benchmark_controller import evaluate_candidate
from mcp_server.artifact_writer import create_run_dir, write_json, write_state_artifacts, write_text
from mcp_server.chart_renderer import render_charts
from mcp_server.explainer import build_design_explanation, validate_explanation_options
from mcp_server.result_access import (
    best_topology_from_result as _best_topology_from_result,
)
from mcp_server.run_store import RunStore
from mcp_server.serializers import design_state_from_dict, summarize_state, summarize_topology
from schemas.design_diff import compare_designs
from schemas.design_ir import DesignIR, design_ir_from_dict, topology_to_design_ir
from schemas.design_operations import replace_part_immutable, validate_replacement
from schemas.state import DesignState, SearchNode
from tools.cello_wrapper import CelloWrapper
from tools.ode_simulator import BatchODESimulator
from tools.part_library import PartLibrary
from tools.tool_adapters import inspect_capabilities
from tools.topology_selection import select_best_topology
from exporters.bom_exporter import export_bom_csv
from exporters.genbank_exporter import export_genbank
from exporters.sbol3_exporter import export_sbol3_turtle
from vector_db import InMemoryVectorDB
from workflows.reflexion_controller import run_reflexion_workflow
from utils.llm_utils import resolve_agent_model


DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_RUN_STORE = RunStore()
ERROR_VALIDATION = "validation_error"
ERROR_DEPENDENCY = "dependency_error"
ERROR_WORKFLOW = "workflow_error"
ERROR_EXTERNAL_TOOL = "external_tool_error"
ERROR_NOT_FOUND = "not_found"


@dataclass
class WorkflowOptions:
    enable_rag: bool = True
    enable_ode: bool = True
    enable_skill_extraction: bool = True
    compute_budget: int = 2
    monte_carlo_samples: int = 1
    output_dir: str | None = None
    cello_command: str | None = None
    ucf_path: str | None = None
    sensor_path: str | None = None
    device_path: str | None = None
    model_name: str | None = None
    api_base: str | None = None
    api_key: str | None = None


class TranslatorRunner:
    def __init__(self, api_key: str | None, model_name: str, api_base: str | None):
        self.api_key = api_key
        self.model_name = model_name
        self.api_base = api_base
        self.kwargs: dict[str, Any] = {}

    def run(self, state: DesignState) -> DesignState:
        from agents.translator_agent import call_translator

        return call_translator(
            state,
            api_key=self.api_key,
            model_name=self.model_name,
            api_base=self.api_base,
            **self.kwargs,
        )


class NoOpODESimulator:
    def run(self, state: DesignState) -> DesignState:
        node = state.tree_nodes.get(state.current_node_id) if state.current_node_id else None
        topologies = node.candidate_topologies if node else state.candidate_topologies
        for topology in topologies:
            topology["ode_status"] = "disabled"
        state.candidate_topologies = topologies
        return state


def design_circuit_quick(
    user_intent: str,
    host_organism: str = "Escherichia coli",
    compute_budget: int = 2,
    enable_rag: bool = True,
    enable_ode: bool = True,
    enable_skill_extraction: bool = True,
    monte_carlo_samples: int = 1,
    model_name: str | None = None,
    api_base: str | None = None,
    api_key: str | None = None,
    output_dir: str | None = None,
    cello_command: str | None = None,
    ucf_path: str | None = None,
    sensor_path: str | None = None,
    device_path: str | None = None,
    progress_callback=None,
    initial_state: DesignState | None = None,
) -> dict[str, Any]:
    user_intent = str(user_intent or "").strip()
    if not user_intent:
        return _error_response("user_intent is required.", ERROR_VALIDATION)
    from utils.safety_checker import check_safety, log_safety_event
    safety_result = check_safety(user_intent, host_organism)
    if safety_result.status != "safe":
        log_safety_event(
            None,
            user_intent,
            safety_result.status,
            safety_result.warnings,
            action="design_circuit_quick",
            categories=safety_result.categories,
        )
    if not safety_result.is_safe:
        return _error_response(safety_result.redirection_message, "SAFETY_VIOLATION")
    if not safety_result.automation_allowed:
        return _error_response(
            safety_result.redirection_message,
            "SAFETY_REVIEW_REQUIRED",
        )
    compute_budget_result = _coerce_min_int(compute_budget, "compute_budget")
    if compute_budget_result["status"] == "error":
        return compute_budget_result
    monte_carlo_result = _coerce_min_int(monte_carlo_samples, "monte_carlo_samples")
    if monte_carlo_result["status"] == "error":
        return monte_carlo_result

    options = WorkflowOptions(
        enable_rag=enable_rag,
        enable_ode=enable_ode,
        enable_skill_extraction=enable_skill_extraction,
        compute_budget=compute_budget_result["value"],
        monte_carlo_samples=monte_carlo_result["value"],
        output_dir=output_dir,
        cello_command=cello_command,
        ucf_path=ucf_path,
        sensor_path=sensor_path,
        device_path=device_path,
        model_name=model_name,
        api_base=api_base,
        api_key=api_key,
    )
    resolved_model = _resolve_model(options.model_name)
    resolved_api_key = _resolve_api_key(options.api_key)
    resolved_api_base = options.api_base or os.getenv("LITELLM_API_BASE") or None

    if not resolved_model:
        return _error_response("model_name is required via argument or LITELLM_MODEL.", ERROR_VALIDATION)

    try:
        from agents.builder_agent import BuilderAgent
        from agents.critic_agent import CriticAgent
        from agents.skill_extractor_agent import SkillExtractorAgent
        from tools.skill_retriever import SkillRetriever
    except ModuleNotFoundError as exc:
        return _error_response(
            (
                "design_circuit_quick requires the LLM workflow dependencies. "
                f"Missing module: {exc.name}"
            ),
            ERROR_DEPENDENCY,
        )

    state = initial_state or DesignState(
        user_intent=user_intent,
        host_organism=host_organism.strip() or "Escherichia coli",
        compute_budget=options.compute_budget,
    )

    batch_ode_simulator = (
        BatchODESimulator(monte_carlo_samples=options.monte_carlo_samples)
        if options.enable_ode
        else NoOpODESimulator()
    )
    skill_retriever = SkillRetriever.from_json_file() if options.enable_rag else None
    skill_extractor = (
        SkillExtractorAgent(vault_dir="outputs/obsidian_skills", vector_db=InMemoryVectorDB())
        if options.enable_skill_extraction
        else None
    )

    try:
        result_state = run_reflexion_workflow(
            state=state,
            builder=BuilderAgent(api_key=resolved_api_key, model_name=resolve_agent_model("builder", resolved_model), api_base=resolved_api_base),
            translator=TranslatorRunner(api_key=resolved_api_key, model_name=resolve_agent_model("translator", resolved_model), api_base=resolved_api_base),
            cello_wrapper=CelloWrapper(
                cello_command=options.cello_command,
                ucf_path=options.ucf_path,
                sensor_path=options.sensor_path,
                device_path=options.device_path,
                artifact_dir=Path(options.output_dir) / "cello_artifacts" if options.output_dir else None,
            ),
            batch_ode_simulator=batch_ode_simulator,
            critic=CriticAgent(api_key=resolved_api_key, model_name=resolve_agent_model("critic", resolved_model), api_base=resolved_api_base),
            consolidator=ConsolidatorAgent(),
            skill_retriever=skill_retriever,
            data_miner=DataMinerAgent() if options.enable_ode else None,
            skill_extractor=skill_extractor,
            progress_callback=progress_callback,
        )
    except Exception as exc:
        return _error_response(f"workflow failed: {exc}", ERROR_WORKFLOW)

    run_dir = create_run_dir(options.output_dir)
    charts = render_charts(result_state.best_topology, run_dir)
    artifacts = write_state_artifacts(result_state, run_dir, charts)
    summary = summarize_state(result_state)
    return _success_response({
        "status": _status_from_state(result_state),
        "run_dir": str(run_dir.resolve()),
        "summary": summary,
        "artifacts": artifacts,
        "warnings": safety_result.warnings,
        "safety": safety_result.to_dict(),
    })


def start_design_run(
    user_intent: str,
    host_organism: str = "Escherichia coli",
    compute_budget: int = 6,
    enable_rag: bool = True,
    enable_ode: bool = True,
    enable_skill_extraction: bool = True,
    monte_carlo_samples: int = 1,
    model_name: str | None = None,
    api_base: str | None = None,
    api_key: str | None = None,
    output_dir: str | None = None,
    cello_command: str | None = None,
    ucf_path: str | None = None,
    sensor_path: str | None = None,
    device_path: str | None = None,
    run_store: RunStore | None = None,
) -> dict[str, Any]:
    user_intent = str(user_intent or "").strip()
    if not user_intent:
        return _error_response("user_intent is required.", ERROR_VALIDATION)
    from utils.safety_checker import check_safety, log_safety_event
    safety_result = check_safety(user_intent, host_organism)
    if not safety_result.is_safe:
        log_safety_event(
            None,
            user_intent,
            safety_result.status,
            safety_result.warnings,
            action="start_design_run",
            categories=safety_result.categories,
        )
        return _error_response(safety_result.redirection_message, "SAFETY_VIOLATION")
    if not safety_result.automation_allowed:
        log_safety_event(
            None,
            user_intent,
            safety_result.status,
            safety_result.warnings,
            action="start_design_run",
            categories=safety_result.categories,
        )
        return _error_response(
            safety_result.redirection_message,
            "SAFETY_REVIEW_REQUIRED",
        )
    compute_budget_result = _coerce_min_int(compute_budget, "compute_budget")
    if compute_budget_result["status"] == "error":
        return compute_budget_result
    monte_carlo_result = _coerce_min_int(monte_carlo_samples, "monte_carlo_samples")
    if monte_carlo_result["status"] == "error":
        return monte_carlo_result

    request = {
        "user_intent": user_intent,
        "host_organism": host_organism,
        "compute_budget": compute_budget_result["value"],
        "enable_rag": enable_rag,
        "enable_ode": enable_ode,
        "enable_skill_extraction": enable_skill_extraction,
        "monte_carlo_samples": monte_carlo_result["value"],
        "model_name": model_name,
        "api_base": api_base,
        "api_key": api_key,
        "output_dir": output_dir,
        "cello_command": cello_command,
        "ucf_path": ucf_path,
        "sensor_path": sensor_path,
        "device_path": device_path,
        "safety": safety_result.to_dict(),
    }
    selected_store = run_store or DEFAULT_RUN_STORE
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    response = selected_store.start(
        task=lambda: design_circuit_quick(
            user_intent=user_intent,
            host_organism=host_organism,
            compute_budget=compute_budget_result["value"],
            enable_rag=enable_rag,
            enable_ode=enable_ode,
            enable_skill_extraction=enable_skill_extraction,
            monte_carlo_samples=monte_carlo_result["value"],
            model_name=model_name,
            api_base=api_base,
            api_key=api_key,
            output_dir=output_dir,
            cello_command=cello_command,
            ucf_path=ucf_path,
            sensor_path=sensor_path,
            device_path=device_path,
            progress_callback=lambda stage, status, progress, message, details=None: selected_store.append_event(
                run_id, stage, status, progress, message, details
            ),
        ),
        request=request,
        run_id=run_id,
    )
    response["warnings"] = safety_result.warnings
    response["safety"] = safety_result.to_dict()
    return response


def get_design_run_status(run_id: str, run_store: RunStore | None = None) -> dict[str, Any]:
    selected_store = run_store or DEFAULT_RUN_STORE
    return selected_store.status(run_id)


def get_design_run_events(
    run_id: str,
    after_event_id: int = 0,
    limit: int = 100,
    run_store: RunStore | None = None,
) -> dict[str, Any]:
    if not str(run_id or "").strip():
        return _error_response("run_id is required.", ERROR_VALIDATION)
    try:
        selected_after = max(0, int(after_event_id))
        selected_limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        return _error_response("after_event_id and limit must be integers.", ERROR_VALIDATION)
    return (run_store or DEFAULT_RUN_STORE).events(
        str(run_id).strip(),
        after_event_id=selected_after,
        limit=selected_limit,
    )


def get_design_run_progress(run_id: str, run_store: RunStore | None = None) -> dict[str, Any]:
    status = get_design_run_status(run_id, run_store=run_store)
    if status.get("status") == "not_found":
        return status
    return _success_response(
        {
            "status": status.get("status"),
            "run_id": status.get("run_id"),
            "stage": status.get("stage"),
            "progress": status.get("progress", 0.0),
            "event_count": status.get("event_count", 0),
            "message": status.get("message"),
            "summary": status.get("summary", {}),
            "artifacts": status.get("artifacts", {}),
        }
    )


def get_design_run_result(run_id: str, run_store: RunStore | None = None) -> dict[str, Any]:
    selected_store = run_store or DEFAULT_RUN_STORE
    return _ensure_standard_response(selected_store.result(run_id))


def list_design_runs(limit: int = 20, run_store: RunStore | None = None) -> dict[str, Any]:
    selected_store = run_store or DEFAULT_RUN_STORE
    try:
        selected_limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        return _error_response("limit must be an integer.", ERROR_VALIDATION)
    return selected_store.list_runs(selected_limit)


def list_tool_capabilities() -> dict[str, Any]:
    capabilities = inspect_capabilities()
    unavailable_count = sum(
        1
        for tool in capabilities.get("tools", [])
        if tool.get("status") in {"unavailable", "fallback", "failed"}
    )
    return _success_response(
        {
            "status": "completed",
            "summary": {
                "capability_count": len(capabilities.get("capabilities", [])),
                "tool_count": len(capabilities.get("tools", [])),
                "unavailable_or_fallback_count": unavailable_count,
            },
            "artifacts": {},
            **capabilities,
        }
    )


def cancel_design_run(run_id: str, run_store: RunStore | None = None) -> dict[str, Any]:
    if not str(run_id or "").strip():
        return _error_response("run_id is required.", ERROR_VALIDATION)
    selected_store = run_store or DEFAULT_RUN_STORE
    return _ensure_standard_response(selected_store.cancel(str(run_id).strip()))


def get_design_run_artifacts(run_id: str, run_store: RunStore | None = None) -> dict[str, Any]:
    if not str(run_id or "").strip():
        return _error_response("run_id is required.", ERROR_VALIDATION)
    selected_store = run_store or DEFAULT_RUN_STORE
    return _ensure_standard_response(selected_store.artifacts(str(run_id).strip()))


def compare_design_runs(run_ids: list[str], run_store: RunStore | None = None) -> dict[str, Any]:
    normalized_run_ids = _normalize_run_ids(run_ids)
    if normalized_run_ids["status"] == "error":
        return normalized_run_ids

    selected_store = run_store or DEFAULT_RUN_STORE
    comparisons = []
    unavailable_runs = []
    for run_id in normalized_run_ids["run_ids"]:
        result = _ensure_standard_response(selected_store.result(run_id))
        if result.get("status") != "completed":
            unavailable_runs.append(
                {
                    "run_id": run_id,
                    "status": result.get("status"),
                    "error": result.get("error"),
                    "error_type": result.get("error_type"),
                }
            )
            continue
        comparisons.append(_comparison_entry(run_id, result))

    comparisons.sort(key=lambda item: (item["score"] is not None, item["score"] or -float("inf")), reverse=True)
    for index, item in enumerate(comparisons, start=1):
        item["rank"] = index

    return _success_response(
        {
            "status": "completed" if comparisons else "error",
            "summary": {
                "requested_run_count": len(normalized_run_ids["run_ids"]),
                "available_run_count": len(comparisons),
                "unavailable_run_count": len(unavailable_runs),
                "best_run_id": comparisons[0]["run_id"] if comparisons else None,
            },
            "artifacts": {},
            "best_run": comparisons[0] if comparisons else None,
            "ranked_runs": comparisons,
            "unavailable_runs": unavailable_runs,
            "metrics": [
                "score",
                "mapping_status",
                "cello_mode",
                "cello_claim_level",
                "ode_status",
                "cello_buildable",
                "robustness_score",
                "toxicity_score",
                "semantic_faithfulness_score",
                "artifact_count",
            ],
            "error": None if comparisons else "No completed runs were available for comparison.",
            "error_type": None if comparisons else ERROR_NOT_FOUND,
        }
    )


def diagnose_design_run(run_id: str, run_store: RunStore | None = None) -> dict[str, Any]:
    if not str(run_id or "").strip():
        return _error_response("run_id is required.", ERROR_VALIDATION)
    selected_store = run_store or DEFAULT_RUN_STORE
    result = _ensure_standard_response(selected_store.result(str(run_id).strip()))
    if result.get("status") == "not_found":
        return result

    summary = result.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}
    artifacts = result.get("artifacts", {})
    if not isinstance(artifacts, dict):
        artifacts = {}
    best_topology = _best_topology_from_result(result)
    failed_attempts = summary.get("failed_attempts") if isinstance(summary.get("failed_attempts"), list) else []

    findings = _diagnose_findings(result, summary, best_topology, failed_attempts)
    actions = _recommended_actions(findings)
    diagnosis_status = _diagnosis_status(findings)
    return _success_response(
        {
            "status": "completed",
            "summary": {
                "run_id": run_id,
                "run_status": result.get("status"),
                "diagnosis_status": diagnosis_status,
                "finding_count": len(findings),
                "high_severity_count": sum(1 for finding in findings if finding["severity"] == "high"),
            },
            "artifacts": artifacts,
            "diagnosis_status": diagnosis_status,
            "findings": findings,
            "likely_causes": _likely_causes(findings),
            "recommended_next_actions": actions,
        }
    )


def explain_design_run(
    run_id: str,
    profile: str = "review",
    sections: list[str] | None = None,
    max_items_per_section: int = 3,
    include_raw_metrics: bool = False,
    include_verilog: bool = False,
    write_artifacts: bool = True,
    run_store: RunStore | None = None,
) -> dict[str, Any]:
    if not str(run_id or "").strip():
        return _error_response("run_id is required.", ERROR_VALIDATION)
    option_error = validate_explanation_options(profile, sections)
    if option_error:
        return _error_response(option_error, ERROR_VALIDATION)
    try:
        item_limit = max(1, min(int(max_items_per_section), 20))
    except (TypeError, ValueError):
        return _error_response("max_items_per_section must be an integer.", ERROR_VALIDATION)

    selected_store = run_store or DEFAULT_RUN_STORE
    result = _ensure_standard_response(selected_store.result(str(run_id).strip()))
    if result.get("status") == "not_found":
        return result
    if result.get("status") not in {"completed", "needs_human_input", "stopped"}:
        return _success_response(
            {
                "status": result.get("status"),
                "summary": {
                    "run_id": run_id,
                    "message": "Run is not finished enough to explain yet. Poll status or request result first.",
                },
                "artifacts": result.get("artifacts", {}),
                "explanation": {},
                "explanation_artifacts": {},
            }
        )

    try:
        result_for_explanation = _hydrate_result_for_explanation(result)
        built = build_design_explanation(
            str(run_id).strip(),
            result_for_explanation,
            profile=profile,
            sections=sections,
            max_items_per_section=item_limit,
            include_raw_metrics=include_raw_metrics,
            include_verilog=include_verilog,
            write_artifacts=write_artifacts,
        )
    except Exception as exc:
        return _error_response(f"explanation failed: {exc}", ERROR_WORKFLOW)

    explanation = built["explanation"]
    explanation_artifacts = built["explanation_artifacts"]
    artifacts = dict(result.get("artifacts", {}) if isinstance(result.get("artifacts"), dict) else {})
    artifacts.update(explanation_artifacts)
    return _success_response(
        {
            "status": "completed",
            "summary": {
                "run_id": run_id,
                "profile": explanation.get("profile"),
                "sections": explanation.get("sections"),
                "headline": explanation.get("headline"),
                "explanation_artifact_count": len(explanation_artifacts),
            },
            "artifacts": artifacts,
            "explanation": explanation,
            "explanation_artifacts": explanation_artifacts,
        }
    )


def submit_design_feedback(
    run_id: str,
    constraints: list[str] | str,
    action: str = "repair",
    extra_budget: int = 2,
    run_store: RunStore | None = None,
) -> dict[str, Any]:
    selected_store = run_store or DEFAULT_RUN_STORE
    result = _ensure_standard_response(selected_store.result(str(run_id or "").strip()))
    if result.get("status") == "not_found":
        return result
    if result.get("status") != "needs_human_input":
        return _error_response("Feedback can only be submitted for a run that needs human input.", ERROR_VALIDATION)
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"repair", "exploitation", "fallback"}:
        return _error_response("action must be repair, exploitation, or fallback.", ERROR_VALIDATION)
    budget_result = _coerce_min_int(extra_budget, "extra_budget")
    if budget_result["status"] == "error":
        return budget_result
    if isinstance(constraints, str):
        normalized_constraints = [
            line.strip("- ").strip()
            for line in constraints.splitlines()
            if line.strip()
        ]
    elif isinstance(constraints, list):
        normalized_constraints = [str(item).strip() for item in constraints if str(item).strip()]
    else:
        return _error_response("constraints must be a string or list of strings.", ERROR_VALIDATION)
    if not normalized_constraints and normalized_action != "fallback":
        return _error_response("At least one constraint is required.", ERROR_VALIDATION)

    status = selected_store.status(str(run_id).strip())
    feedback_path = Path(str(status["run_dir"])) / "human_feedback.json"
    feedback = {
        "run_id": str(run_id).strip(),
        "constraints": normalized_constraints,
        "action": normalized_action,
        "extra_budget": budget_result["value"],
    }
    write_json(feedback_path, feedback)
    selected_store.append_event(
        str(run_id).strip(),
        "human_input",
        "completed",
        float(status.get("progress") or 1.0),
        "Human guidance was submitted.",
        {"action": normalized_action, "constraint_count": len(normalized_constraints)},
    )
    return _success_response(
        {
            "status": "completed",
            "run_id": str(run_id).strip(),
            "feedback": feedback,
            "summary": {"ready_to_resume": normalized_action != "fallback"},
            "artifacts": {"human_feedback_json": str(feedback_path.resolve())},
        }
    )


def resume_design_run(
    run_id: str,
    model_name: str | None = None,
    api_base: str | None = None,
    api_key: str | None = None,
    run_store: RunStore | None = None,
) -> dict[str, Any]:
    selected_store = run_store or DEFAULT_RUN_STORE
    parent_id = str(run_id or "").strip()
    result = _ensure_standard_response(selected_store.result(parent_id))
    if result.get("status") == "not_found":
        return result
    if result.get("status") != "needs_human_input":
        return _error_response("Only a run that needs human input can be resumed.", ERROR_VALIDATION)
    artifacts = result.get("artifacts", {})
    state_path = artifacts.get("state_json") if isinstance(artifacts, dict) else None
    status = selected_store.status(parent_id)
    feedback_path = Path(str(status.get("run_dir"))) / "human_feedback.json"
    if not state_path or not Path(str(state_path)).exists():
        return _error_response("The saved state.json required for resume is unavailable.", ERROR_NOT_FOUND)
    if not feedback_path.exists():
        return _error_response("Submit human feedback before resuming the run.", ERROR_VALIDATION)

    state = design_state_from_dict(json.loads(Path(str(state_path)).read_text(encoding="utf-8")))
    feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
    action = str(feedback.get("action") or "repair")
    constraints = [str(item) for item in feedback.get("constraints", [])]
    state.human_constraints.extend(item for item in constraints if item not in state.human_constraints)
    state.compute_budget += int(feedback.get("extra_budget") or 0)
    state.requires_human_input = False
    state.pause_reason = None
    state.human_feedback_prompt = None
    state.last_error = None
    if action == "fallback":
        state.is_completed = state.best_topology is not None
        return _error_response("Fallback selection does not require resume.", ERROR_VALIDATION)
    _add_guided_child(state, "Repair" if action == "repair" else "Exploitation")

    request = {
        "parent_run_id": parent_id,
        "user_intent": state.user_intent,
        "host_organism": state.host_organism,
        "compute_budget": state.compute_budget,
        "resume_action": action,
        "model_name": model_name,
        "api_base": api_base,
        "api_key": api_key,
    }
    child_id = f"run_{uuid.uuid4().hex[:12]}"
    started = selected_store.start(
        task=lambda: design_circuit_quick(
            user_intent=state.user_intent,
            host_organism=state.host_organism,
            compute_budget=state.compute_budget,
            model_name=model_name,
            api_base=api_base,
            api_key=api_key,
            initial_state=state,
            progress_callback=lambda stage, event_status, progress, message, details=None: selected_store.append_event(
                child_id, stage, event_status, progress, message, details
            ),
        ),
        request=request,
        run_id=child_id,
    )
    selected_store.append_event(
        child_id,
        "resume",
        "running",
        0.03,
        f"Resumed from parent run {parent_id}.",
        {"parent_run_id": parent_id, "action": action, "constraints": constraints},
    )
    started["parent_run_id"] = parent_id
    return started


def get_design_ir(
    run_id: str,
    revision_id: str | None = None,
    run_store: RunStore | None = None,
) -> dict[str, Any]:
    loaded = _load_design(run_id, revision_id, run_store)
    if isinstance(loaded, dict):
        return loaded
    design, path = loaded
    return _success_response(
        {
            "status": "completed",
            "run_id": run_id,
            "revision_id": design.revision.revision_id,
            "design": design.to_dict(),
            "summary": {
                "design_id": design.design_id,
                "part_count": len(design.parts),
                "construct_count": len(design.constructs),
                "warning_count": len(design.warnings),
            },
            "artifacts": {"design_ir_json": str(path.resolve())},
        }
    )


def list_compatible_replacements(
    run_id: str,
    target_part_id: str,
    revision_id: str | None = None,
    library_path: str | None = None,
    run_store: RunStore | None = None,
) -> dict[str, Any]:
    loaded = _load_design(run_id, revision_id, run_store)
    if isinstance(loaded, dict):
        return loaded
    design, _ = loaded
    target = next((part for part in design.parts if part.id == target_part_id), None)
    if target is None:
        return _error_response(f"Unknown target part: {target_part_id}", ERROR_NOT_FOUND)
    library = _load_library(library_path)
    if isinstance(library, dict):
        return library
    candidates = library.compatible_parts(
        part_type=target.part_type,
        host_organism=target.host_compatibility[0] if target.host_compatibility else None,
        gate_type=str(target.assignment.metadata.get("gate_type") or "") if target.assignment else None,
    )
    return _success_response(
        {
            "status": "completed",
            "run_id": run_id,
            "target_part_id": target_part_id,
            "library": _library_summary(library),
            "replacements": [asdict(item) for item in candidates],
            "summary": {"compatible_count": len(candidates)},
            "artifacts": {},
        }
    )


def validate_design_part_replacement(
    run_id: str,
    target_part_id: str,
    replacement_part_id: str,
    revision_id: str | None = None,
    library_path: str | None = None,
    run_store: RunStore | None = None,
) -> dict[str, Any]:
    loaded = _load_design(run_id, revision_id, run_store)
    if isinstance(loaded, dict):
        return loaded
    design, _ = loaded
    library = _load_library(library_path)
    if isinstance(library, dict):
        return library
    validation = validate_replacement(
        design,
        target_part_id=target_part_id,
        replacement_part_id=replacement_part_id,
        library=library,
    )
    return _success_response(
        {
            "status": "completed",
            "run_id": run_id,
            "validation": asdict(validation),
            "summary": {"valid": validation.valid},
            "artifacts": {},
        }
    )


def replace_design_part(
    run_id: str,
    target_part_id: str,
    replacement_part_id: str,
    revision_id: str | None = None,
    library_path: str | None = None,
    created_by: str = "mcp_user",
    run_store: RunStore | None = None,
) -> dict[str, Any]:
    loaded = _load_design(run_id, revision_id, run_store)
    if isinstance(loaded, dict):
        return loaded
    design, source_path = loaded
    library = _load_library(library_path)
    if isinstance(library, dict):
        return library
    replacement = replace_part_immutable(
        design,
        target_part_id=target_part_id,
        replacement_part_id=replacement_part_id,
        library=library,
        created_by=created_by,
    )
    if replacement.design is None:
        return _success_response(
            {
                "status": "completed",
                "run_id": run_id,
                "validation": asdict(replacement.validation),
                "design": None,
                "summary": {"replaced": False},
                "artifacts": {"source_design_ir_json": str(source_path.resolve())},
            }
        )
    revision_path = source_path.parent / f"{replacement.design.revision.revision_id}.json"
    write_json(revision_path, replacement.design)
    return _success_response(
        {
            "status": "completed",
            "run_id": run_id,
            "validation": asdict(replacement.validation),
            "design": replacement.design.to_dict(),
            "summary": {
                "replaced": True,
                "revision_id": replacement.design.revision.revision_id,
                "parent_revision_id": replacement.design.revision.parent_revision_id,
            },
            "artifacts": {"design_revision_json": str(revision_path.resolve())},
        }
    )


def compare_design_revisions(
    run_id: str,
    left_revision_id: str,
    right_revision_id: str,
    run_store: RunStore | None = None,
) -> dict[str, Any]:
    left_loaded = _load_design(run_id, left_revision_id, run_store)
    if isinstance(left_loaded, dict):
        return left_loaded
    right_loaded = _load_design(run_id, right_revision_id, run_store)
    if isinstance(right_loaded, dict):
        return right_loaded
    diff = compare_designs(left_loaded[0], right_loaded[0])
    return _success_response(
        {
            "status": "completed",
            "run_id": run_id,
            "diff": asdict(diff),
            "summary": {"description": diff.summary, "recommendation": diff.recommendation},
            "artifacts": {},
        }
    )


def export_design(
    run_id: str,
    revision_id: str | None = None,
    formats: list[str] | None = None,
    run_store: RunStore | None = None,
) -> dict[str, Any]:
    loaded = _load_design(run_id, revision_id, run_store)
    if isinstance(loaded, dict):
        return loaded
    design, design_path = loaded
    selected_formats = [str(item).lower() for item in (formats or ["bom", "genbank", "sbol3"])]
    exporters = {
        "bom": export_bom_csv,
        "genbank": export_genbank,
        "sbol3": export_sbol3_turtle,
    }
    unknown = [item for item in selected_formats if item not in exporters]
    if unknown:
        return _error_response(f"Unsupported export formats: {', '.join(unknown)}", ERROR_VALIDATION)
    export_dir = design_path.parent / "exports" / design.revision.revision_id
    export_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    artifacts = {}
    for name in selected_formats:
        exported = exporters[name](design)
        result_payload = asdict(exported)
        results[name] = result_payload
        if exported.ok:
            path = export_dir / exported.filename
            write_text(path, exported.content)
            artifacts[name] = str(path.resolve())
    manifest_path = export_dir / "export_manifest.json"
    write_json(manifest_path, {"run_id": run_id, "revision_id": design.revision.revision_id, "exports": results})
    artifacts["export_manifest_json"] = str(manifest_path.resolve())
    return _success_response(
        {
            "status": "completed",
            "run_id": run_id,
            "revision_id": design.revision.revision_id,
            "exports": results,
            "summary": {
                "requested_count": len(selected_formats),
                "ready_count": sum(1 for item in results.values() if item["ok"]),
                "blocked_count": sum(1 for item in results.values() if not item["ok"]),
            },
            "artifacts": artifacts,
        }
    )


def evaluate_verilog(
    verilog: str,
    user_intent: str = "Evaluate a Cello-compatible genetic circuit.",
    host_organism: str = "Escherichia coli",
    enable_ode: bool = True,
    monte_carlo_samples: int = 1,
    output_dir: str | None = None,
    cello_command: str | None = None,
    ucf_path: str | None = None,
) -> dict[str, Any]:
    verilog = str(verilog or "").strip()
    if not verilog:
        return _error_response("verilog is required.", ERROR_VALIDATION)
    monte_carlo_result = _coerce_min_int(monte_carlo_samples, "monte_carlo_samples")
    if monte_carlo_result["status"] == "error":
        return monte_carlo_result

    state = DesignState(user_intent=user_intent, host_organism=host_organism, compute_budget=1)
    node = SearchNode(node_id="root", search_mode="Exploration")
    node.verilog_codes = [verilog]
    state.tree_nodes["root"] = node
    state.current_node_id = "root"
    state.verilog_codes = [verilog]

    try:
        state = CelloWrapper(
            cello_command=cello_command,
            ucf_path=ucf_path,
            artifact_dir=Path(output_dir) / "cello_artifacts" if output_dir else None,
        ).run(state)
        if state.last_error:
            return _error_response(state.last_error, ERROR_EXTERNAL_TOOL)
        state = DataMinerAgent().run(state) if enable_ode else state
        state = (
            BatchODESimulator(monte_carlo_samples=monte_carlo_result["value"]).run(state)
            if enable_ode
            else NoOpODESimulator().run(state)
        )
        for topology in node.candidate_topologies:
            topology.update(evaluate_candidate(topology))
        best_topology = select_best_topology(node.candidate_topologies)
        node.best_topology = best_topology
        node.sync_evaluation_metrics(best_topology)
        state.best_topology = best_topology
    except Exception as exc:
        return _error_response(f"evaluation failed: {exc}", ERROR_WORKFLOW)

    run_dir = create_run_dir(output_dir)
    charts = render_charts(state.best_topology, run_dir)
    artifacts = write_state_artifacts(state, run_dir, charts)
    write_json(run_dir / "input_verilog.json", {"verilog": verilog})
    return _success_response({
        "status": "completed",
        "run_dir": str(run_dir.resolve()),
        "summary": summarize_state(state),
        "best_topology": summarize_topology(state.best_topology),
        "artifacts": artifacts,
    })


def _hydrate_result_for_explanation(result: dict[str, Any]) -> dict[str, Any]:
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, dict):
        return result
    state_path = artifacts.get("state_json")
    if not state_path:
        return result
    try:
        state_json = json.loads(Path(str(state_path)).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return result
    if not isinstance(state_json, dict):
        return result
    full_topology = state_json.get("best_topology")
    if not isinstance(full_topology, dict) or not full_topology:
        return result
    hydrated = dict(result)
    summary = dict(hydrated.get("summary", {}) if isinstance(hydrated.get("summary"), dict) else {})
    summary["best_topology"] = full_topology
    hydrated["summary"] = summary
    hydrated.setdefault("best_topology", full_topology)
    return hydrated


def summarize_design_state(state_json: dict[str, Any]) -> dict[str, Any]:
    """Summarize a saved state JSON produced by this adapter."""
    return _success_response({
        "status": "completed",
        "summary": {
            "user_intent": state_json.get("user_intent"),
            "host_organism": state_json.get("host_organism"),
            "is_completed": state_json.get("is_completed"),
            "is_approved": state_json.get("is_approved"),
            "requires_human_input": state_json.get("requires_human_input"),
            "pause_reason": state_json.get("pause_reason"),
            "current_node_id": state_json.get("current_node_id"),
            "best_topology": summarize_topology(state_json.get("best_topology")),
        },
        "artifacts": {},
    })


def _add_guided_child(state: DesignState, search_mode: str) -> None:
    parent = state.tree_nodes.get(state.current_node_id) if state.current_node_id else None
    if parent is None:
        raise ValueError("Cannot resume without a current search node.")
    suffix = "repair" if search_mode == "Repair" else "exploit"
    child_id = f"{parent.node_id}_{suffix}_{uuid.uuid4().hex[:4]}"
    child = SearchNode(
        node_id=child_id,
        parent_id=parent.node_id,
        search_mode=search_mode,
        logic_proposals=parent.logic_proposals.copy() if search_mode == "Exploitation" else [],
        critic_feedbacks=parent.critic_feedbacks.copy(),
        failed_attempts=parent.failed_attempts.copy(),
        error_type=parent.error_type,
    )
    parent.children_ids.append(child_id)
    if parent.status == "Needs_Human_Input":
        parent.status = "Evaluated"
    state.tree_nodes[child_id] = child
    state.active_frontier.insert(0, child_id)
    state.current_node_id = child_id


def _load_design(
    run_id: str,
    revision_id: str | None,
    run_store: RunStore | None,
) -> tuple[DesignIR, Path] | dict[str, Any]:
    selected_store = run_store or DEFAULT_RUN_STORE
    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        return _error_response("run_id is required.", ERROR_VALIDATION)
    result = _ensure_standard_response(selected_store.result(normalized_run_id))
    if result.get("status") == "not_found":
        return result
    if result.get("status") not in {"completed", "needs_human_input", "stopped"}:
        return _error_response("Run is not ready for design operations.", ERROR_VALIDATION)
    artifacts = result.get("artifacts", {})
    state_path = artifacts.get("state_json") if isinstance(artifacts, dict) else None
    if not state_path or not Path(str(state_path)).exists():
        return _error_response("The run does not have a readable state.json artifact.", ERROR_NOT_FOUND)
    revisions_dir = Path(str(state_path)).parent / "design_revisions"
    revisions_dir.mkdir(parents=True, exist_ok=True)
    if revision_id:
        revision_path = revisions_dir / f"{revision_id}.json"
        if not revision_path.exists():
            return _error_response(f"Unknown design revision: {revision_id}", ERROR_NOT_FOUND)
        try:
            return design_ir_from_dict(json.loads(revision_path.read_text(encoding="utf-8"))), revision_path
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return _error_response(f"Could not load design revision: {exc}", ERROR_WORKFLOW)

    existing_revisions = sorted(revisions_dir.glob("*.json"))
    if existing_revisions:
        initial_path = existing_revisions[0]
        return design_ir_from_dict(json.loads(initial_path.read_text(encoding="utf-8"))), initial_path
    state_payload = json.loads(Path(str(state_path)).read_text(encoding="utf-8"))
    topology = state_payload.get("best_topology")
    if not isinstance(topology, dict) or not topology:
        return _error_response("The run does not contain a best topology.", ERROR_NOT_FOUND)
    design = topology_to_design_ir(
        topology,
        host_organism=str(state_payload.get("host_organism") or "Escherichia coli"),
        design_id=f"{normalized_run_id}_design",
    )
    initial_path = revisions_dir / f"{design.revision.revision_id}.json"
    write_json(initial_path, design)
    return design, initial_path


def _load_library(library_path: str | None) -> PartLibrary | dict[str, Any]:
    try:
        return PartLibrary.from_json(library_path) if library_path else PartLibrary.demo()
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return _error_response(f"Could not load part library: {exc}", ERROR_VALIDATION)


def _library_summary(library: PartLibrary) -> dict[str, Any]:
    return {
        "library_id": library.library_id,
        "version": library.version,
        "name": library.name,
        "evidence_level": library.evidence_level,
        "source_path": library.source_path,
    }


def _resolve_model(model_name: str | None) -> str:
    return model_name or os.getenv("LITELLM_MODEL") or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL


def _resolve_api_key(api_key: str | None) -> str | None:
    return api_key or os.getenv("LITELLM_API_KEY") or os.getenv("OPENAI_API_KEY") or None


def _status_from_state(state: DesignState) -> str:
    if state.last_error:
        return "error"
    if state.requires_human_input:
        return "needs_human_input"
    if state.is_completed or state.best_topology:
        return "completed"
    return "stopped"


def _success_response(payload: dict[str, Any]) -> dict[str, Any]:
    payload.setdefault("error", None)
    payload.setdefault("error_type", None)
    payload.setdefault("summary", {})
    payload.setdefault("artifacts", {})
    return payload


def _error_response(message: str, error_type: str) -> dict[str, Any]:
    return {
        "status": "error" if error_type != ERROR_NOT_FOUND else "not_found",
        "error": message,
        "error_type": error_type,
        "summary": {},
        "artifacts": {},
    }


def _ensure_standard_response(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("status") == "not_found":
        payload.setdefault("error_type", ERROR_NOT_FOUND)
    else:
        payload.setdefault("error_type", None)
    payload.setdefault("error", None)
    payload.setdefault("summary", {})
    payload.setdefault("artifacts", {})
    return payload


def _coerce_min_int(value: Any, field_name: str) -> dict[str, Any]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return _error_response(f"{field_name} must be an integer.", ERROR_VALIDATION)
    if parsed < 1:
        return _error_response(f"{field_name} must be at least 1.", ERROR_VALIDATION)
    return {"status": "ok", "value": parsed}


def _normalize_run_ids(run_ids: Any) -> dict[str, Any]:
    if not isinstance(run_ids, list):
        return _error_response("run_ids must be a list of run IDs.", ERROR_VALIDATION)
    normalized = [str(run_id).strip() for run_id in run_ids if str(run_id or "").strip()]
    if len(normalized) < 2:
        return _error_response("compare_design_runs requires between 2 and 10 run IDs.", ERROR_VALIDATION)
    if len(normalized) > 10:
        return _error_response("compare_design_runs accepts at most 10 run IDs.", ERROR_VALIDATION)
    return {"status": "ok", "run_ids": normalized}


def _comparison_entry(run_id: str, result: dict[str, Any]) -> dict[str, Any]:
    summary = result.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}
    best_topology = _best_topology_from_result(result)
    artifacts = result.get("artifacts", {})
    if not isinstance(artifacts, dict):
        artifacts = {}
    return {
        "run_id": run_id,
        "status": result.get("status"),
        "score": _metric(best_topology, summary, "score"),
        "mapping_status": _metric(best_topology, summary, "mapping_status"),
        "cello_mode": _metric(best_topology, summary, "cello_mode"),
        "cello_claim_level": _metric(best_topology, summary, "cello_claim_level"),
        "cello_warning": _metric(best_topology, summary, "cello_warning"),
        "ode_status": _metric(best_topology, summary, "ode_status"),
        "cello_buildable": _metric(best_topology, summary, "cello_buildable"),
        "robustness_score": _metric(best_topology, summary, "robustness_score"),
        "toxicity_score": _metric(best_topology, summary, "toxicity_score"),
        "semantic_faithfulness_score": _metric(best_topology, summary, "semantic_faithfulness_score"),
        "artifact_count": len(artifacts),
        "artifact_keys": sorted(artifacts.keys()),
        "run_dir": result.get("run_dir") or result.get("workflow_run_dir"),
        "best_topology": best_topology,
    }


def _metric(best_topology: dict[str, Any], summary: dict[str, Any], key: str) -> Any:
    if key in best_topology:
        return best_topology[key]
    return summary.get(key)


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _diagnose_findings(
    result: dict[str, Any],
    summary: dict[str, Any],
    best_topology: dict[str, Any],
    failed_attempts: list[Any],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    run_status = result.get("status")
    if run_status != "completed":
        findings.append(
            _finding(
                "high",
                "run_status",
                f"Run status is {run_status}, so the design may not be ready for evaluation.",
                {"status": run_status, "error": result.get("error"), "error_type": result.get("error_type")},
            )
        )
    if summary.get("requires_human_input"):
        findings.append(
            _finding(
                "high",
                "human_input",
                "Workflow paused and requires human guidance before it can be considered complete.",
                {"pause_reason": summary.get("pause_reason"), "prompt": summary.get("human_feedback_prompt")},
            )
        )
    if not best_topology:
        findings.append(
            _finding(
                "high",
                "topology",
                "No best topology is available in the run result.",
                {"current_node_id": summary.get("current_node_id")},
            )
        )

    mapping_status = str(_metric(best_topology, summary, "mapping_status") or "").lower()
    source = str(_metric(best_topology, summary, "source") or "").lower()
    cello_mode = str(_metric(best_topology, summary, "cello_mode") or "").lower()
    claim_level = str(_metric(best_topology, summary, "cello_claim_level") or "").lower()
    if cello_mode == "mock" or claim_level == "mock_only" or "mock" in source:
        findings.append(
            _finding(
                "warning",
                "cello_provenance",
                "Cello output is mock/demo only and should not be treated as real part mapping.",
                {
                    "source": source or None,
                    "cello_mode": cello_mode or None,
                    "cello_claim_level": claim_level or None,
                    "cello_warning": _metric(best_topology, summary, "cello_warning"),
                },
            )
        )
    if mapping_status in {"failed", "unmapped", "error"}:
        findings.append(
            _finding(
                "high" if mapping_status == "failed" else "warning",
                "mapping",
                "Cello mapping did not produce a clearly mapped design.",
                {"mapping_status": mapping_status or None},
            )
        )

    ode_status = str(_metric(best_topology, summary, "ode_status") or "").lower()
    if ode_status in {"failed", "error", "disabled"}:
        findings.append(
            _finding(
                "warning",
                "ode",
                "ODE validation is unavailable or did not complete successfully.",
                {"ode_status": ode_status or None},
            )
        )

    for metric_name, category in (
        ("score", "score"),
        ("robustness_score", "robustness"),
        ("semantic_faithfulness_score", "semantics"),
    ):
        metric_value = _float_or_none(_metric(best_topology, summary, metric_name))
        if metric_value is None:
            continue
        if metric_value < 0.25:
            severity = "high"
        elif metric_value < 0.5:
            severity = "warning"
        else:
            continue
        findings.append(
            _finding(
                severity,
                category,
                f"{metric_name} is below the recommended threshold.",
                {metric_name: metric_value},
            )
        )

    toxicity_score = _float_or_none(_metric(best_topology, summary, "toxicity_score"))
    toxicity = _float_or_none(_metric(best_topology, summary, "toxicity"))
    if toxicity_score is not None and toxicity_score < 0.5:
        findings.append(
            _finding(
                "warning" if toxicity_score >= 0.25 else "high",
                "toxicity",
                "Toxicity score suggests possible part burden or toxicity risk.",
                {"toxicity_score": toxicity_score, "toxicity": toxicity},
            )
        )

    feedback = str(summary.get("latest_critic_feedback") or "").strip()
    if feedback:
        findings.append(
            _finding(
                "info",
                "critic",
                "Critic feedback is available for this run.",
                {"latest_critic_feedback": feedback},
            )
        )

    if failed_attempts:
        findings.append(
            _finding(
                "warning",
                "search",
                "The workflow recorded failed attempts before reaching this result.",
                {"failed_attempt_count": len(failed_attempts), "recent_failed_attempt": failed_attempts[-1]},
            )
        )
    return findings


def _finding(severity: str, category: str, message: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "severity": severity,
        "category": category,
        "message": message,
        "evidence": evidence,
    }


def _diagnosis_status(findings: list[dict[str, Any]]) -> str:
    severities = {finding.get("severity") for finding in findings}
    if "high" in severities:
        return "needs_attention"
    if "warning" in severities:
        return "watch"
    return "healthy"


def _likely_causes(findings: list[dict[str, Any]]) -> list[str]:
    causes_by_category = {
        "run_status": "The run did not complete successfully or is not ready for final evaluation.",
        "human_input": "The workflow needs additional constraints, trade-offs, or fallback guidance.",
        "topology": "The workflow did not produce a usable best topology.",
        "cello_provenance": "The Cello result is mock/demo or has unclear provenance.",
        "mapping": "Cello constraints, UCF configuration, or Verilog structure may prevent mapping.",
        "ode": "ODE simulation was disabled, unavailable, or failed during validation.",
        "score": "The candidate design underperformed across weighted benchmark criteria.",
        "robustness": "The design may be sensitive to parameter variation or biological noise.",
        "semantics": "The generated circuit may not faithfully match the original intent.",
        "toxicity": "Selected parts may introduce burden or toxicity concerns.",
        "critic": "The critic identified concerns that should be reviewed.",
        "search": "Earlier search branches failed before the current candidate was selected.",
    }
    causes = []
    for finding in findings:
        cause = causes_by_category.get(str(finding.get("category")))
        if cause and cause not in causes:
            causes.append(cause)
    return causes


def _recommended_actions(findings: list[dict[str, Any]]) -> list[str]:
    actions_by_category = {
        "run_status": "Check the run result error fields and rerun after resolving the reported issue.",
        "human_input": "Provide additional design constraints, acceptable trade-offs, or a preferred fallback topology.",
        "topology": "Rerun with a larger compute budget or inspect failed attempts for the blocking stage.",
        "cello_provenance": "Run with an external Cello command and compatible UCF before making buildability claims.",
        "mapping": "Inspect the Verilog, Cello command, and UCF path; consider simplifying gates or changing allowed parts.",
        "ode": "Enable ODE validation or inspect the ODE simulator inputs before trusting dynamic behavior.",
        "score": "Compare against higher-scoring runs and use the best topology as the next design seed.",
        "robustness": "Increase Monte Carlo samples and tune parts for stronger dynamic margins.",
        "semantics": "Clarify the natural-language intent and add edge cases for the translator/critic loop.",
        "toxicity": "Review part burden and toxicity metrics; prefer lower-burden gates or alternative host constraints.",
        "critic": "Review latest critic feedback before launching a repair or exploitation branch.",
        "search": "Use failed attempt records to choose between logic repair and part-level exploitation.",
    }
    actions = []
    for finding in findings:
        action = actions_by_category.get(str(finding.get("category")))
        if action and action not in actions:
            actions.append(action)
    if not actions:
        actions.append("No immediate action is required; keep the run as a viable candidate.")
    return actions
