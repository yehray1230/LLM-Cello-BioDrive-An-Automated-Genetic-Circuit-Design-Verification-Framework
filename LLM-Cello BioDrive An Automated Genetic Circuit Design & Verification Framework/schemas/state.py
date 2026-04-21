from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RoundRecord(BaseModel):
    round_number: int = Field(description="Builder/Critic round number")
    builder_output: str = Field(description="Builder output for this round")
    critic_output: str = Field(description="Critic output for this round")


class CircuitState(BaseModel):
    user_intent: str = Field(description="Original user design intent")
    host_organism: str = Field(
        default="Escherichia coli",
        description="Target host organism",
    )
    rag_context: str = Field(
        default="",
        description="Retrieved RAG context",
    )
    current_round: int = Field(default=0, description="Current iteration round")
    current_topology: Optional[str] = Field(
        default=None,
        description="Latest Builder design draft",
    )
    critic_feedbacks: List[str] = Field(
        default_factory=list,
        description="Accumulated Critic feedback history",
    )
    is_approved: bool = Field(
        default=False,
        description="Whether the design is approved",
    )
    formal_specification: Optional[str] = Field(
        default=None,
        description="Final consolidated specification",
    )
    simulation_results: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Simulation outputs",
    )
    seed_debate_transcript: Optional[str] = Field(
        default=None,
        description="Seed transcript used to bootstrap the first round",
    )
    round_history: List[RoundRecord] = Field(
        default_factory=list,
        description="Structured history for each round",
    )
    last_error: Optional[str] = Field(
        default=None,
        description="Last workflow error message",
    )

    @property
    def latest_critic_feedback(self) -> Optional[str]:
        if not self.critic_feedbacks:
            return None
        return self.critic_feedbacks[-1]
