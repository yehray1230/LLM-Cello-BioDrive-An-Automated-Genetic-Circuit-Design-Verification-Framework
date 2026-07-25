from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from utils.hashing import stable_json_sha256


@dataclass(frozen=True)
class ScoringProfile:
    profile_id: str
    version: str
    name: str
    description: str
    dimension_weights: dict[str, float]
    grade_thresholds: dict[str, float]
    biophysical_weights: dict[str, float] | None = None

    @property
    def configuration_hash(self) -> str:
        configuration = asdict(self)
        if configuration["biophysical_weights"] is None:
            configuration.pop("biophysical_weights")
        return stable_json_sha256(configuration)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "configuration_hash": self.configuration_hash}


LEGACY_PROFILE = ScoringProfile(
    profile_id="legacy-weighted",
    version="1.0.0",
    name="Legacy weighted total",
    description="Preserves the pre-v1.8 weighted_total_score behavior.",
    dimension_weights={
        "functional": 0.22,
        "kinetic": 0.15,
        "static_plausibility": 0.08,
        "metabolic_burden": 0.15,
        "robustness": 0.15,
        "temporal": 0.05,
        "orthogonality": 0.10,
        "cello_assignment": 0.10,
    },
    grade_thresholds={"excellent": 0.80, "pass": 0.60},
)


RESEARCH_PROFILE = ScoringProfile(
    profile_id="research-v1.8",
    version="1.8.0",
    name="Research evaluation",
    description=(
        "Evidence-aware multidimensional evaluation for reproducible "
        "candidate comparison. Scores remain computational screening signals."
    ),
    dimension_weights={
        "logic_function": 0.15,
        "dynamic_behavior": 0.15,
        "robustness": 0.15,
        "resource_burden": 0.10,
        "buildability": 0.15,
        "evidence_quality": 0.10,
        "data_completeness": 0.10,
        "semantic_faithfulness": 0.10,
    },
    grade_thresholds={"excellent": 0.80, "pass": 0.60},
)

SIMULATION_RESEARCH_PROFILE = ScoringProfile(
    profile_id="research-v2-preview",
    version="1.9.0",
    name="Research evaluation with versioned simulation",
    description=(
        "Preview profile tied to the v1.9 resource-aware ODE contract. "
        "Simulation model and configuration hashes must accompany comparisons."
    ),
    dimension_weights=dict(RESEARCH_PROFILE.dimension_weights),
    grade_thresholds=dict(RESEARCH_PROFILE.grade_thresholds),
    biophysical_weights={
        "logic": 0.40,
        "noise_resilience": 0.15,
        "retroactivity_resilience": 0.15,
        "rbs_accessibility": 0.15,
        "resource_burden": 0.15,
    },
)


SCORING_PROFILES = {
    LEGACY_PROFILE.profile_id: LEGACY_PROFILE,
    RESEARCH_PROFILE.profile_id: RESEARCH_PROFILE,
    SIMULATION_RESEARCH_PROFILE.profile_id: SIMULATION_RESEARCH_PROFILE,
}


def get_scoring_profile(profile_id: str | None) -> ScoringProfile:
    selected = str(profile_id or LEGACY_PROFILE.profile_id)
    try:
        return SCORING_PROFILES[selected]
    except KeyError as exc:
        raise ValueError(
            f"Unknown scoring profile: {selected}. Available profiles: "
            f"{', '.join(sorted(SCORING_PROFILES))}."
        ) from exc


def list_scoring_profiles() -> list[dict[str, Any]]:
    return [
        SCORING_PROFILES[key].to_dict()
        for key in sorted(SCORING_PROFILES)
    ]
