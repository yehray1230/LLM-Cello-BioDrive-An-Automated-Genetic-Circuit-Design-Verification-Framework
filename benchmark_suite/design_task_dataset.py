from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
from typing import Any

from utils.hashing import stable_json_sha256


DESIGN_TASK_SCHEMA_VERSION = "1.0"
TASK_SET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
DEFAULT_TASK_SET_DIR = Path(__file__).resolve().parent / "task_sets"
CANONICAL_EXP003_CATEGORIES = frozenset(
    {"reporter", "toggle", "oscillator", "cello_logic", "ambiguous"}
)


@dataclass
class DesignTask:
    task_id: str
    category: str
    name: str
    request: str
    expected: dict[str, Any]
    constraints: dict[str, Any] = field(default_factory=dict)
    scoring_notes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)


@dataclass
class DesignTaskSet:
    task_set_id: str
    version: str
    name: str
    description: str
    tasks: list[DesignTask]
    schema_version: str = DESIGN_TASK_SCHEMA_VERSION
    license: str = "project-fixture"
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return stable_json_sha256(asdict(self))

    def task(self, task_id: str) -> DesignTask:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(task_id)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["content_hash"] = self.content_hash
        return payload


def load_design_task_set(
    task_set_id: str,
    *,
    task_set_dir: str | Path | None = None,
) -> DesignTaskSet:
    if not TASK_SET_ID_PATTERN.fullmatch(str(task_set_id or "")):
        raise ValueError("Invalid design task-set ID.")
    base = Path(task_set_dir) if task_set_dir else DEFAULT_TASK_SET_DIR
    path = base / f"{task_set_id}.json"
    if not path.is_file():
        raise KeyError(task_set_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Design task set must contain one JSON object.")
    task_set = DesignTaskSet(
        task_set_id=str(payload.get("task_set_id") or ""),
        version=str(payload.get("version") or ""),
        name=str(payload.get("name") or ""),
        description=str(payload.get("description") or ""),
        schema_version=str(
            payload.get("schema_version") or DESIGN_TASK_SCHEMA_VERSION
        ),
        license=str(payload.get("license") or "project-fixture"),
        provenance=dict(payload.get("provenance") or {}),
        tasks=[
            DesignTask(
                task_id=str(item.get("task_id") or ""),
                category=str(item.get("category") or ""),
                name=str(item.get("name") or ""),
                request=str(item.get("request") or ""),
                expected=dict(item.get("expected") or {}),
                constraints=dict(item.get("constraints") or {}),
                scoring_notes=[str(value) for value in item.get("scoring_notes", [])],
                tags=[str(value) for value in item.get("tags", [])],
                source=dict(item.get("source") or {}),
            )
            for item in payload.get("tasks", [])
            if isinstance(item, dict)
        ],
    )
    errors = validate_design_task_set(task_set)
    if errors:
        raise ValueError("Invalid design task set: " + " ".join(errors))
    return task_set


def validate_design_task_set(task_set: DesignTaskSet) -> list[str]:
    errors: list[str] = []
    if task_set.schema_version != DESIGN_TASK_SCHEMA_VERSION:
        errors.append(
            f"Unsupported design task schema version: {task_set.schema_version}."
        )
    if not TASK_SET_ID_PATTERN.fullmatch(task_set.task_set_id):
        errors.append("task_set_id is invalid.")
    if not task_set.version:
        errors.append("version is required.")
    if not task_set.tasks:
        errors.append("At least one design task is required.")
    task_ids = [task.task_id for task in task_set.tasks]
    duplicates = sorted(
        {task_id for task_id in task_ids if task_ids.count(task_id) > 1}
    )
    if duplicates:
        errors.append(f"Duplicate task IDs: {', '.join(duplicates)}.")
    for task in task_set.tasks:
        label = task.task_id or "<missing>"
        if not TASK_SET_ID_PATTERN.fullmatch(task.task_id):
            errors.append(f"Task {label} has an invalid task_id.")
        if not task.name.strip():
            errors.append(f"Task {label} requires name.")
        if not task.request.strip():
            errors.append(f"Task {label} requires a natural-language request.")
        if not task.category.strip():
            errors.append(f"Task {label} requires category.")
        if not task.expected:
            errors.append(f"Task {label} requires expected behavior.")
    return errors


def validate_exp003_task_set(task_set: DesignTaskSet) -> list[str]:
    errors = validate_design_task_set(task_set)
    categories = [task.category for task in task_set.tasks]
    if len(task_set.tasks) != 5:
        errors.append("EXP-003 requires exactly five canonical tasks.")
    missing = sorted(CANONICAL_EXP003_CATEGORIES - set(categories))
    unexpected = sorted(set(categories) - CANONICAL_EXP003_CATEGORIES)
    duplicates = sorted(
        {category for category in categories if categories.count(category) > 1}
    )
    if missing:
        errors.append(f"Missing EXP-003 categories: {', '.join(missing)}.")
    if unexpected:
        errors.append(f"Unexpected EXP-003 categories: {', '.join(unexpected)}.")
    if duplicates:
        errors.append(f"Duplicate EXP-003 categories: {', '.join(duplicates)}.")
    return errors
