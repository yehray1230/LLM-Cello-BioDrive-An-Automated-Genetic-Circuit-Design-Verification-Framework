from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.scripts.check_duplicate_functions import (
    baseline_payload,
    build_report,
    detect_groups,
    load_exception_ids,
    scan_functions,
)


def _write(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_detects_exact_and_repeated_name_groups(tmp_path: Path) -> None:
    _write(
        tmp_path / "application/a.py",
        "def _shared(value):\n    parsed = float(value)\n    return parsed + 1\n",
    )
    _write(
        tmp_path / "src/b.py",
        "def _shared(value):\n    parsed = float(value)\n    return parsed + 1\n",
    )

    records, errors, files_scanned = scan_functions(tmp_path, ("application", "src"))
    groups = detect_groups(records)

    assert errors == []
    assert files_scanned == 2
    assert len(groups["exact_body"]) == 1
    assert groups["repeated_name"][0]["id"] == "repeated_name:_shared"


def test_structural_match_preserves_identifier_relationships(tmp_path: Path) -> None:
    _write(tmp_path / "application/a.py", "def first(x):\n    y = x + 1\n    return y * x\n")
    _write(tmp_path / "src/b.py", "def second(a):\n    b = a + 1\n    return b * a\n")
    _write(tmp_path / "src/c.py", "def third(a):\n    b = a + 1\n    return b * b\n")

    records, _, _ = scan_functions(tmp_path, ("application", "src"))
    groups = detect_groups(records)

    assert len(groups["exact_body"]) == 0
    assert len(groups["structural_body"]) == 1
    symbols = {item["symbol"] for item in groups["structural_body"][0]["symbols"]}
    assert symbols == {"application/a.py::first", "src/b.py::second"}


def test_baseline_comparison_is_report_only(tmp_path: Path) -> None:
    _write(tmp_path / "application/a.py", "def alpha(x):\n    y = x + 1\n    return y\n")
    _write(tmp_path / "src/b.py", "def beta(x):\n    y = x + 1\n    return y\n")
    initial = build_report(tmp_path, scan_paths=("application", "src"))
    baseline_path = tmp_path / "quality/duplication_baseline.json"
    baseline_path.parent.mkdir(parents=True)
    baseline_path.write_text(json.dumps(baseline_payload(initial)), encoding="utf-8")

    _write(tmp_path / "src/c.py", "def _helper(x):\n    return x\n")
    _write(tmp_path / "application/d.py", "def _helper(x):\n    return x\n")
    report = build_report(
        tmp_path,
        scan_paths=("application", "src"),
        baseline_path=baseline_path,
    )

    assert report["mode"] == "report-only"
    assert "repeated_name:_helper" in report["baseline_comparison"]["new_group_ids"]
    assert report["summary"]["unexcepted_new_groups"] >= 1


def test_baseline_detects_new_member_in_existing_group(tmp_path: Path) -> None:
    source = "def {name}(x):\n    y = x + 1\n    return y\n"
    _write(tmp_path / "application/a.py", source.format(name="alpha"))
    _write(tmp_path / "src/b.py", source.format(name="beta"))
    initial = build_report(tmp_path, scan_paths=("application", "src"))
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline_payload(initial)), encoding="utf-8")

    _write(tmp_path / "src/c.py", source.format(name="gamma"))
    report = build_report(
        tmp_path,
        scan_paths=("application", "src"),
        baseline_path=baseline_path,
    )

    assert report["summary"]["new_groups"] == 0
    assert report["summary"]["new_occurrences_in_existing_groups"] == 1
    additions = report["baseline_comparison"]["new_symbols_in_existing_groups"]
    assert list(additions.values()) == [["src/c.py::gamma"]]


def test_exception_registry_requires_review_fields(tmp_path: Path) -> None:
    path = tmp_path / "exceptions.json"
    path.write_text(
        json.dumps(
            {
                "exceptions": [
                    {
                        "group_id": "repeated_name:_adapter",
                        "classification": "A",
                        "reason": "Transport contracts differ.",
                        "boundary": "API and MCP adapters.",
                        "review_trigger": "Review when contracts converge.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert load_exception_ids(path) == {"repeated_name:_adapter"}

    path.write_text(json.dumps({"exceptions": [{"group_id": "bad"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        load_exception_ids(path)
