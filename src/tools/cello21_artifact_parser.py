from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence

from tools.cello_artifact_parser import CelloParseResult
from tools.cello21_adapter import (
    QUALIFIED_IMAGE_DIGEST,
    QUALIFIED_SOURCE_COMMIT,
)


SUMMARY_FILENAME = "cello21_mapping_summary.json"
SUMMARY_SCHEMA = "cello21.mapping-summary.v1"


class Cello21SummaryParser:
    """Parse only the stable, hash-bound summary emitted by the Cello 2.1 adapter."""

    name = "cello21_mapping_summary"
    version = "1.0"

    def parse_directory(
        self, artifact_dir: str | Path, *, placements_only: bool = False
    ) -> CelloParseResult:
        del placements_only  # Cello 2.1 has a distinct, versioned provenance contract.
        root = Path(artifact_dir)
        result = CelloParseResult(parser=self.name, parser_version=self.version)
        summaries = sorted(root.rglob(SUMMARY_FILENAME)) if root.exists() else []
        if len(summaries) != 1:
            result.warnings.append(
                f"Expected exactly one {SUMMARY_FILENAME}; found {len(summaries)}."
            )
            return result
        summary_path = summaries[0]
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
            assignments, metadata = _validate_summary(root, summary_path, payload)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            result.warnings.append(f"Invalid Cello 2.1 mapping summary: {exc}")
            return result
        result.source_files = [str(summary_path.resolve())]
        result.assignments = assignments
        result.assignment_provenance = "cello21_mapping_summary"
        result.metadata = metadata
        return result


def _validate_summary(
    artifact_root: Path, summary_path: Path, payload: Any
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("schema_version") != SUMMARY_SCHEMA:
        raise ValueError("unsupported schema_version")
    adapter = payload.get("adapter")
    if not isinstance(adapter, dict) or adapter.get("name") != "cello21_noninteractive":
        raise ValueError("summary was not emitted by the qualified adapter")
    search = payload.get("search")
    if not isinstance(search, dict) or search.get("search_mode") != "exhaustive":
        raise ValueError("formal evidence requires exhaustive search")
    count = _bounded_int(search.get("permutation_count"), "permutation_count")
    cap = _bounded_int(search.get("max_permutations"), "max_permutations")
    if search.get("passed") is not True or count > cap or cap > 50_000:
        raise ValueError("bounded exhaustive-search policy failed")

    toolchain = payload.get("toolchain")
    if not isinstance(toolchain, dict):
        raise ValueError("missing toolchain identity")
    source_commit = str(toolchain.get("source_commit") or "")
    image_digest = str(toolchain.get("image_digest") or "")
    if source_commit != QUALIFIED_SOURCE_COMMIT:
        raise ValueError("summary source commit is not the qualified R1 revision")
    if image_digest != QUALIFIED_IMAGE_DIGEST:
        raise ValueError("summary image digest is not the qualified R1 image")

    _validate_inputs(artifact_root, payload.get("inputs"))
    mapping = payload.get("mapping")
    if not isinstance(mapping, dict) or mapping.get("status") != "MAPPING_PASS":
        raise ValueError("mapping status is not MAPPING_PASS")
    score = _positive_float(mapping.get("score"), "mapping score")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict) or artifacts.get("mapping_pass") is not True:
        raise ValueError("mapping artifact assessment did not pass")
    mapping_entries = artifacts.get("mapping_required")
    if not isinstance(mapping_entries, list):
        raise ValueError("missing mapping artifact entries")
    resolved = _validate_artifact_entries(summary_path.parent, mapping_entries)
    _require_suffixes(
        resolved, ("_yosys.json", "_activity-table.csv", "_circuit-score.csv")
    )
    score_files = [
        path for path in resolved if path.name.endswith("_circuit-score.csv")
    ]
    if len(score_files) != 1 or not math.isclose(
        _read_circuit_score(score_files[0]), score, rel_tol=1e-12, abs_tol=1e-12
    ):
        raise ValueError("summary score does not match the native circuit-score CSV")

    grouped = mapping.get("assignments")
    if not isinstance(grouped, dict):
        raise ValueError("missing assignments")
    assignments: list[dict[str, Any]] = []
    for key, role in (("inputs", "input"), ("gates", "gate"), ("outputs", "output")):
        rows = grouped.get(key)
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"missing {key} assignments")
        for row in rows:
            if not isinstance(row, dict) or row.get("role") != role:
                raise ValueError(f"invalid {role} assignment record")
            logic_node_id = str(row.get("logic_node_id") or "").strip()
            part_id = str(row.get("part_id") or "").strip()
            if not logic_node_id or not part_id:
                raise ValueError("assignment identifiers must be non-empty")
            assignments.append(
                {
                    "logic_node_id": logic_node_id,
                    "part_id": part_id,
                    "part_name": part_id,
                    "part_type": role,
                    "sequence": None,
                    "sequence_status": "not_asserted_by_mapping_summary",
                    "evidence_source": str(summary_path.resolve()),
                    "confidence": None,
                    "gate_type": row.get("gate_type"),
                    "raw_gate_name": row.get("gate_group"),
                    "assignment_role": role,
                    "assignment_provenance": "cello21_mapping_summary",
                }
            )
    node_keys = [(row["assignment_role"], row["logic_node_id"]) for row in assignments]
    if len(node_keys) != len(set(node_keys)):
        raise ValueError("duplicate assignment role/node identifiers")
    return assignments, {
        "validated": True,
        "mapping_status": "MAPPING_PASS",
        "score": score,
        "search": search,
        "toolchain": toolchain,
        "export_pass": artifacts.get("export_pass") is True,
        "missing_export_suffixes": list(artifacts.get("missing_export_suffixes") or []),
    }


def _validate_inputs(root: Path, inputs: Any) -> None:
    if not isinstance(inputs, dict):
        raise ValueError("missing frozen input identities")
    for role in ("verilog", "ucf", "input", "output"):
        entry = inputs.get(role)
        if not isinstance(entry, dict):
            raise ValueError(f"missing {role} identity")
        filename = str(entry.get("filename") or "")
        if not filename or Path(filename).name != filename:
            raise ValueError(f"invalid {role} filename")
        expected_hash = str(entry.get("sha256") or "")
        expected_size = _bounded_int(entry.get("size_bytes"), f"{role} size")
        candidates = [path for path in root.rglob(filename) if path.is_file()]
        matches = [
            path
            for path in candidates
            if path.stat().st_size == expected_size and _sha256(path) == expected_hash
        ]
        if len(matches) != 1:
            raise ValueError(f"{role} input identity is missing or ambiguous")


def _validate_artifact_entries(root: Path, entries: Sequence[Any]) -> list[Path]:
    resolved: list[Path] = []
    root_resolved = root.resolve()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("invalid artifact entry")
        relative = Path(str(entry.get("relative_path") or ""))
        path = (root / relative).resolve()
        if root_resolved not in path.parents or not path.is_file():
            raise ValueError(
                "artifact path escapes or is absent from the result directory"
            )
        if path.stat().st_size != _bounded_int(
            entry.get("size_bytes"), "artifact size"
        ):
            raise ValueError(f"artifact size mismatch: {relative}")
        if _sha256(path) != str(entry.get("sha256") or ""):
            raise ValueError(f"artifact hash mismatch: {relative}")
        resolved.append(path)
    return resolved


def _require_suffixes(paths: Sequence[Path], suffixes: Sequence[str]) -> None:
    missing = [
        suffix
        for suffix in suffixes
        if not any(path.name.endswith(suffix) for path in paths)
    ]
    if missing:
        raise ValueError("missing required native artifacts: " + ", ".join(missing))


def _read_circuit_score(path: Path) -> float:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if len(rows) != 1 or len(rows[0]) != 2 or rows[0][0] != "circuit_score":
        raise ValueError("invalid circuit-score CSV")
    return _positive_float(rows[0][1], "native circuit score")


def _bounded_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"invalid {label}")
    number = value
    if number < 0:
        raise ValueError(f"invalid {label}")
    return number


def _positive_float(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return number


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
