from __future__ import annotations

import json
from pathlib import Path

from app import (
    _list_host_profiles,
    _list_json_repository_records,
    _list_parameter_fit_snapshots,
)


def _repository_dir(root: Path, name: str) -> Path:
    path = root / "outputs" / "api_data" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_missing_repository_directories_are_created_and_list_as_empty(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert _list_host_profiles() == []
    assert _list_parameter_fit_snapshots() == []
    assert (tmp_path / "outputs" / "api_data" / "host_profiles").is_dir()
    assert (tmp_path / "outputs" / "api_data" / "parameter_fit_snapshots").is_dir()


def test_wrappers_use_separate_repository_directories(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    host_dir = _repository_dir(tmp_path, "host_profiles")
    snapshot_dir = _repository_dir(tmp_path, "parameter_fit_snapshots")
    _write_json(host_dir / "host.json", {"profile_id": "host-1"})
    _write_json(snapshot_dir / "snapshot.json", {"snapshot_id": "snapshot-1"})

    assert _list_host_profiles() == [{"profile_id": "host-1"}]
    assert _list_parameter_fit_snapshots() == [{"snapshot_id": "snapshot-1"}]


def test_records_are_sorted_by_filename_not_payload_identifier(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    host_dir = _repository_dir(tmp_path, "host_profiles")
    _write_json(host_dir / "b.json", {"profile_id": "a-payload-id"})
    _write_json(host_dir / "a.json", {"profile_id": "z-payload-id"})

    assert _list_host_profiles() == [
        {"profile_id": "z-payload-id"},
        {"profile_id": "a-payload-id"},
    ]


def test_invalid_json_and_non_object_records_are_skipped_individually(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    host_dir = _repository_dir(tmp_path, "host_profiles")
    _write_json(host_dir / "a-valid.json", {"profile_id": "valid"})
    (host_dir / "b-invalid.json").write_text("{", encoding="utf-8")
    _write_json(host_dir / "c-list.json", [{"profile_id": "nested"}])

    assert _list_host_profiles() == [{"profile_id": "valid"}]


def test_unvalidated_object_payload_is_returned_to_ui(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    snapshot_dir = _repository_dir(tmp_path, "parameter_fit_snapshots")
    _write_json(snapshot_dir / "missing-id.json", {"unexpected": True})

    assert _list_parameter_fit_snapshots() == [{"unexpected": True}]


def test_non_json_read_failure_discards_other_valid_records(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    host_dir = _repository_dir(tmp_path, "host_profiles")
    _write_json(host_dir / "a-valid.json", {"profile_id": "valid"})
    (host_dir / "b-invalid-utf8.json").write_bytes(b"\xff")

    assert _list_host_profiles() == []


def test_shared_private_helper_accepts_an_explicit_repository_path(
    tmp_path: Path,
) -> None:
    repository_dir = tmp_path / "custom-records"
    repository_dir.mkdir()
    _write_json(repository_dir / "record.json", {"record_id": "custom"})

    assert _list_json_repository_records(repository_dir) == [
        {"record_id": "custom"}
    ]
