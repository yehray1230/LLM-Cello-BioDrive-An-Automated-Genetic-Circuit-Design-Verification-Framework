from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Mapping


TASK_SCHEMA_VERSION = "egma-task-v1"
RESULT_SCHEMA_VERSION = "egma-result-v1"

SOURCE_FAMILIES = frozenset(
    {
        "procedural_boolean",
        "heldout_composition_or_part_symbol",
        "repair_or_invalid_input",
        "literature_anchored",
    }
)
INTENT_STATUSES = frozenset(
    {"feasible", "underspecified", "contradictory_or_infeasible"}
)
EXPECTED_RESPONSE_CLASSES = frozenset(
    {"design", "clarification", "unresolved_or_refusal"}
)
LANGUAGE_STRATA = frozenset(
    {
        "canonical_direct",
        "paraphrased_domain_varied",
        "noisy_incomplete_conflicting",
    }
)
SPLITS = frozenset({"development", "sealed_confirmatory"})
SYSTEM_IDS = frozenset({"S0", "S1", "S2", "S3"})

PROTECTED_EVIDENCE_FIELDS = frozenset(
    {
        "claim_audit",
        "constraint_failures",
        "evidence_completeness",
        "formal_validity",
        "ode_result",
        "provenance_gaps",
        "signal_overlap",
        "simulation_result",
        "topology_validation",
        "truth_table_mismatch",
        "unsupported_claims",
        "verifier_result",
    }
)
MODEL_VISIBLE_CHANNELS = (
    "prompt_messages",
    "tool_messages",
    "agent_state",
    "cache_payload",
    "ranking_feedback",
    "repair_feedback",
)


def project_model_visible_state(
    system_id: str,
    workflow_state: Mapping[str, Any],
) -> dict[str, Any]:
    """Project workflow state to the fields that a compared model may receive."""

    projected = deepcopy(dict(workflow_state))
    if system_id != "S2":
        return projected

    def strip_protected(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: strip_protected(item)
                for key, item in value.items()
                if key not in PROTECTED_EVIDENCE_FIELDS
            }
        if isinstance(value, list):
            return [strip_protected(item) for item in value]
        return value

    return strip_protected(projected)


def validate_evidence_ablation_bundle(bundle: Mapping[str, Any]) -> list[str]:
    """Fail closed when S2 model-visible channels contain verifier evidence."""

    errors: list[str] = []
    system_id = bundle.get("system_id")
    if system_id not in SYSTEM_IDS:
        return [f"Unknown system_id: {system_id!r}."]
    if system_id != "S2":
        return errors

    evidence_feedback = bundle.get("evidence_feedback")
    if evidence_feedback not in (None, {}, []):
        errors.append("S2 evidence_feedback must be empty.")

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                child_path = f"{path}.{key}" if path else key
                if key in PROTECTED_EVIDENCE_FIELDS:
                    errors.append(
                        f"S2 protected evidence field is model-visible: {child_path}."
                    )
                walk(item, child_path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    for channel in MODEL_VISIBLE_CHANNELS:
        walk(bundle.get(channel), channel)

    canaries = [str(value) for value in bundle.get("evidence_canaries", [])]
    visible_payload = {
        channel: bundle.get(channel) for channel in MODEL_VISIBLE_CHANNELS
    }
    serialized = json.dumps(visible_payload, sort_keys=True, ensure_ascii=False)
    for canary in canaries:
        if canary and canary in serialized:
            errors.append(f"S2 evidence canary leaked to a model-visible channel: {canary}.")
    return errors
