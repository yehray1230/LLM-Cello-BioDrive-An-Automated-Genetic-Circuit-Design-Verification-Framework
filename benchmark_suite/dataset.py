from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
from typing import Any

from utils.hashing import stable_json_sha256



DATASET_SCHEMA_VERSION = "1.0"
DATASET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
DEFAULT_DATASET_DIR = Path(__file__).resolve().parent / "datasets"


@dataclass
class BenchmarkCase:
    case_id: str
    name: str
    candidate: dict[str, Any]
    expected: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    notes: str = ""


@dataclass
class BenchmarkDataset:
    dataset_id: str
    version: str
    name: str
    description: str
    cases: list[BenchmarkCase]
    schema_version: str = DATASET_SCHEMA_VERSION
    license: str = "project-fixture"
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return stable_json_sha256(asdict(self))

    def to_dict(self, *, include_cases: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        if not include_cases:
            payload.pop("cases", None)
            payload["case_count"] = len(self.cases)
        payload["content_hash"] = self.content_hash
        return payload


def load_benchmark_dataset(
    dataset_id: str,
    *,
    dataset_dir: str | Path | None = None,
) -> BenchmarkDataset:
    if not DATASET_ID_PATTERN.fullmatch(str(dataset_id or "")):
        raise ValueError("Invalid benchmark dataset ID.")
    base = Path(dataset_dir) if dataset_dir else DEFAULT_DATASET_DIR
    path = base / f"{dataset_id}.json"
    if not path.is_file():
        raise KeyError(dataset_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Benchmark dataset must contain one JSON object.")
    dataset = BenchmarkDataset(
        dataset_id=str(payload.get("dataset_id") or ""),
        version=str(payload.get("version") or ""),
        name=str(payload.get("name") or ""),
        description=str(payload.get("description") or ""),
        schema_version=str(
            payload.get("schema_version") or DATASET_SCHEMA_VERSION
        ),
        license=str(payload.get("license") or "project-fixture"),
        provenance=dict(payload.get("provenance") or {}),
        cases=[
            BenchmarkCase(
                case_id=str(item.get("case_id") or ""),
                name=str(item.get("name") or ""),
                candidate=dict(item.get("candidate") or {}),
                expected=dict(item.get("expected") or {}),
                tags=list(item.get("tags") or []),
                source=dict(item.get("source") or {}),
                notes=str(item.get("notes") or ""),
            )
            for item in payload.get("cases", [])
            if isinstance(item, dict)
        ],
    )
    errors = validate_benchmark_dataset(dataset)
    if errors:
        raise ValueError("Invalid benchmark dataset: " + " ".join(errors))
    return dataset


def list_benchmark_datasets(
    *,
    dataset_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    base = Path(dataset_dir) if dataset_dir else DEFAULT_DATASET_DIR
    if not base.exists():
        return []
    datasets = []
    for path in sorted(base.glob("*.json")):
        dataset = load_benchmark_dataset(path.stem, dataset_dir=base)
        datasets.append(dataset.to_dict(include_cases=False))
    return datasets


VALID_EVIDENCE_STATUSES = {
    "literature_curated_fixture",
    "experiment_validated",
    "synthetic_fixture",
    "implemented_preview",
}
VALID_SPOT_REVIEW_STATUSES = {
    "pending_independent_review",
    "verified",
}


def validate_benchmark_dataset(dataset: BenchmarkDataset) -> list[str]:
    errors: list[str] = []
    if dataset.schema_version != DATASET_SCHEMA_VERSION:
        errors.append(
            f"Unsupported dataset schema version: {dataset.schema_version}."
        )
    if not DATASET_ID_PATTERN.fullmatch(dataset.dataset_id):
        errors.append("dataset_id is invalid.")
    if not dataset.version:
        errors.append("version is required.")
    if not dataset.cases:
        errors.append("At least one benchmark case is required.")

    evidence_status = dataset.provenance.get("evidence_status")
    if evidence_status and evidence_status not in VALID_EVIDENCE_STATUSES:
        errors.append(f"Invalid evidence_status: '{evidence_status}'. Must be one of {sorted(VALID_EVIDENCE_STATUSES)}.")

    case_ids = [case.case_id for case in dataset.cases]
    duplicates = sorted(
        {case_id for case_id in case_ids if case_ids.count(case_id) > 1}
    )
    if duplicates:
        errors.append(f"Duplicate case IDs: {', '.join(duplicates)}.")
    for case in dataset.cases:
        if not case.case_id:
            errors.append("Each benchmark case requires case_id.")
        if not case.candidate:
            errors.append(f"Case {case.case_id} requires candidate data.")
        if evidence_status == "literature_curated_fixture":
            if not case.source:
                errors.append(f"Case {case.case_id} missing record-level literature source provenance.")
            elif not (case.source.get("doi") or case.source.get("reference")):
                errors.append(f"Case {case.case_id} missing DOI or reference in source provenance.")
            for field_name in (
                "exact_location",
                "measurement_context",
                "parameter_derivation",
                "data_rights",
                "spot_review_status",
            ):
                if not case.source.get(field_name):
                    errors.append(
                        f"Case {case.case_id} missing literature provenance field: {field_name}."
                    )
            review_status = case.source.get("spot_review_status")
            if review_status not in VALID_SPOT_REVIEW_STATUSES:
                errors.append(
                    f"Case {case.case_id} has invalid spot_review_status: {review_status!r}."
                )
            if review_status == "verified" and not case.source.get("review_artifact"):
                errors.append(
                    f"Case {case.case_id} claims verified review without review_artifact."
                )
    return errors
