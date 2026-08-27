from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


ADAPTER_NAME = "cello21_noninteractive"
ADAPTER_VERSION = "1.0"
SUMMARY_SCHEMA = "cello21.mapping-summary.v1"
SUMMARY_FILENAME = "cello21_mapping_summary.json"
AUTHORITY_ENV = "CELLO21_MAPPING_AUTHORIZED_EXPERIMENT"
DEFAULT_MAX_PERMUTATIONS = 50_000
QUALIFIED_SOURCE_COMMIT = "f5b664422ecb051f244724289e33bb596817c278"
QUALIFIED_IMAGE_DIGEST = (
    "sha256:544bf84363c66a3a5597ad467c289c9c95707dcd2f1566e49e4d3b55d3607c1e"
)

MAPPING_SUFFIXES = (
    "_yosys.json",
    "_activity-table.csv",
    "_circuit-score.csv",
)
EXPORT_SUFFIXES = (
    "_eugene.eug",
    "_dpl-part-information.csv",
    "_dpl-dna-designs.csv",
    "_dna-sequences.csv",
    "_pySBOL3.nt",
    "_all-files.zip",
)


class Cello21AdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class Cello21RunSpec:
    cello_root: Path
    verilog_path: Path
    ucf_path: Path
    input_path: Path
    output_path: Path
    result_dir: Path
    experiment_id: str
    source_commit: str
    image_digest: str
    required_inputs: int
    required_outputs: int
    required_gates: int
    max_permutations: int = DEFAULT_MAX_PERMUTATIONS


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def permutation_preflight(
    *,
    ucf_path: str | Path,
    input_path: str | Path,
    output_path: str | Path,
    required_inputs: int,
    required_outputs: int,
    required_gates: int,
    max_permutations: int = DEFAULT_MAX_PERMUTATIONS,
) -> dict[str, Any]:
    main = _json_list(ucf_path)
    inputs = _json_list(input_path)
    outputs = _json_list(output_path)
    available_inputs = _collection_count(inputs, "input_sensors")
    available_outputs = _collection_count(outputs, "output_devices")
    gate_groups = {
        str(item.get("group") or "").strip()
        for item in main
        if item.get("collection") == "gates" and str(item.get("group") or "").strip()
    }
    available_gates = len(gate_groups)
    required = (required_inputs, required_outputs, required_gates)
    available = (available_inputs, available_outputs, available_gates)
    if any(value < 0 for value in required):
        raise Cello21AdapterError("Required node counts must be non-negative.")
    if any(need > have for need, have in zip(required, available)):
        raise Cello21AdapterError(
            "Required nodes exceed the selected UCF capacity: "
            f"required={required}, available={available}."
        )
    count = (
        math.perm(available_inputs, required_inputs)
        * math.perm(available_outputs, required_outputs)
        * math.perm(available_gates, required_gates)
    )
    return {
        "search_mode": "exhaustive",
        "required": {
            "inputs": required_inputs,
            "outputs": required_outputs,
            "gates": required_gates,
        },
        "available": {
            "inputs": available_inputs,
            "outputs": available_outputs,
            "gate_groups": available_gates,
        },
        "permutation_count": count,
        "max_permutations": max_permutations,
        "passed": count <= max_permutations,
        "reason": (
            "bounded_exhaustive_search_allowed"
            if count <= max_permutations
            else "permutation_cap_exceeded"
        ),
    }


def require_execution_authority(*, execute: bool, experiment_id: str) -> None:
    if not execute:
        raise Cello21AdapterError(
            "Execution is disabled. Pass --execute only after a named activation review."
        )
    authorized = os.getenv(AUTHORITY_ENV, "").strip()
    if not experiment_id or authorized != experiment_id:
        raise Cello21AdapterError(
            f"Fail-closed authority check failed: {AUTHORITY_ENV} must exactly match "
            "the requested experiment ID."
        )


def validate_qualified_toolchain_identity(
    *, source_commit: str, image_digest: str
) -> None:
    if source_commit != QUALIFIED_SOURCE_COMMIT:
        raise Cello21AdapterError(
            "Cello source commit is not the qualified Cello 2.1 R1 revision: "
            f"expected={QUALIFIED_SOURCE_COMMIT}, actual={source_commit}."
        )
    if image_digest != QUALIFIED_IMAGE_DIGEST:
        raise Cello21AdapterError(
            "Cello image digest is not the qualified Cello 2.1 R1 image: "
            f"expected={QUALIFIED_IMAGE_DIGEST}, actual={image_digest}."
        )


def execute_cello21(spec: Cello21RunSpec, *, execute: bool = False) -> dict[str, Any]:
    require_execution_authority(execute=execute, experiment_id=spec.experiment_id)
    _validate_spec_paths(spec)
    validate_qualified_toolchain_identity(
        source_commit=spec.source_commit, image_digest=spec.image_digest
    )
    actual_commit = read_git_head(spec.cello_root)
    if actual_commit != spec.source_commit:
        raise Cello21AdapterError(
            f"Cello source identity mismatch: expected={spec.source_commit}, actual={actual_commit}."
        )
    preflight = permutation_preflight(
        ucf_path=spec.ucf_path,
        input_path=spec.input_path,
        output_path=spec.output_path,
        required_inputs=spec.required_inputs,
        required_outputs=spec.required_outputs,
        required_gates=spec.required_gates,
        max_permutations=spec.max_permutations,
    )
    if preflight["passed"] is not True:
        raise Cello21AdapterError(
            "Formal mapping blocked because bounded exhaustive search exceeds the cap: "
            f"{preflight['permutation_count']} > {preflight['max_permutations']}."
        )

    spec.result_dir.mkdir(parents=True, exist_ok=True)
    root_text = str(spec.cello_root.resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    # Deliberately import only after every path, identity, cap, and authority gate passes.
    from core_algorithm.celloAlgo import CELLO3  # type: ignore[import-not-found]

    process = CELLO3(
        spec.verilog_path.stem,
        spec.ucf_path.name,
        spec.input_path.name,
        spec.output_path.name,
        str(spec.verilog_path.parent),
        str(spec.ucf_path.parent),
        str(spec.result_dir),
        {
            "yosys_cmd_choice": 1,
            "verbose": False,
            "print_iters": False,
            "test_configs": False,
            "log_overwrite": True,
            "exhaustive": True,
            "iterations": preflight["permutation_count"],
        },
    )
    summary = build_mapping_summary(spec=spec, process=process, preflight=preflight)
    summary_path = spec.result_dir / SUMMARY_FILENAME
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if summary["mapping"]["status"] != "MAPPING_PASS":
        raise Cello21AdapterError("Cello completed but did not satisfy MAPPING_PASS.")
    return summary


def build_mapping_summary(
    *, spec: Cello21RunSpec, process: Any, preflight: dict[str, Any]
) -> dict[str, Any]:
    best_graphs = list(getattr(process, "best_graphs", []) or [])
    if not best_graphs:
        raise Cello21AdapterError("Cello exposes no best_graphs result.")
    score, graph, _truth_table, _truth_labels = max(
        best_graphs, key=lambda item: float(item[0])
    )
    score = float(score)
    assignments = _serialize_assignments(process, graph)
    mapping_artifacts = _artifact_entries(spec.result_dir, MAPPING_SUFFIXES)
    export_artifacts = _artifact_entries(spec.result_dir, EXPORT_SUFFIXES)
    mapping_complete = (
        math.isfinite(score)
        and score > 0
        and all(assignments[role] for role in ("inputs", "gates", "outputs"))
        and len(mapping_artifacts) == len(MAPPING_SUFFIXES)
    )
    return {
        "schema_version": SUMMARY_SCHEMA,
        "adapter": {"name": ADAPTER_NAME, "version": ADAPTER_VERSION},
        "experiment": {
            "experiment_id": spec.experiment_id,
            "execution_authorized_by_adapter_gate": True,
        },
        "toolchain": {
            "name": "Cello-v2-1-Core",
            "source_commit": spec.source_commit,
            "image_digest": spec.image_digest,
        },
        "search": preflight,
        "inputs": {
            role: {
                "filename": path.name,
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
            for role, path in (
                ("verilog", spec.verilog_path),
                ("ucf", spec.ucf_path),
                ("input", spec.input_path),
                ("output", spec.output_path),
            )
        },
        "mapping": {
            "status": "MAPPING_PASS" if mapping_complete else "MAPPING_FAILED",
            "score": score,
            "assignments": assignments,
        },
        "artifacts": {
            "mapping_pass": mapping_complete,
            "export_pass": len(export_artifacts) == len(EXPORT_SUFFIXES),
            "mapping_required": mapping_artifacts,
            "exports": export_artifacts,
            "missing_mapping_suffixes": _missing_suffixes(
                mapping_artifacts, MAPPING_SUFFIXES
            ),
            "missing_export_suffixes": _missing_suffixes(
                export_artifacts, EXPORT_SUFFIXES
            ),
        },
        "claim_boundary": (
            "Computational Cello 2.1 external-tool mapping only; not legacy Cello-v2 "
            "equivalence, buildability, or biological validation."
        ),
    }


def read_git_head(root: str | Path) -> str:
    git_dir = Path(root) / ".git"
    identity_file = Path(root) / "CELLO21_SOURCE_COMMIT"
    if not git_dir.exists():
        if identity_file.is_file():
            value = identity_file.read_text(encoding="utf-8").strip()
            if re.fullmatch(r"[0-9a-f]{40}", value):
                return value
        raise Cello21AdapterError(
            f"Could not resolve source identity from .git or {identity_file}."
        )
    head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    if not head.startswith("ref: "):
        return head
    ref_name = head[5:].strip()
    loose_ref = git_dir / Path(ref_name)
    if loose_ref.is_file():
        return loose_ref.read_text(encoding="utf-8").strip()
    packed = git_dir / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith(("#", "^")):
                commit, name = line.split(" ", 1)
                if name == ref_name:
                    return commit
    raise Cello21AdapterError(f"Could not resolve Git HEAD for {root}.")


def _serialize_assignments(process: Any, graph: Any) -> dict[str, list[dict[str, Any]]]:
    rnl = process.rnl
    input_rows = [
        {
            "role": "input",
            "logic_node_id": str(logic[0]),
            "part_id": str(assigned.name),
        }
        for logic, assigned in zip(rnl.inputs, graph.inputs)
    ]
    gate_rows = [
        {
            "role": "gate",
            "logic_node_id": str(logic_id),
            "gate_group": str(assigned.name),
            "part_id": str(assigned.gate_in_use or assigned.name),
            "gate_type": str(assigned.gate_type),
        }
        for logic_id, assigned in zip(rnl.gates, graph.gates)
    ]
    output_rows = [
        {
            "role": "output",
            "logic_node_id": str(logic[0]),
            "part_id": str(assigned.name),
        }
        for logic, assigned in zip(rnl.outputs, graph.outputs)
    ]
    return {
        "inputs": sorted(input_rows, key=lambda row: row["logic_node_id"]),
        "gates": sorted(gate_rows, key=lambda row: row["logic_node_id"]),
        "outputs": sorted(output_rows, key=lambda row: row["logic_node_id"]),
    }


def _artifact_entries(root: Path, suffixes: Sequence[str]) -> list[dict[str, Any]]:
    entries = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if any(path.name.endswith(suffix) for suffix in suffixes):
            entries.append(
                {
                    "relative_path": path.relative_to(root).as_posix(),
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    return entries


def _missing_suffixes(
    entries: Sequence[dict[str, Any]], suffixes: Sequence[str]
) -> list[str]:
    names = [str(entry.get("relative_path") or "") for entry in entries]
    return [
        suffix
        for suffix in suffixes
        if not any(name.endswith(suffix) for name in names)
    ]


def _json_list(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(
        isinstance(item, dict) for item in payload
    ):
        raise Cello21AdapterError(f"Expected a JSON object list: {path}")
    return payload


def _collection_count(payload: Sequence[dict[str, Any]], collection: str) -> int:
    return sum(1 for item in payload if item.get("collection") == collection)


def _validate_spec_paths(spec: Cello21RunSpec) -> None:
    if not spec.cello_root.is_dir():
        raise Cello21AdapterError(f"Cello root does not exist: {spec.cello_root}")
    for path in (spec.verilog_path, spec.ucf_path, spec.input_path, spec.output_path):
        if not path.is_file():
            raise Cello21AdapterError(f"Required input does not exist: {path}")
    constraint_parents = {
        path.resolve().parent
        for path in (spec.ucf_path, spec.input_path, spec.output_path)
    }
    if len(constraint_parents) != 1:
        raise Cello21AdapterError(
            "UCF, input, and output JSON files must share one directory."
        )
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", spec.image_digest):
        raise Cello21AdapterError("A pinned sha256 image digest is required.")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed noninteractive Cello 2.1 adapter"
    )
    parser.add_argument("--cello-root", type=Path, required=True)
    parser.add_argument("--verilog", type=Path, required=True)
    parser.add_argument("--ucf", type=Path, required=True)
    parser.add_argument("--input", dest="input_path", type=Path, required=True)
    parser.add_argument("--output", dest="output_path", type=Path, required=True)
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--required-inputs", type=int, required=True)
    parser.add_argument("--required-outputs", type=int, required=True)
    parser.add_argument("--required-gates", type=int, required=True)
    parser.add_argument(
        "--max-permutations", type=int, default=DEFAULT_MAX_PERMUTATIONS
    )
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--execute", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    preflight = permutation_preflight(
        ucf_path=args.ucf,
        input_path=args.input_path,
        output_path=args.output_path,
        required_inputs=args.required_inputs,
        required_outputs=args.required_outputs,
        required_gates=args.required_gates,
        max_permutations=args.max_permutations,
    )
    if args.preflight_only:
        print(json.dumps(preflight, indent=2, sort_keys=True))
        return 0 if preflight["passed"] else 2
    spec = Cello21RunSpec(
        cello_root=args.cello_root,
        verilog_path=args.verilog,
        ucf_path=args.ucf,
        input_path=args.input_path,
        output_path=args.output_path,
        result_dir=args.result_dir,
        experiment_id=args.experiment_id,
        source_commit=args.source_commit,
        image_digest=args.image_digest,
        required_inputs=args.required_inputs,
        required_outputs=args.required_outputs,
        required_gates=args.required_gates,
        max_permutations=args.max_permutations,
    )
    try:
        execute_cello21(spec, execute=args.execute)
    except Cello21AdapterError as exc:
        print(f"CELLO21_ADAPTER_BLOCKED: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
