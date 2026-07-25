from __future__ import annotations

from dataclasses import asdict
import hashlib
import json

from benchmark_suite.dataset import BenchmarkCase, BenchmarkDataset
from benchmark_suite.design_task_dataset import DesignTask, DesignTaskSet
from benchmark_suite.scoring_profiles import ScoringProfile
from repositories.sqlite_repository import canonical_payload_hash as repository_hash
from schemas.run_manifest import payload_sha256
from schemas.simulation import canonical_payload_hash as simulation_hash
from utils.hashing import stable_json_sha256


def _compact_json_hash(payload: object, *, default: object | None = None) -> str:
    options = {
        "ensure_ascii": False,
        "sort_keys": True,
        "separators": (",", ":"),
    }
    if default is not None:
        options["default"] = default
    serialized = json.dumps(payload, **options)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _dataset(*, candidate: dict[str, object]) -> BenchmarkDataset:
    return BenchmarkDataset(
        dataset_id="contract-v1",
        version="1.0.0",
        name="穩定雜湊",
        description="Unicode contract",
        cases=[BenchmarkCase(case_id="case-1", name="Case", candidate=candidate)],
    )


def _task_set(*, expected: dict[str, object]) -> DesignTaskSet:
    return DesignTaskSet(
        task_set_id="contract-v1",
        version="1.0.0",
        name="穩定雜湊",
        description="Unicode contract",
        tasks=[
            DesignTask(
                task_id="task-1",
                category="reporter",
                name="Task",
                request="Produce GFP",
                expected=expected,
            )
        ],
    )


def test_dataset_content_hash_uses_full_compact_utf8_payload() -> None:
    dataset = _dataset(candidate={"z": 1, "訊號": "綠色"})

    assert dataset.content_hash == _compact_json_hash(asdict(dataset))


def test_dataset_and_task_hashes_ignore_mapping_insertion_order() -> None:
    first_dataset = _dataset(candidate={"a": 1, "b": 2})
    second_dataset = _dataset(candidate={"b": 2, "a": 1})
    first_tasks = _task_set(expected={"a": 1, "b": 2})
    second_tasks = _task_set(expected={"b": 2, "a": 1})

    assert first_dataset.content_hash == second_dataset.content_hash
    assert first_tasks.content_hash == second_tasks.content_hash


def test_non_finite_numbers_keep_python_json_tokens() -> None:
    dataset = _dataset(candidate={"nan": float("nan"), "infinity": float("inf")})
    serialized = json.dumps(
        asdict(dataset),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    assert '"infinity":Infinity' in serialized
    assert '"nan":NaN' in serialized
    assert dataset.content_hash == hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def test_scoring_profile_omits_absent_compatibility_field() -> None:
    profile = ScoringProfile(
        profile_id="legacy",
        version="1.0.0",
        name="Legacy",
        description="Compatibility profile",
        dimension_weights={"functional": 1.0},
        grade_thresholds={"A": 0.9},
    )
    payload = asdict(profile)
    payload.pop("biophysical_weights")

    assert profile.configuration_hash == _compact_json_hash(payload)


def test_scoring_profile_hashes_present_empty_compatibility_field() -> None:
    absent = ScoringProfile("p", "1", "P", "", {}, {}, None)
    present_empty = ScoringProfile("p", "1", "P", "", {}, {}, {})

    assert absent.configuration_hash != present_empty.configuration_hash
    assert present_empty.configuration_hash == _compact_json_hash(asdict(present_empty))


def test_default_str_hash_primitives_share_their_existing_contract() -> None:
    payload = {"label": "訊號", "value": complex(1, 2)}
    expected = _compact_json_hash(payload, default=str)

    assert payload_sha256(payload) == expected
    assert simulation_hash(payload) == expected


def test_repository_hash_reuses_repository_serialization_contract() -> None:
    first = {"z": "訊號", "a": 1}
    second = {"a": 1, "z": "訊號"}

    assert repository_hash(first) == repository_hash(second)
    assert repository_hash(first) == _compact_json_hash(first)


def test_shared_primitive_preserves_compact_utf8_contract() -> None:
    payload = {"z": [float("nan"), "綠色"], "a": {"b": 2, "a": 1}}

    assert stable_json_sha256(payload) == _compact_json_hash(payload)
