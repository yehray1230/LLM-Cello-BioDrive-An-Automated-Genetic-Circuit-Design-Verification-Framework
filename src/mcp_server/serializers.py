from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from schemas.state import DesignState, SearchNode


SUMMARY_TOPOLOGY_KEYS = (
    "source",
    "cello_mode",
    "cello_claim_level",
    "cello_warning",
    "cello_artifact_dir",
    "cello_artifact_manifest_path",
    "part_assignments",
    "design_revision",
    "score",
    "weighted_total_score",
    "mapping_status",
    "ode_status",
    "verilog",
    "gate_count",
    "gene_count",
    "dynamic_margin",
    "kinetic_score",
    "robustness_score",
    "signal_to_noise_ratio",
    "monte_carlo_runs",
    "monte_carlo_failure_rate",
    "metrics_max_burden",
    "metrics_cv",
    "orthogonality_score",
    "cello_assignment_score",
    "cello_assignment_raw_score",
    "cello_buildable",
    "toxicity",
    "toxicity_score",
    "semantic_faithfulness_score",
    "missed_edge_cases",
    "benchmark_report",
    "simulation_model_version",
    "simulation_spec",
    "simulation_result",
    "ode_trace",
)


def to_jsonable(value: Any) -> Any:
    """Convert dataclasses, paths, and common numeric containers into JSON-safe values."""
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def summarize_topology(topology: dict[str, Any] | None) -> dict[str, Any]:
    if not topology:
        return {}
    return {key: to_jsonable(topology[key]) for key in SUMMARY_TOPOLOGY_KEYS if key in topology}


def summarize_state(state: Any) -> dict[str, Any]:
    current_node = None
    if state.current_node_id:
        current_node = state.tree_nodes.get(state.current_node_id)

    tree_summary = []
    for node_id, node in state.tree_nodes.items():
        tree_summary.append(
            {
                "node_id": node_id,
                "parent_id": node.parent_id,
                "children_ids": list(node.children_ids),
                "search_mode": node.search_mode,
                "status": node.status,
                "score": node.score,
                "is_approved": node.is_approved,
                "error_type": node.error_type,
                "critic_feedback": node.critic_feedbacks[-1] if node.critic_feedbacks else "",
            }
        )

    return {
        "user_intent": state.user_intent,
        "host_organism": state.host_organism,
        "is_completed": state.is_completed,
        "is_approved": state.is_approved,
        "requires_human_input": state.requires_human_input,
        "pause_reason": state.pause_reason,
        "human_feedback_prompt": state.human_feedback_prompt,
        "current_node_id": state.current_node_id,
        "current_node_status": current_node.status if current_node else None,
        "compute_budget": state.compute_budget,
        "used_budget": state.used_budget,
        "active_frontier": list(state.active_frontier),
        "error_type": state.error_type,
        "last_error": state.last_error,
        "latest_critic_feedback": state.latest_critic_feedback,
        "logic_proposals": to_jsonable(state.logic_proposals),
        "verilog_codes": to_jsonable(state.verilog_codes),
        "best_topology": summarize_topology(state.best_topology),
        "failed_attempts": to_jsonable(state.failed_attempts),
        "tree_summary": to_jsonable(tree_summary),
    }


def design_state_from_dict(payload: dict[str, Any]) -> DesignState:
    state_fields = DesignState.__dataclass_fields__
    state_kwargs = {
        key: value
        for key, value in payload.items()
        if key in state_fields and key != "tree_nodes"
    }
    state = DesignState(**state_kwargs)
    raw_nodes = payload.get("tree_nodes", {})
    if isinstance(raw_nodes, dict):
        node_fields = SearchNode.__dataclass_fields__
        for node_id, raw_node in raw_nodes.items():
            if not isinstance(raw_node, dict):
                continue
            node_kwargs = {
                key: value
                for key, value in raw_node.items()
                if key in node_fields
            }
            node_kwargs.setdefault("node_id", str(node_id))
            state.tree_nodes[str(node_id)] = SearchNode(**node_kwargs)
    return state

