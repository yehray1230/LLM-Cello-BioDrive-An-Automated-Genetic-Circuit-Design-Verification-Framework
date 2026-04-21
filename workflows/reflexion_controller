from __future__ import annotations

from agents.builder_agent import call_builder
from agents.consolidator_agent import call_design_consolidator
from agents.critic_agent import call_critic
from schemas.state import CircuitState, RoundRecord


MAX_ITERATIONS = 5


def _is_error_text(text: str | None) -> bool:
    return bool(text) and str(text).startswith("ERROR:")


def _extract_critic_route(feedback: str | None) -> str:
    """
    Normalize Critic output into one of:
    - PASS
    - REVISE
    - FATAL
    - UNKNOWN
    """
    if not feedback:
        return "UNKNOWN"

    normalized = feedback.upper()

    if "[PASS]" in normalized or "審查結論：通過" in feedback or "PASS" in normalized:
        return "PASS"
    if "[FATAL]" in normalized or "FATAL" in normalized:
        return "FATAL"
    if "[REVISE]" in normalized or "REVISE" in normalized or "需修改" in feedback:
        return "REVISE"

    return "UNKNOWN"


def run_reflexion_workflow(
    api_key: str | None,
    model_name: str,
    state: CircuitState,
    *,
    on_round_complete=None,
    api_base: str | None = None,
    force_zero_shot: bool = False,
    host_organism: str = "Escherichia coli",
    max_iterations: int = MAX_ITERATIONS,
) -> tuple[bool, CircuitState]:
    """
    Dynamic state-machine workflow for Builder/Critic/Consolidator routing.
    """
    state.last_error = None
    state.host_organism = host_organism

    if not state.rag_context.strip():
        state.last_error = "ERROR: missing RAG context."
        return False, state

    if state.rag_context.startswith("ERROR:"):
        state.last_error = state.rag_context
        return False, state

    current_node = "Builder"

    while True:
        if current_node == "Builder":
            if state.current_round >= max_iterations:
                current_node = "Human_in_Loop"
                continue

            state.current_round += 1

            if state.current_round > 1:
                state.seed_debate_transcript = None

            state = call_builder(
                state,
                api_key,
                model_name,
                api_base=api_base,
                force_zero_shot=force_zero_shot,
            )

            if _is_error_text(state.current_topology):
                state.last_error = state.current_topology
                return False, state

            current_node = "Critic"
            continue

        if current_node == "Critic":
            prev_feedback_count = len(state.critic_feedbacks)

            state = call_critic(
                state,
                api_key,
                model_name,
                api_base=api_base,
                force_zero_shot=force_zero_shot,
            )

            latest_feedback = state.latest_critic_feedback

            if _is_error_text(latest_feedback):
                state.last_error = latest_feedback
                return False, state

            if len(state.critic_feedbacks) > prev_feedback_count:
                state.round_history.append(
                    RoundRecord(
                        round_number=state.current_round,
                        builder_output=state.current_topology or "",
                        critic_output=latest_feedback or "",
                    )
                )

            if on_round_complete:
                on_round_complete(
                    state.current_round,
                    state.current_topology or "",
                    latest_feedback or "",
                )

            critic_route = _extract_critic_route(latest_feedback)

            if critic_route == "PASS":
                state.is_approved = True
                current_node = "Consolidator"
                continue

            if critic_route in {"REVISE", "FATAL", "UNKNOWN"}:
                state.is_approved = False
                if state.current_round < max_iterations:
                    current_node = "Builder"
                else:
                    current_node = "Human_in_Loop"
                continue

        if current_node == "Consolidator":
            state = call_design_consolidator(
                state,
                api_key,
                model_name,
                api_base=api_base,
            )

            if _is_error_text(state.formal_specification):
                state.last_error = state.formal_specification
                return False, state

            return True, state

        if current_node == "Human_in_Loop":
            unresolved_feedback = state.latest_critic_feedback or "No critic feedback available."
            state.last_error = (
                "Human review required: workflow reached max iterations without approval.\n\n"
                f"Latest critic feedback:\n{unresolved_feedback}"
            )
            return False, state

        state.last_error = f"ERROR: unknown workflow node `{current_node}`."
        return False, state
