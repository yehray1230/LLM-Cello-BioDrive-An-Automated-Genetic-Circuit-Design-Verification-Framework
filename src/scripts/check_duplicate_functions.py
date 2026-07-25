from __future__ import annotations

import argparse
import ast
import copy
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
DETECTOR_VERSION = "0.1.0"
DEFAULT_SCAN_PATHS = (
    "application",
    "benchmark_suite",
    "src",
    "app.py",
    "oracle_evaluator.py",
    "vector_db.py",
)
DEFAULT_EXCLUDED_PARTS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "tests",
    "venv",
}


@dataclass(frozen=True)
class FunctionRecord:
    path: str
    qualified_name: str
    start_line: int
    end_line: int
    statement_count: int
    exact_fingerprint: str
    structural_fingerprint: str

    @property
    def symbol(self) -> str:
        return f"{self.path}::{self.qualified_name}"

    def location(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("exact_fingerprint")
        payload.pop("structural_fingerprint")
        payload["symbol"] = self.symbol
        return payload


class _IdentifierNormalizer(ast.NodeTransformer):
    def __init__(self) -> None:
        self._names: dict[str, str] = {}

    def _normalized(self, name: str) -> str:
        if name not in self._names:
            self._names[name] = f"v{len(self._names)}"
        return self._names[name]

    def visit_Name(self, node: ast.Name) -> ast.AST:  # noqa: N802
        node.id = self._normalized(node.id)
        return node

    def visit_arg(self, node: ast.arg) -> ast.AST:  # noqa: N802
        node.arg = self._normalized(node.arg)
        return node


def _body_without_docstring(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _fingerprint(body: list[ast.stmt], *, structural: bool) -> str:
    module = ast.Module(body=copy.deepcopy(body), type_ignores=[])
    if structural:
        module = _IdentifierNormalizer().visit(module)
        ast.fix_missing_locations(module)
    dumped = ast.dump(module, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()


class _FunctionCollector(ast.NodeVisitor):
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.scope: list[str] = []
        self.records: list[FunctionRecord] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        body = _body_without_docstring(node)
        statement_count = sum(isinstance(item, ast.stmt) for item in ast.walk(ast.Module(body=body, type_ignores=[])))
        qualified_name = ".".join((*self.scope, node.name))
        self.records.append(
            FunctionRecord(
                path=self.relative_path,
                qualified_name=qualified_name,
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                statement_count=statement_count,
                exact_fingerprint=_fingerprint(body, structural=False),
                structural_fingerprint=_fingerprint(body, structural=True),
            )
        )
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()


def _iter_python_files(root: Path, scan_paths: Iterable[str]) -> list[Path]:
    files: set[Path] = set()
    for raw_path in scan_paths:
        path = root / raw_path
        if path.is_file() and path.suffix == ".py":
            files.add(path)
        elif path.is_dir():
            for candidate in path.rglob("*.py"):
                relative_parts = candidate.relative_to(root).parts
                if not any(part in DEFAULT_EXCLUDED_PARTS for part in relative_parts):
                    files.add(candidate)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def scan_functions(
    root: Path,
    scan_paths: Iterable[str] = DEFAULT_SCAN_PATHS,
) -> tuple[list[FunctionRecord], list[dict[str, Any]], int]:
    records: list[FunctionRecord] = []
    errors: list[dict[str, Any]] = []
    files = _iter_python_files(root, scan_paths)
    for path in files:
        relative_path = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
        except (OSError, UnicodeError, SyntaxError) as exc:
            errors.append({"path": relative_path, "error": str(exc)})
            continue
        collector = _FunctionCollector(relative_path)
        collector.visit(tree)
        records.extend(collector.records)
    return sorted(records, key=lambda item: item.symbol), errors, len(files)


def _group_id(kind: str, value: str) -> str:
    if kind == "repeated_name":
        return f"{kind}:{value}"
    return f"{kind}:{value[:16]}"


def _fingerprint_groups(
    records: list[FunctionRecord],
    *,
    kind: str,
    fingerprint_attribute: str,
    min_statements: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[FunctionRecord]] = {}
    for record in records:
        if record.statement_count >= min_statements:
            grouped.setdefault(getattr(record, fingerprint_attribute), []).append(record)
    results = []
    for fingerprint, members in grouped.items():
        if len(members) < 2:
            continue
        results.append(
            {
                "id": _group_id(kind, fingerprint),
                "fingerprint": fingerprint,
                "symbols": [member.location() for member in members],
            }
        )
    return sorted(results, key=lambda item: item["id"])


def _repeated_name_groups(records: list[FunctionRecord]) -> list[dict[str, Any]]:
    grouped: dict[str, list[FunctionRecord]] = {}
    for record in records:
        name = record.qualified_name.rsplit(".", 1)[-1]
        if name.startswith("_") and not name.startswith("__"):
            grouped.setdefault(name, []).append(record)
    results = []
    for name, members in grouped.items():
        if len({member.path for member in members}) < 2:
            continue
        results.append(
            {
                "id": _group_id("repeated_name", name),
                "name": name,
                "symbols": [member.location() for member in members],
            }
        )
    return sorted(results, key=lambda item: item["id"])


def detect_groups(
    records: list[FunctionRecord],
    *,
    min_statements: int = 2,
) -> dict[str, list[dict[str, Any]]]:
    exact = _fingerprint_groups(
        records,
        kind="exact_body",
        fingerprint_attribute="exact_fingerprint",
        min_statements=min_statements,
    )
    structural = _fingerprint_groups(
        records,
        kind="structural_body",
        fingerprint_attribute="structural_fingerprint",
        min_statements=min_statements,
    )
    exact_symbol_sets = {
        frozenset(symbol["symbol"] for symbol in group["symbols"]) for group in exact
    }
    structural = [
        group
        for group in structural
        if frozenset(symbol["symbol"] for symbol in group["symbols"])
        not in exact_symbol_sets
    ]
    return {
        "exact_body": exact,
        "structural_body": structural,
        "repeated_name": _repeated_name_groups(records),
    }


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{label} file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {path}: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} root must be a JSON object: {path}")
    return payload


def load_exception_ids(path: Path | None) -> set[str]:
    if path is None:
        return set()
    payload = _load_json_object(path, "Exception registry")
    entries = payload.get("exceptions")
    if not isinstance(entries, list):
        raise ValueError("Exception registry must contain an 'exceptions' array.")
    required = {"group_id", "classification", "reason", "boundary", "review_trigger"}
    result: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"Exception entry {index} must be an object.")
        missing = sorted(required - entry.keys())
        if missing:
            raise ValueError(f"Exception entry {index} is missing: {', '.join(missing)}.")
        if entry["classification"] not in {"A", "L", "T", "D2"}:
            raise ValueError(f"Exception entry {index} has an invalid classification.")
        if not all(isinstance(entry[key], str) and entry[key].strip() for key in required):
            raise ValueError(f"Exception entry {index} has an empty required field.")
        result.add(entry["group_id"])
    return result


def _all_group_ids(groups: dict[str, list[dict[str, Any]]]) -> set[str]:
    return {group["id"] for items in groups.values() for group in items}


def _load_baseline(
    path: Path | None,
) -> tuple[set[str], dict[str, set[str]]]:
    if path is None:
        return set(), {}
    payload = _load_json_object(path, "Baseline")
    raw_groups = payload.get("group_ids")
    if not isinstance(raw_groups, dict):
        raise ValueError("Baseline must contain a 'group_ids' object.")
    ids: set[str] = set()
    for kind, values in raw_groups.items():
        if not isinstance(kind, str) or not isinstance(values, list) or not all(
            isinstance(value, str) for value in values
        ):
            raise ValueError("Baseline group IDs must be arrays of strings.")
        ids.update(values)
    raw_members = payload.get("group_members")
    if not isinstance(raw_members, dict):
        raise ValueError("Baseline must contain a 'group_members' object.")
    members: dict[str, set[str]] = {}
    for group_id, symbols in raw_members.items():
        if not isinstance(group_id, str) or not isinstance(symbols, list) or not all(
            isinstance(symbol, str) for symbol in symbols
        ):
            raise ValueError("Baseline group members must be arrays of symbol strings.")
        members[group_id] = set(symbols)
    if set(members) != ids:
        raise ValueError("Baseline group_ids and group_members must identify the same groups.")
    return ids, members


def build_report(
    root: Path,
    *,
    scan_paths: Iterable[str] = DEFAULT_SCAN_PATHS,
    min_statements: int = 2,
    baseline_path: Path | None = None,
    exceptions_path: Path | None = None,
) -> dict[str, Any]:
    records, parse_errors, files_scanned = scan_functions(root, scan_paths)
    groups = detect_groups(records, min_statements=min_statements)
    current_ids = _all_group_ids(groups)
    current_members = {
        group["id"]: {symbol["symbol"] for symbol in group["symbols"]}
        for items in groups.values()
        for group in items
    }
    baseline_ids, baseline_members = _load_baseline(baseline_path)
    exception_ids = load_exception_ids(exceptions_path)
    new_ids = current_ids - baseline_ids if baseline_path is not None else set()
    missing_ids = baseline_ids - current_ids
    shared_ids = current_ids & baseline_ids
    new_symbols = {
        group_id: sorted(current_members[group_id] - baseline_members[group_id])
        for group_id in sorted(shared_ids)
        if current_members[group_id] - baseline_members[group_id]
    }
    missing_symbols = {
        group_id: sorted(baseline_members[group_id] - current_members[group_id])
        for group_id in sorted(shared_ids)
        if baseline_members[group_id] - current_members[group_id]
    }
    new_occurrences = sum(len(symbols) for symbols in new_symbols.values())
    unexcepted_new_occurrences = sum(
        len(symbols)
        for group_id, symbols in new_symbols.items()
        if group_id not in exception_ids
    )
    return {
        "schema_version": "duplicate-function-report@1.0.0",
        "detector_version": DETECTOR_VERSION,
        "mode": "report-only",
        "scope": {
            "root": str(root.resolve()),
            "scan_paths": list(scan_paths),
            "excluded_parts": sorted(DEFAULT_EXCLUDED_PARTS),
            "min_statements": min_statements,
        },
        "summary": {
            "files_scanned": files_scanned,
            "functions_scanned": len(records),
            "exact_body_groups": len(groups["exact_body"]),
            "structural_body_groups": len(groups["structural_body"]),
            "repeated_name_groups": len(groups["repeated_name"]),
            "parse_errors": len(parse_errors),
            "new_groups": len(new_ids),
            "unexcepted_new_groups": len(new_ids - exception_ids),
            "new_occurrences_in_existing_groups": new_occurrences,
            "unexcepted_new_occurrences_in_existing_groups": (
                unexcepted_new_occurrences
            ),
        },
        "groups": groups,
        "parse_errors": parse_errors,
        "baseline_comparison": {
            "baseline": str(baseline_path) if baseline_path else None,
            "new_group_ids": sorted(new_ids),
            "missing_group_ids": sorted(missing_ids),
            "excepted_new_group_ids": sorted(new_ids & exception_ids),
            "unexcepted_new_group_ids": sorted(new_ids - exception_ids),
            "new_symbols_in_existing_groups": new_symbols,
            "missing_symbols_from_existing_groups": missing_symbols,
        },
    }


def baseline_payload(report: dict[str, Any]) -> dict[str, Any]:
    groups = report["groups"]
    return {
        "schema_version": "duplicate-function-baseline@1.0.0",
        "detector_version": DETECTOR_VERSION,
        "mode": "report-only",
        "group_ids": {
            kind: sorted(group["id"] for group in items)
            for kind, items in groups.items()
        },
        "group_members": {
            group["id"]: sorted(symbol["symbol"] for symbol in group["symbols"])
            for items in groups.values()
            for group in items
        },
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _render_human(report: dict[str, Any]) -> str:
    summary = report["summary"]
    comparison = report["baseline_comparison"]
    lines = [
        "Duplicate Function Detector: REPORT ONLY",
        f"Scanned: {summary['files_scanned']} files / {summary['functions_scanned']} functions",
        (
            "Candidate groups: "
            f"{summary['exact_body_groups']} exact, "
            f"{summary['structural_body_groups']} structural, "
            f"{summary['repeated_name_groups']} repeated-name"
        ),
    ]
    if comparison["baseline"]:
        lines.append(
            "Baseline comparison: "
            f"{summary['new_groups']} new "
            f"({summary['unexcepted_new_groups']} unexcepted), "
            f"{summary['new_occurrences_in_existing_groups']} added occurrences "
            f"({summary['unexcepted_new_occurrences_in_existing_groups']} unexcepted), "
            f"{len(comparison['missing_group_ids'])} no longer present"
        )
    if report["parse_errors"]:
        lines.append(f"Parse errors: {summary['parse_errors']}")
    lines.append("Candidate findings never fail CI in report-only mode.")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report exact, structural, and repeated-name Python function candidates."
    )
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--paths", nargs="+", default=list(DEFAULT_SCAN_PATHS))
    parser.add_argument("--min-statements", type=int, default=2)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--exceptions", type=Path)
    parser.add_argument("--write-baseline", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.min_statements < 1:
        parser.error("--min-statements must be at least 1")
    baseline = args.baseline
    exceptions = args.exceptions
    if (
        baseline is None
        and args.write_baseline is None
        and (args.root / "quality/duplication_baseline.json").exists()
    ):
        baseline = args.root / "quality/duplication_baseline.json"
    if exceptions is None and (args.root / "quality/duplication_exceptions.json").exists():
        exceptions = args.root / "quality/duplication_exceptions.json"
    try:
        report = build_report(
            args.root,
            scan_paths=args.paths,
            min_statements=args.min_statements,
            baseline_path=baseline,
            exceptions_path=exceptions,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if args.write_baseline:
        _write_json(args.write_baseline, baseline_payload(report))
    if args.output:
        _write_json(args.output, report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_render_human(report))
    return 2 if report["parse_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
