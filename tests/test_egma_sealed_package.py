from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from benchmark_suite.egma_generator import generate_egma_dry_run_bundle
from benchmark_suite.egma_sealing import (
    CIPHERTEXT_FILENAME,
    KEY_BYTES,
    MANIFEST_FILENAME,
    SealingBoundaryError,
    SealingIntegrityError,
    SealingKeyError,
    manifest_sha256,
    read_external_key,
    scan_for_secret_material,
    seal_synthetic_payload,
    synthetic_throwaway_payload,
    unlock_synthetic_payload,
    validate_sealed_manifest,
    validate_unlock_audit,
)


PROTOCOL_DIR = Path(__file__).parents[1] / "benchmark_suite" / "protocols"


def _package(tmp_path: Path) -> tuple[Path, bytes, dict]:
    key = bytes(range(KEY_BYTES))
    package_dir = tmp_path / "sealed-fixture"
    manifest = seal_synthetic_payload(
        synthetic_throwaway_payload(),
        key=key,
        package_dir=package_dir,
    )
    return package_dir, key, manifest


def test_manifest_ciphertext_contract_hashes_and_round_trip(tmp_path: Path) -> None:
    package_dir, key, manifest = _package(tmp_path)
    schema = json.loads(
        (PROTOCOL_DIR / "egma-sealed-manifest-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(manifest)
    assert validate_sealed_manifest(manifest) == []
    assert manifest["commitments"]["manifest_sha256"] == manifest_sha256(manifest)
    assert manifest["fixture_only"] is True
    assert manifest["confirmatory_eligible"] is False
    assert manifest["sealed_materialized"] is False

    plaintext = json.dumps(synthetic_throwaway_payload(), sort_keys=True)
    public_files = (
        (package_dir / MANIFEST_FILENAME).read_bytes()
        + (package_dir / CIPHERTEXT_FILENAME).read_bytes()
    )
    for marker in ("SYNTHETIC-ANSWER-ALPHA-ONLY", "SYNTHETIC-ANSWER-BETA-ONLY"):
        assert marker.encode() not in public_files
        assert marker not in json.dumps(manifest, sort_keys=True)
    assert plaintext.encode() not in public_files

    audit_path = tmp_path / "unlock-audit.jsonl"
    unlocked = unlock_synthetic_payload(
        package_dir,
        key=key,
        audit_path=audit_path,
        timestamp_utc="2026-07-25T00:00:00Z",
    )
    assert unlocked == synthetic_throwaway_payload()
    assert validate_unlock_audit(audit_path) == []


def test_no_key_wrong_key_and_truncated_key_fail_closed(tmp_path: Path) -> None:
    package_dir, key, _ = _package(tmp_path)
    audit_path = tmp_path / "unlock-audit.jsonl"

    with pytest.raises(SealingKeyError):
        unlock_synthetic_payload(package_dir, key=None, audit_path=audit_path)
    with pytest.raises(SealingIntegrityError, match="authentication failed"):
        unlock_synthetic_payload(
            package_dir,
            key=bytes(reversed(key)),
            audit_path=audit_path,
        )
    with pytest.raises(SealingKeyError, match="exactly 32 bytes"):
        unlock_synthetic_payload(package_dir, key=key[:-1], audit_path=audit_path)
    assert not audit_path.exists()


@pytest.mark.parametrize("mutation", ["truncate", "substitute"])
def test_truncated_or_substituted_ciphertext_fails_closed(
    tmp_path: Path,
    mutation: str,
) -> None:
    package_dir, key, _ = _package(tmp_path)
    ciphertext_path = package_dir / CIPHERTEXT_FILENAME
    ciphertext = ciphertext_path.read_bytes()
    if mutation == "truncate":
        ciphertext_path.write_bytes(ciphertext[:-1])
    else:
        replacement = bytearray(ciphertext)
        replacement[len(replacement) // 2] ^= 1
        ciphertext_path.write_bytes(bytes(replacement))

    audit_path = tmp_path / "unlock-audit.jsonl"
    with pytest.raises(SealingIntegrityError, match="Ciphertext"):
        unlock_synthetic_payload(package_dir, key=key, audit_path=audit_path)
    assert not audit_path.exists()


def test_manifest_substitution_fails_before_unlock_audit(tmp_path: Path) -> None:
    package_dir, key, manifest = _package(tmp_path)
    substituted = deepcopy(manifest)
    substituted["approved_metadata"]["record_count"] += 1
    (package_dir / MANIFEST_FILENAME).write_text(
        json.dumps(substituted, sort_keys=True),
        encoding="utf-8",
    )

    audit_path = tmp_path / "unlock-audit.jsonl"
    with pytest.raises(SealingIntegrityError, match="Manifest SHA-256"):
        unlock_synthetic_payload(package_dir, key=key, audit_path=audit_path)
    assert not audit_path.exists()


def test_substituted_ciphertext_with_recomputed_public_hashes_fails_authentication(
    tmp_path: Path,
) -> None:
    package_dir, key, manifest = _package(tmp_path)
    ciphertext_path = package_dir / CIPHERTEXT_FILENAME
    replacement = bytearray(ciphertext_path.read_bytes())
    replacement[len(replacement) // 2] ^= 1
    ciphertext_path.write_bytes(bytes(replacement))
    manifest["cipher"]["ciphertext_sha256"] = hashlib.sha256(replacement).hexdigest()
    manifest["commitments"]["manifest_sha256"] = manifest_sha256(manifest)
    (package_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, sort_keys=True),
        encoding="utf-8",
    )

    audit_path = tmp_path / "unlock-audit.jsonl"
    with pytest.raises(SealingIntegrityError, match="authentication failed"):
        unlock_synthetic_payload(package_dir, key=key, audit_path=audit_path)
    assert not audit_path.exists()


def test_unlock_audit_is_append_only_hash_chained_and_reproducible(
    tmp_path: Path,
) -> None:
    package_dir, key, manifest = _package(tmp_path)
    audit_path = tmp_path / "unlock-audit.jsonl"

    unlock_synthetic_payload(
        package_dir,
        key=key,
        audit_path=audit_path,
        timestamp_utc="2026-07-25T00:00:00Z",
    )
    first_line = audit_path.read_text(encoding="utf-8").splitlines()[0]
    unlock_synthetic_payload(
        package_dir,
        key=key,
        audit_path=audit_path,
        timestamp_utc="2026-07-25T00:01:00Z",
    )
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    second = json.loads(lines[1])

    assert lines[0] == first_line
    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert second["previous_event_hash"] == first["event_hash"]
    assert first["manifest_sha256"] == manifest["commitments"]["manifest_sha256"]
    assert first["ciphertext_sha256"] == manifest["cipher"]["ciphertext_sha256"]
    assert first["payload_sha256"] == manifest["commitments"]["payload_sha256"]
    assert validate_unlock_audit(audit_path) == []


def test_tampered_existing_audit_blocks_another_append(tmp_path: Path) -> None:
    package_dir, key, _ = _package(tmp_path)
    audit_path = tmp_path / "unlock-audit.jsonl"
    unlock_synthetic_payload(package_dir, key=key, audit_path=audit_path)
    original = audit_path.read_text(encoding="utf-8")
    tampered = original.replace('"sequence": 1', '"sequence": 9')
    audit_path.write_text(tampered, encoding="utf-8")

    with pytest.raises(SealingIntegrityError, match="Existing unlock audit is invalid"):
        unlock_synthetic_payload(package_dir, key=key, audit_path=audit_path)
    assert len(audit_path.read_text(encoding="utf-8").splitlines()) == 1


def test_secret_absence_scan_covers_package_audit_environment_and_snapshots(
    tmp_path: Path,
) -> None:
    package_dir, key, _ = _package(tmp_path)
    audit_path = tmp_path / "unlock-audit.jsonl"
    unlock_synthetic_payload(package_dir, key=key, audit_path=audit_path)
    environment_dump = json.dumps(dict(os.environ), sort_keys=True)

    assert (
        scan_for_secret_material(
            [package_dir, audit_path],
            secret=key,
            additional_blobs={
                "environment_dump": environment_dump,
                "test_snapshot": "synthetic package summary only",
            },
        )
        == []
    )

    leaked = tmp_path / "deliberate-leak.txt"
    leaked.write_text(base64.b64encode(key).decode("ascii"), encoding="utf-8")
    assert scan_for_secret_material([leaked], secret=key) == [str(leaked)]


def test_external_key_boundary_rejects_missing_short_and_in_package_keys(
    tmp_path: Path,
) -> None:
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    inside_key = package_dir / "key.bin"
    inside_key.write_bytes(os.urandom(KEY_BYTES))
    outside_key = tmp_path / "external-key.bin"
    outside_key.write_bytes(os.urandom(KEY_BYTES))
    short_key = tmp_path / "short-key.bin"
    short_key.write_bytes(os.urandom(KEY_BYTES - 1))

    with pytest.raises(SealingKeyError, match="required"):
        read_external_key(None, package_dir=package_dir)
    with pytest.raises(SealingKeyError, match="must not be inside"):
        read_external_key(inside_key, package_dir=package_dir)
    with pytest.raises(SealingKeyError, match="exactly 32 bytes"):
        read_external_key(short_key, package_dir=package_dir)
    assert read_external_key(outside_key, package_dir=package_dir) == outside_key.read_bytes()


def test_slice4_visible_dry_run_cannot_be_sealed_or_relabelled(
    tmp_path: Path,
) -> None:
    visible_dry_run = generate_egma_dry_run_bundle()

    with pytest.raises(SealingBoundaryError, match="Slice 4"):
        seal_synthetic_payload(
            visible_dry_run,
            key=os.urandom(KEY_BYTES),
            package_dir=tmp_path / "forbidden",
        )
    assert not (tmp_path / "forbidden").exists()


def test_existing_package_is_never_overwritten(tmp_path: Path) -> None:
    package_dir, key, _ = _package(tmp_path)
    before = (package_dir / MANIFEST_FILENAME).read_bytes()

    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        seal_synthetic_payload(
            synthetic_throwaway_payload(),
            key=key,
            package_dir=package_dir,
        )
    assert (package_dir / MANIFEST_FILENAME).read_bytes() == before
