from __future__ import annotations

from dataclasses import dataclass
from itertools import product
import json
from pathlib import Path
import random
from typing import Any, Iterable, Mapping

from benchmark_suite.egma_boolean import (
    canonical_expression,
    canonical_truth_table,
    parse_boolean_expression,
)
from benchmark_suite.egma_contracts import (
    INTENT_STATUSES,
    LANGUAGE_STRATA,
    SOURCE_FAMILIES,
)
from benchmark_suite.egma_topology import TOPOLOGY_INVARIANTS
from benchmark_suite.egma_validation import validate_egma_task
from utils.hashing import stable_json_sha256


GENERATOR_VERSION = "egma-dry-run-generator-v1"
BUNDLE_SCHEMA_VERSION = "egma-benchmark-dry-run-v1"
BENCHMARK_ID = "egma_ecoli_transcriptional_logic_dry_run_v1"
DEFAULT_DRY_RUN_SEED = 20260725
DRY_RUN_CLAIM_STATUS = "offline_fixture_only_not_confirmatory"

SOURCE_ORDER = (
    "procedural_boolean",
    "heldout_composition_or_part_symbol",
    "repair_or_invalid_input",
    "literature_anchored",
)
INTENT_ORDER = (
    "feasible",
    "underspecified",
    "contradictory_or_infeasible",
)
LANGUAGE_ORDER = (
    "canonical_direct",
    "paraphrased_domain_varied",
    "noisy_incomplete_conflicting",
)
SPLIT_ORDER = ("development", "sealed_confirmatory")

SPLIT_SOURCE_INTENT_COUNTS = {
    "development": {
        "procedural_boolean": (42, 12, 6),
        "heldout_composition_or_part_symbol": (14, 4, 2),
        "repair_or_invalid_input": (7, 2, 1),
        "literature_anchored": (7, 2, 1),
    },
    "sealed_confirmatory": {
        "procedural_boolean": (22, 6, 2),
        "heldout_composition_or_part_symbol": (6, 2, 2),
        "repair_or_invalid_input": (4, 1, 0),
        "literature_anchored": (3, 1, 1),
    },
}
SPLIT_LANGUAGE_COUNTS = {
    "development": {
        "canonical_direct": 34,
        "paraphrased_domain_varied": 33,
        "noisy_incomplete_conflicting": 33,
    },
    "sealed_confirmatory": {
        "canonical_direct": 16,
        "paraphrased_domain_varied": 17,
        "noisy_incomplete_conflicting": 17,
    },
}
SPLIT_INPUT_COUNTS = {
    "development": {2: 12, 3: 88},
    "sealed_confirmatory": {2: 8, 3: 42},
}
GROUP_LAYOUT = {
    "development": [(2, 2)] * 6 + [(3, 4)] * 22,
    "sealed_confirmatory": [(2, 2)] * 4 + [(3, 3)] * 14,
}


@dataclass(frozen=True)
class AllocationSlot:
    split: str
    source_family: str
    intent_status: str
    language_stratum: str


def generate_egma_dry_run_bundle(
    *,
    seed: int = DEFAULT_DRY_RUN_SEED,
) -> dict[str, Any]:
    """Generate a fully visible fixture bundle, never a confirmatory holdout."""

    rng = random.Random(seed)
    function_pools = _function_pools(rng)
    tasks: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    function_cursor = {2: 0, 3: 0}

    for split in SPLIT_ORDER:
        slots = _allocation_slots(split, rng)
        group_specs = list(GROUP_LAYOUT[split])
        rng.shuffle(group_specs)
        grouped_slots = _group_slots_by_language(slots, group_specs, rng)
        for group_index, ((input_count, _), member_slots) in enumerate(
            zip(group_specs, grouped_slots, strict=True)
        ):
            signature, expression = function_pools[input_count][
                function_cursor[input_count]
            ]
            function_cursor[input_count] += 1
            group_id = f"{split[:3]}-g{group_index:03d}"
            task_ids: list[str] = []
            for variant_index, slot in enumerate(member_slots):
                task = _materialize_task(
                    slot,
                    group_id=group_id,
                    variant_index=variant_index,
                    input_count=input_count,
                    expression=expression,
                    seed=seed,
                )
                tasks.append(task)
                task_ids.append(task["task_id"])
            groups.append(
                {
                    "leakage_group": group_id,
                    "split": split,
                    "input_count": input_count,
                    "function_signature": signature,
                    "template_id": f"template:{group_id}",
                    "task_ids": task_ids,
                }
            )

    payload = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "claim_status": DRY_RUN_CLAIM_STATUS,
        "confirmatory_eligible": False,
        "sealed_materialized": False,
        "description": (
            "Visible deterministic fixture for generator, allocation, and leakage "
            "validation. It is not the future confirmatory benchmark."
        ),
        "tasks": tasks,
        "groups": groups,
        "allocation_targets": _allocation_targets(),
        "provenance": {
            "source_status": "generated_fixture_slots",
            "literature_slots_are_placeholders": True,
            "public_data_alignment_inspected": False,
            "provider_calls": 0,
            "paid_cost_usd": 0.0,
        },
    }
    payload["content_hash"] = stable_json_sha256(payload)
    return payload


def validate_egma_dry_run_bundle(bundle: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if bundle.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        errors.append("Dry-run bundle schema_version is invalid.")
    if bundle.get("benchmark_id") != BENCHMARK_ID:
        errors.append("Dry-run bundle benchmark_id is invalid.")
    if bundle.get("generator_version") != GENERATOR_VERSION:
        errors.append("Dry-run bundle generator_version is invalid.")
    if bundle.get("claim_status") != DRY_RUN_CLAIM_STATUS:
        errors.append("Dry-run bundle claim boundary is invalid.")
    if bundle.get("confirmatory_eligible") is not False:
        errors.append("Dry-run bundle must be confirmatory_eligible=false.")
    if bundle.get("sealed_materialized") is not False:
        errors.append("Dry-run bundle must be sealed_materialized=false.")

    tasks = bundle.get("tasks")
    groups = bundle.get("groups")
    if not isinstance(tasks, list):
        return errors + ["Dry-run bundle tasks must be an array."]
    if not isinstance(groups, list):
        return errors + ["Dry-run bundle groups must be an array."]
    if len(tasks) != 150:
        errors.append("Dry-run bundle must contain exactly 150 tasks.")
    if any(not isinstance(task, Mapping) for task in tasks):
        return errors + ["Every dry-run task must be an object."]
    if any(not isinstance(group, Mapping) for group in groups):
        return errors + ["Every dry-run group must be an object."]

    task_ids = [str(task.get("task_id") or "") for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        errors.append("Dry-run task IDs must be unique.")
    for task in tasks:
        task_errors = validate_egma_task(task)
        errors.extend(
            f"Task {task.get('task_id')}: {error}" for error in task_errors
        )

    errors.extend(_validate_counts(tasks, groups))
    errors.extend(_validate_groups(tasks, groups))

    expected_hash = bundle.get("content_hash")
    hash_payload = dict(bundle)
    hash_payload.pop("content_hash", None)
    if expected_hash != stable_json_sha256(hash_payload):
        errors.append("Dry-run bundle content_hash does not reproduce.")
    return errors


def write_egma_dry_run_bundle(
    path: str | Path,
    *,
    seed: int = DEFAULT_DRY_RUN_SEED,
) -> dict[str, Any]:
    bundle = generate_egma_dry_run_bundle(seed=seed)
    errors = validate_egma_dry_run_bundle(bundle)
    if errors:
        raise ValueError("Invalid generated EGMA dry run: " + " ".join(errors))
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return bundle


def _allocation_slots(split: str, rng: random.Random) -> list[AllocationSlot]:
    cell_counts = SPLIT_SOURCE_INTENT_COUNTS[split]
    language_remaining = dict(SPLIT_LANGUAGE_COUNTS[split])
    cells: list[tuple[str, str, int, list[str]]] = []
    for source_family in SOURCE_ORDER:
        counts = cell_counts[source_family]
        for intent_status, count in zip(INTENT_ORDER, counts, strict=True):
            cells.append((source_family, intent_status, count, []))

    for cell_index, (_, _, count, assigned) in enumerate(cells):
        if count < len(LANGUAGE_ORDER):
            continue
        rotated = list(LANGUAGE_ORDER[cell_index % 3 :]) + list(
            LANGUAGE_ORDER[: cell_index % 3]
        )
        for language in rotated:
            assigned.append(language)
            language_remaining[language] -= 1

    for cell_index, (_, _, count, assigned) in enumerate(cells):
        while len(assigned) < count:
            available = [
                language
                for language in LANGUAGE_ORDER
                if language_remaining[language] > 0
            ]
            if not available:
                raise ValueError("Language allocation exhausted early.")
            tie_order = {
                language: (LANGUAGE_ORDER.index(language) - cell_index) % 3
                for language in LANGUAGE_ORDER
            }
            selected = min(
                available,
                key=lambda language: (
                    assigned.count(language),
                    -language_remaining[language],
                    tie_order[language],
                ),
            )
            assigned.append(selected)
            language_remaining[selected] -= 1
    if any(language_remaining.values()):
        raise ValueError(f"Language allocation did not reconcile: {language_remaining}")

    slots = [
        AllocationSlot(split, source_family, intent_status, language)
        for source_family, intent_status, _, assigned in cells
        for language in assigned
    ]
    rng.shuffle(slots)
    return slots


def _group_slots_by_language(
    slots: list[AllocationSlot],
    group_specs: list[tuple[int, int]],
    rng: random.Random,
) -> list[list[AllocationSlot]]:
    pools = {
        language: [slot for slot in slots if slot.language_stratum == language]
        for language in LANGUAGE_ORDER
    }
    for pool in pools.values():
        rng.shuffle(pool)
    grouped: list[list[AllocationSlot]] = []
    for _, size in group_specs:
        members: list[AllocationSlot] = []
        desired_distinct = min(size, len(LANGUAGE_ORDER))
        for _ in range(desired_distinct):
            available = [
                language
                for language in LANGUAGE_ORDER
                if pools[language]
                and language not in {
                    member.language_stratum for member in members
                }
            ]
            selected = max(available, key=lambda language: len(pools[language]))
            members.append(pools[selected].pop())
        while len(members) < size:
            selected = max(LANGUAGE_ORDER, key=lambda language: len(pools[language]))
            if not pools[selected]:
                raise ValueError("Group allocation exhausted a language pool.")
            members.append(pools[selected].pop())
        grouped.append(members)
    if any(pools[language] for language in LANGUAGE_ORDER):
        raise ValueError("Group allocation left unassigned slots.")
    return grouped


def _function_pools(
    rng: random.Random,
) -> dict[int, list[tuple[str, str]]]:
    pools: dict[int, list[tuple[str, str]]] = {}
    for input_count in (2, 3):
        inputs = [chr(ord("A") + index) for index in range(input_count)]
        functions: list[tuple[str, str]] = []
        row_count = 2**input_count
        for encoded in range(1, 2**row_count - 1):
            bits = tuple(
                (encoded >> (row_count - index - 1)) & 1
                for index in range(row_count)
            )
            if not _depends_on_all_inputs(bits, input_count):
                continue
            signature = f"{input_count}:{''.join(str(bit) for bit in bits)}"
            functions.append((signature, _dnf_expression(bits, inputs)))
        rng.shuffle(functions)
        pools[input_count] = functions
    required = {
        input_count: sum(
            1
            for split in SPLIT_ORDER
            for group_input_count, _ in GROUP_LAYOUT[split]
            if group_input_count == input_count
        )
        for input_count in (2, 3)
    }
    for input_count, count in required.items():
        if len(pools[input_count]) < count:
            raise ValueError(f"Insufficient {input_count}-input Boolean functions.")
    return pools


def _depends_on_all_inputs(bits: tuple[int, ...], input_count: int) -> bool:
    assignments = list(product((0, 1), repeat=input_count))
    outputs = dict(zip(assignments, bits, strict=True))
    for variable_index in range(input_count):
        if not any(
            outputs[assignment]
            != outputs[
                tuple(
                    1 - bit if index == variable_index else bit
                    for index, bit in enumerate(assignment)
                )
            ]
            for assignment in assignments
            if assignment[variable_index] == 0
        ):
            return False
    return True


def _dnf_expression(bits: tuple[int, ...], inputs: list[str]) -> str:
    terms: list[str] = []
    for assignment, output in zip(
        product((0, 1), repeat=len(inputs)),
        bits,
        strict=True,
    ):
        if output != 1:
            continue
        literals = [
            symbol if bit else f"NOT {symbol}"
            for symbol, bit in zip(inputs, assignment, strict=True)
        ]
        terms.append("(" + " AND ".join(literals) + ")")
    return " OR ".join(terms)


def _materialize_task(
    slot: AllocationSlot,
    *,
    group_id: str,
    variant_index: int,
    input_count: int,
    expression: str,
    seed: int,
) -> dict[str, Any]:
    inputs = [chr(ord("A") + index) for index in range(input_count)]
    output = "Y"
    canonical = canonical_expression(parse_boolean_expression(expression))
    task_id = f"{group_id}-v{variant_index:02d}"
    expected_class = {
        "feasible": "design",
        "underspecified": "clarification",
        "contradictory_or_infeasible": "unresolved_or_refusal",
    }[slot.intent_status]
    formal_spec = None
    if slot.intent_status == "feasible":
        formal_spec = {
            "input_symbols": inputs,
            "output_symbol": output,
            "boolean_expression": expression,
            "canonical_expression": canonical,
            "allowed_operators": ["NOT", "AND", "OR"],
            "truth_table": canonical_truth_table(expression, inputs, output),
            "topology_invariants": list(TOPOLOGY_INVARIANTS),
        }
    source = _source_record(slot.source_family, group_id)
    return {
        "schema_version": "egma-task-v1",
        "task_id": task_id,
        "request": _request_text(
            slot,
            inputs=inputs,
            output=output,
            expression=expression,
            variant_index=variant_index,
        ),
        "source": source,
        "intent_status": slot.intent_status,
        "expected_response_class": expected_class,
        "language": {
            "stratum": slot.language_stratum,
            "paraphrase_parent_id": (
                None
                if slot.language_stratum == "canonical_direct"
                else f"parent:{group_id}"
            ),
        },
        "formal_spec": formal_spec,
        "split": slot.split,
        "leakage_group": group_id,
        "generation": {
            "generator_version": GENERATOR_VERSION,
            "seed": seed,
        },
    }


def _source_record(source_family: str, group_id: str) -> dict[str, Any]:
    locator_prefix = {
        "procedural_boolean": "generator://egma-dry-run",
        "heldout_composition_or_part_symbol": "fixture://egma-dry-run/part-family",
        "repair_or_invalid_input": "fixture://egma-dry-run/invalid",
        "literature_anchored": "fixture://egma-dry-run/literature-slot",
    }[source_family]
    source: dict[str, Any] = {
        "family": source_family,
        "locator": f"{locator_prefix}/{group_id}",
        "license_status": "generated",
        "lineage_id": group_id,
    }
    if source_family == "literature_anchored":
        source["exact_location"] = "dry-run-placeholder-not-a-literature-claim"
    return source


def _request_text(
    slot: AllocationSlot,
    *,
    inputs: list[str],
    output: str,
    expression: str,
    variant_index: int,
) -> str:
    input_text = ", ".join(inputs)
    if slot.intent_status == "underspecified":
        base = {
            "canonical_direct": (
                f"Design an E. coli transcriptional reporter controlled by "
                f"{input_text}; the required ON condition is not specified."
            ),
            "paraphrased_domain_varied": (
                f"Use {input_text} to regulate {output} in E. coli, but determine "
                "the desired response pattern."
            ),
            "noisy_incomplete_conflicting": (
                f"ecoli reporter with {input_text} pls; output behavior TBD."
            ),
        }[slot.language_stratum]
        return f"{base} Variant {variant_index + 1}."
    if slot.intent_status == "contradictory_or_infeasible":
        assignment = ", ".join(f"{symbol}=0" for symbol in inputs)
        base = {
            "canonical_direct": (
                f"For the same input assignment ({assignment}), require {output} "
                "to be both 0 and 1."
            ),
            "paraphrased_domain_varied": (
                f"Build an E. coli reporter where ({assignment}) simultaneously "
                f"turns {output} OFF and ON."
            ),
            "noisy_incomplete_conflicting": (
                f"{assignment} -> {output}=0 and {output}=1 at once, make it work."
            ),
        }[slot.language_stratum]
        return f"{base} Variant {variant_index + 1}."
    base = {
        "canonical_direct": (
            f"Design an E. coli transcriptional logic circuit with inputs "
            f"{input_text} and output {output} implementing {expression}."
        ),
        "paraphrased_domain_varied": (
            f"In E. coli, make reporter {output} follow {expression}; the input "
            f"signals are {input_text}."
        ),
        "noisy_incomplete_conflicting": (
            f"ecoli logic pls: {output} should act like {expression}; inputs "
            f"{input_text}."
        ),
    }[slot.language_stratum]
    if slot.source_family == "literature_anchored":
        base = "Dry-run literature allocation slot only. " + base
    return f"{base} Variant {variant_index + 1}."


def _allocation_targets() -> dict[str, Any]:
    return {
        "source_family_total": {
            "procedural_boolean": 90,
            "heldout_composition_or_part_symbol": 30,
            "repair_or_invalid_input": 15,
            "literature_anchored": 15,
        },
        "intent_status_total": {
            "feasible": 105,
            "underspecified": 30,
            "contradictory_or_infeasible": 15,
        },
        "language_stratum_total": {
            "canonical_direct": 50,
            "paraphrased_domain_varied": 50,
            "noisy_incomplete_conflicting": 50,
        },
        "split_total": {"development": 100, "sealed_confirmatory": 50},
        "input_count_total": {"2": 20, "3": 130},
    }


def _validate_counts(
    tasks: list[Mapping[str, Any]],
    groups: list[Mapping[str, Any]],
) -> list[str]:
    errors: list[str] = []
    targets = _allocation_targets()
    input_count_by_task_id = {
        str(task_id): int(group.get("input_count") or 0)
        for group in groups
        for task_id in group.get("task_ids") or []
    }
    dimensions = {
        "source_family_total": lambda task: task.get("source", {}).get("family"),
        "intent_status_total": lambda task: task.get("intent_status"),
        "language_stratum_total": lambda task: task.get("language", {}).get(
            "stratum"
        ),
        "split_total": lambda task: task.get("split"),
        "input_count_total": lambda task: str(
            len(task.get("formal_spec", {}).get("input_symbols", []))
            if isinstance(task.get("formal_spec"), Mapping)
            else input_count_by_task_id.get(str(task.get("task_id") or ""), 0)
        ),
    }
    for dimension, getter in dimensions.items():
        actual = {
            value: sum(getter(task) == value for task in tasks)
            for value in targets[dimension]
        }
        if actual != targets[dimension]:
            errors.append(
                f"Allocation mismatch for {dimension}: "
                f"expected {targets[dimension]}, got {actual}."
            )

    for split in SPLIT_ORDER:
        split_tasks = [task for task in tasks if task.get("split") == split]
        expected_source = {
            source: sum(SPLIT_SOURCE_INTENT_COUNTS[split][source])
            for source in SOURCE_ORDER
        }
        actual_source = {
            source: sum(
                task.get("source", {}).get("family") == source
                for task in split_tasks
            )
            for source in SOURCE_ORDER
        }
        if actual_source != expected_source:
            errors.append(f"{split} source allocation does not match.")
        actual_language = {
            language: sum(
                task.get("language", {}).get("stratum") == language
                for task in split_tasks
            )
            for language in LANGUAGE_ORDER
        }
        if actual_language != SPLIT_LANGUAGE_COUNTS[split]:
            errors.append(f"{split} language allocation does not match.")
    return errors
def _validate_groups(
    tasks: list[Mapping[str, Any]],
    groups: list[Mapping[str, Any]],
) -> list[str]:
    errors: list[str] = []
    task_by_id = {str(task.get("task_id")): task for task in tasks}
    group_ids = [str(group.get("leakage_group") or "") for group in groups]
    if len(group_ids) != len(set(group_ids)):
        errors.append("Leakage group IDs must be unique.")
    assigned_task_ids: list[str] = []
    signature_splits: dict[str, set[str]] = {}
    lineage_splits: dict[str, set[str]] = {}
    for group in groups:
        group_id = str(group.get("leakage_group") or "")
        split = str(group.get("split") or "")
        task_ids = [str(value) for value in group.get("task_ids") or []]
        assigned_task_ids.extend(task_ids)
        for task_id in task_ids:
            task = task_by_id.get(task_id)
            if task is None:
                errors.append(f"Group {group_id} references unknown task {task_id}.")
                continue
            if task.get("split") != split or task.get("leakage_group") != group_id:
                errors.append(f"Group assignment mismatch for task {task_id}.")
            lineage = str(task.get("source", {}).get("lineage_id") or "")
            lineage_splits.setdefault(lineage, set()).add(split)
        signature = str(group.get("function_signature") or "")
        signature_splits.setdefault(signature, set()).add(split)
        expected_size = 2 if group.get("input_count") == 2 else (
            4 if split == "development" else 3
        )
        if len(task_ids) != expected_size:
            errors.append(f"Group {group_id} has the wrong task count.")
        group_languages = {
            task_by_id[task_id].get("language", {}).get("stratum")
            for task_id in task_ids
            if task_id in task_by_id
        }
        if len(group_languages) != min(expected_size, 3):
            errors.append(f"Group {group_id} lacks language diversity.")
        for task_id in task_ids:
            task = task_by_id.get(task_id)
            if not task or not isinstance(task.get("formal_spec"), Mapping):
                continue
            formal = task["formal_spec"]
            actual_signature = _truth_signature(formal["truth_table"])
            if actual_signature != signature:
                errors.append(f"Task {task_id} function signature drifted.")
    if sorted(assigned_task_ids) != sorted(task_by_id):
        errors.append("Group manifest does not cover each task exactly once.")
    if any(len(splits) != 1 for splits in signature_splits.values()):
        errors.append("Canonical Boolean function leakage crosses splits.")
    if any(len(splits) != 1 for splits in lineage_splits.values()):
        errors.append("Source lineage leakage crosses splits.")
    return errors


def _truth_signature(truth_table: Iterable[Mapping[str, Any]]) -> str:
    rows = list(truth_table)
    if not rows:
        return ""
    output_key = "Y"
    input_count = len(rows[0]) - 1
    return f"{input_count}:{''.join(str(row[output_key]) for row in rows)}"


def assert_generator_vocabulary() -> None:
    if frozenset(SOURCE_ORDER) != SOURCE_FAMILIES:
        raise RuntimeError("Generator source vocabulary drifted.")
    if frozenset(INTENT_ORDER) != INTENT_STATUSES:
        raise RuntimeError("Generator intent vocabulary drifted.")
    if frozenset(LANGUAGE_ORDER) != LANGUAGE_STRATA:
        raise RuntimeError("Generator language vocabulary drifted.")


assert_generator_vocabulary()
