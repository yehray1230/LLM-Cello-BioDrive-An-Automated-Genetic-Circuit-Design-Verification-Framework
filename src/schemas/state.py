from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from utils.boolean_values import defaulted_bool as _coerce_bool

SearchMode = Literal["Exploration", "Repair", "Exploitation"]
NodeStatus = Literal["Pending", "Evaluated", "Pass", "Dead_End", "Needs_Human_Input"]
ErrorType = Literal["LOGIC_ERROR", "PART_ERROR", "BOTH", "NONE"]


def _coerce_float(value: Any, default: float) -> float:
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int) -> int:
    try:
        return default if value is None else int(value)
    except (TypeError, ValueError):
        return default


def _coerce_str_list(value: Any, default: list[str]) -> list[str]:
    if value is None:
        return default
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    return default


@dataclass
class SearchNode:
    node_id: str
    parent_id: str | None = None
    children_ids: list[str] = field(default_factory=list)
    search_mode: SearchMode = "Exploration"
    status: NodeStatus = "Pending"

    logic_proposals: list[str] = field(default_factory=list)
    verilog_codes: list[str] = field(default_factory=list)
    candidate_topologies: list[dict[str, Any]] = field(default_factory=list)
    current_topology: str = ""
    best_topology: dict[str, Any] | None = None
    score: float = -float("inf")
    metabolic_burden_score: float = 1.0
    gate_count: int = 0
    complexity_penalty: float = 0.0
    robustness_score: float = 1.0
    signal_to_noise_ratio: float = 0.0
    monte_carlo_runs: int = 0
    temporal_score: float = 1.0
    rise_time: float | None = None
    orthogonality_score: float = 1.0
    cello_assignment_score: float = 0.0
    cello_buildable: bool = False
    semantic_faithfulness_score: float = 1.0
    missed_edge_cases: list[str] = field(default_factory=list)

    critic_feedbacks: list[str] = field(default_factory=list)
    failed_attempts: list[dict[str, Any]] = field(default_factory=list)
    is_approved: bool = False
    error_type: ErrorType = "NONE"
    last_error: str | None = None
    last_recommendation: dict[str, Any] | None = None
    self_healing_history: list[dict[str, Any]] = field(default_factory=list)

    def sync_evaluation_metrics(self, topology: dict[str, Any] | None) -> None:
        if not topology:
            return
        benchmark_report = topology.get("benchmark_report")
        if not isinstance(benchmark_report, dict):
            benchmark_report = {}
        self.score = _coerce_float(
            topology.get("score", benchmark_report.get("score")),
            self.score,
        )
        self.metabolic_burden_score = _coerce_float(
            topology.get(
                "metabolic_burden_score",
                benchmark_report.get("metabolic_burden_score"),
            ),
            self.metabolic_burden_score,
        )
        self.gate_count = _coerce_int(
            topology.get("gate_count", benchmark_report.get("gate_count")),
            self.gate_count,
        )
        self.complexity_penalty = _coerce_float(
            topology.get(
                "complexity_penalty",
                benchmark_report.get("complexity_penalty"),
            ),
            self.complexity_penalty,
        )
        self.robustness_score = _coerce_float(
            topology.get(
                "robustness_score",
                benchmark_report.get("robustness_score"),
            ),
            self.robustness_score,
        )
        self.signal_to_noise_ratio = _coerce_float(
            topology.get(
                "signal_to_noise_ratio",
                topology.get(
                    "snr",
                    benchmark_report.get(
                        "signal_to_noise_ratio",
                        benchmark_report.get("snr"),
                    ),
                ),
            ),
            self.signal_to_noise_ratio,
        )
        self.monte_carlo_runs = _coerce_int(
            topology.get(
                "monte_carlo_runs",
                topology.get(
                    "monte_carlo_samples",
                    benchmark_report.get(
                        "monte_carlo_runs",
                        benchmark_report.get("monte_carlo_samples"),
                    ),
                ),
            ),
            self.monte_carlo_runs,
        )
        self.temporal_score = _coerce_float(
            topology.get(
                "temporal_score",
                benchmark_report.get("temporal_score"),
            ),
            self.temporal_score,
        )
        rise_time = topology.get("rise_time", benchmark_report.get("rise_time"))
        self.rise_time = None if rise_time is None else _coerce_float(rise_time, self.rise_time or 0.0)
        self.orthogonality_score = _coerce_float(
            topology.get(
                "orthogonality_score",
                benchmark_report.get("orthogonality_score"),
            ),
            self.orthogonality_score,
        )
        self.cello_assignment_score = _coerce_float(
            topology.get(
                "cello_assignment_score",
                benchmark_report.get("cello_assignment_score"),
            ),
            self.cello_assignment_score,
        )
        self.cello_buildable = _coerce_bool(
            topology.get(
                "cello_buildable",
                benchmark_report.get("cello_buildable"),
            ),
            self.cello_buildable,
        )
        self.semantic_faithfulness_score = _coerce_float(
            topology.get(
                "semantic_faithfulness_score",
                benchmark_report.get("semantic_faithfulness_score"),
            ),
            self.semantic_faithfulness_score,
        )
        self.missed_edge_cases = _coerce_str_list(
            topology.get(
                "missed_edge_cases",
                topology.get(
                    "missed_conditions",
                    benchmark_report.get(
                        "missed_edge_cases",
                        benchmark_report.get("missed_conditions"),
                    ),
                ),
            ),
            self.missed_edge_cases,
        )


@dataclass
class DesignState:
    user_intent: str = ""
    host_organism: str = "Escherichia coli"

    tree_nodes: dict[str, SearchNode] = field(default_factory=dict)
    active_frontier: list[str] = field(default_factory=list)
    current_node_id: str | None = None
    compute_budget: int = 6
    used_budget: int = 0

    skill_context: str = ""
    rag_context: str = ""
    skill_library_context: str = ""
    seed_debate_transcript: str = ""
    biokinetic_context: dict[str, Any] = field(default_factory=dict)
    extracted_skills: list[dict[str, Any]] = field(default_factory=list)

    logic_proposals: list[str] = field(default_factory=list)
    verilog_codes: list[str] = field(default_factory=list)
    candidate_topologies: list[dict[str, Any]] = field(default_factory=list)
    current_topology: str = ""
    best_topology: dict[str, Any] | None = None

    critic_feedbacks: list[str] = field(default_factory=list)
    failed_attempts: list[dict[str, Any]] = field(default_factory=list)
    is_approved: bool = False
    error_type: ErrorType = "NONE"
    is_completed: bool = False
    requires_human_input: bool = False
    human_feedback_prompt: str | None = None
    pause_reason: str | None = None
    human_constraints: list[str] = field(default_factory=list)
    iteration_count: int = 0
    last_error: str | None = None
    last_recommendation: dict[str, Any] | None = None

    # PM Agent Fields
    structured_spec: dict[str, Any] = field(default_factory=dict)
    pm_chat_history: list[dict[str, str]] = field(default_factory=list)
    pending_proposal: dict[str, Any] = field(default_factory=dict)
    pm_stage: Literal["elicitation", "engine_running", "hitl_dialogue", "completed"] = "elicitation"

    @property
    def latest_critic_feedback(self) -> str:
        if self.current_node_id and self.current_node_id in self.tree_nodes:
            feedbacks = self.tree_nodes[self.current_node_id].critic_feedbacks
            if feedbacks:
                return feedbacks[-1]
        return self.critic_feedbacks[-1] if self.critic_feedbacks else ""

