from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from benchmark_suite.egma_generator import (
    DEFAULT_DRY_RUN_SEED,
    DRY_RUN_CLAIM_STATUS,
    GROUP_LAYOUT,
    LANGUAGE_ORDER,
    SOURCE_ORDER,
    SPLIT_INPUT_COUNTS,
    SPLIT_LANGUAGE_COUNTS,
    SPLIT_ORDER,
    SPLIT_SOURCE_INTENT_COUNTS,
    generate_egma_dry_run_bundle,
    validate_egma_dry_run_bundle,
    write_egma_dry_run_bundle,
)


PROTOCOL_DIR = Path(__file__).parents[1] / "benchmark_suite" / "protocols"


def _input_count_by_task(bundle: dict) -> dict[str, int]:
    return {
        task_id: group["input_count"]
        for group in bundle["groups"]
        for task_id in group["task_ids"]
    }


def test_default_dry_run_is_valid_and_has_exact_global_allocations() -> None:
    bundle = generate_egma_dry_run_bundle()

    assert validate_egma_dry_run_bundle(bundle) == []
    assert len(bundle["tasks"]) == 150
    assert len(bundle["groups"]) == 46
    assert bundle["claim_status"] == DRY_RUN_CLAIM_STATUS
    assert bundle["confirmatory_eligible"] is False
    assert bundle["sealed_materialized"] is False

    tasks = bundle["tasks"]
    assert Counter(task["split"] for task in tasks) == {
        "development": 100,
        "sealed_confirmatory": 50,
    }
    assert Counter(task["source"]["family"] for task in tasks) == {
        "procedural_boolean": 90,
        "heldout_composition_or_part_symbol": 30,
        "repair_or_invalid_input": 15,
        "literature_anchored": 15,
    }
    assert Counter(task["intent_status"] for task in tasks) == {
        "feasible": 105,
        "underspecified": 30,
        "contradictory_or_infeasible": 15,
    }
    assert Counter(task["language"]["stratum"] for task in tasks) == {
        "canonical_direct": 50,
        "paraphrased_domain_varied": 50,
        "noisy_incomplete_conflicting": 50,
    }
    task_input_count = _input_count_by_task(bundle)
    assert Counter(task_input_count.values()) == {2: 20, 3: 130}


def test_split_joint_allocations_match_the_frozen_dry_run_plan() -> None:
    bundle = generate_egma_dry_run_bundle()
    tasks = bundle["tasks"]
    input_count_by_task = _input_count_by_task(bundle)

    for split in SPLIT_ORDER:
        split_tasks = [task for task in tasks if task["split"] == split]
        assert Counter(
            task["language"]["stratum"] for task in split_tasks
        ) == SPLIT_LANGUAGE_COUNTS[split]
        assert Counter(
            input_count_by_task[task["task_id"]] for task in split_tasks
        ) == SPLIT_INPUT_COUNTS[split]
        for source_index, source in enumerate(SOURCE_ORDER):
            expected = SPLIT_SOURCE_INTENT_COUNTS[split][source]
            source_tasks = [
                task for task in split_tasks if task["source"]["family"] == source
            ]
            actual = Counter(task["intent_status"] for task in source_tasks)
            assert tuple(
                actual[intent]
                for intent in (
                    "feasible",
                    "underspecified",
                    "contradictory_or_infeasible",
                )
            ) == expected, (split, source_index)


def test_generation_is_seeded_reproducible_and_seed_sensitive() -> None:
    first = generate_egma_dry_run_bundle(seed=DEFAULT_DRY_RUN_SEED)
    replay = generate_egma_dry_run_bundle(seed=DEFAULT_DRY_RUN_SEED)
    alternate = generate_egma_dry_run_bundle(seed=DEFAULT_DRY_RUN_SEED + 1)

    assert first == replay
    assert first["content_hash"] == replay["content_hash"]
    assert alternate["content_hash"] != first["content_hash"]
    assert validate_egma_dry_run_bundle(alternate) == []


def test_groups_are_split_local_function_unique_and_language_diverse() -> None:
    bundle = generate_egma_dry_run_bundle()
    task_by_id = {task["task_id"]: task for task in bundle["tasks"]}
    signatures_by_split: dict[str, set[str]] = {
        split: set() for split in SPLIT_ORDER
    }

    assert Counter(
        (group["split"], group["input_count"], len(group["task_ids"]))
        for group in bundle["groups"]
    ) == Counter(
        (split, input_count, group_size)
        for split in SPLIT_ORDER
        for input_count, group_size in GROUP_LAYOUT[split]
    )
    for group in bundle["groups"]:
        group_tasks = [task_by_id[task_id] for task_id in group["task_ids"]]
        assert {task["split"] for task in group_tasks} == {group["split"]}
        assert {
            task["leakage_group"] for task in group_tasks
        } == {group["leakage_group"]}
        assert len(
            {task["language"]["stratum"] for task in group_tasks}
        ) == min(len(group_tasks), len(LANGUAGE_ORDER))
        assert group["function_signature"] not in signatures_by_split[group["split"]]
        signatures_by_split[group["split"]].add(group["function_signature"])

    assert signatures_by_split["development"].isdisjoint(
        signatures_by_split["sealed_confirmatory"]
    )


def test_literature_rows_are_explicit_non_literature_placeholders() -> None:
    bundle = generate_egma_dry_run_bundle()
    literature_tasks = [
        task
        for task in bundle["tasks"]
        if task["source"]["family"] == "literature_anchored"
    ]

    assert len(literature_tasks) == 15
    assert bundle["provenance"]["literature_slots_are_placeholders"] is True
    assert bundle["provenance"]["public_data_alignment_inspected"] is False
    assert all(
        task["source"]["locator"].startswith(
            "fixture://egma-dry-run/literature-slot/"
        )
        for task in literature_tasks
    )


def test_validator_rejects_cross_split_group_assignment() -> None:
    bundle = generate_egma_dry_run_bundle()
    mutated = deepcopy(bundle)
    moved_task_id = mutated["groups"][0]["task_ids"][0]
    task = next(
        item for item in mutated["tasks"] if item["task_id"] == moved_task_id
    )
    task["split"] = "sealed_confirmatory"

    errors = validate_egma_dry_run_bundle(mutated)

    assert any("Group assignment mismatch" in error for error in errors)
    assert any("split_total" in error for error in errors)


def test_validator_rejects_duplicate_task_id_and_hash_drift() -> None:
    bundle = generate_egma_dry_run_bundle()
    mutated = deepcopy(bundle)
    mutated["tasks"][1]["task_id"] = mutated["tasks"][0]["task_id"]

    errors = validate_egma_dry_run_bundle(mutated)

    assert "Dry-run task IDs must be unique." in errors
    assert "Dry-run bundle content_hash does not reproduce." in errors


def test_writer_round_trips_a_valid_bundle(tmp_path: Path) -> None:
    output = tmp_path / "dry-run.json"

    expected = write_egma_dry_run_bundle(output)
    actual = json.loads(output.read_text(encoding="utf-8"))

    assert actual == expected
    assert validate_egma_dry_run_bundle(actual) == []


def test_dry_run_schema_is_versioned_and_preserves_nonconfirmatory_boundary() -> None:
    schema = json.loads(
        (PROTOCOL_DIR / "egma-benchmark-dry-run-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    task_schema = json.loads(
        (PROTOCOL_DIR / "egma-task-v1.schema.json").read_text(encoding="utf-8")
    )
    registry = Registry().with_resource(
        task_schema["$id"],
        Resource.from_contents(task_schema),
    )

    assert schema["properties"]["schema_version"]["const"] == (
        "egma-benchmark-dry-run-v1"
    )
    assert schema["properties"]["tasks"]["minItems"] == 150
    assert schema["properties"]["groups"]["minItems"] == 46
    assert schema["properties"]["confirmatory_eligible"]["const"] is False
    assert schema["properties"]["sealed_materialized"]["const"] is False
    Draft202012Validator(schema, registry=registry).validate(
        generate_egma_dry_run_bundle()
    )
