from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
from typing import Any, Mapping, Sequence


MANIFEST_SCHEMA_VERSION = "egma-sealed-manifest-v1"
PAYLOAD_SCHEMA_VERSION = "egma-synthetic-sealed-payload-v1"
AUDIT_SCHEMA_VERSION = "egma-unlock-audit-v1"
CIPHER_ALGORITHM = "HMAC-SHA256-ETM-STREAM-v1"
KDF_ALGORITHM = "HMAC-SHA256-LABEL-v1"
KEY_BYTES = 32
SALT_BYTES = 16
NONCE_BYTES = 16
CIPHERTEXT_FILENAME = "payload.enc"
MANIFEST_FILENAME = "manifest.json"

_DRY_RUN_SCHEMA_VERSION = "egma-benchmark-dry-run-v1"
_DRY_RUN_BENCHMARK_ID = "egma_ecoli_transcriptional_logic_dry_run_v1"
_DRY_RUN_CLAIM_STATUS = "offline_fixture_only_not_confirmatory"


class SealingError(ValueError):
    """Base error for fail-closed synthetic sealing operations."""


class SealingKeyError(SealingError):
    """The external key is absent, misplaced, or malformed."""


class SealingIntegrityError(SealingError):
    """A package commitment or authentication check failed."""


class SealingBoundaryError(SealingError):
    """The payload violates the fixture-only promotion boundary."""


def synthetic_throwaway_payload() -> dict[str, Any]:
    """Return a tiny answer-bearing payload that has no benchmark task semantics."""

    return {
        "schema_version": PAYLOAD_SCHEMA_VERSION,
        "fixture_id": "egma-sealing-throwaway-v1",
        "fixture_only": True,
        "confirmatory_eligible": False,
        "sealed_materialized": False,
        "source_status": "synthetic_throwaway",
        "records": [
            {
                "synthetic_id": "throwaway-001",
                "secret_marker": "SYNTHETIC-ANSWER-ALPHA-ONLY",
            },
            {
                "synthetic_id": "throwaway-002",
                "secret_marker": "SYNTHETIC-ANSWER-BETA-ONLY",
            },
        ],
    }


def validate_synthetic_payload(payload: Mapping[str, Any]) -> list[str]:
    """Validate the throwaway contract and reject visible benchmark promotion."""

    errors: list[str] = []
    if payload.get("schema_version") == _DRY_RUN_SCHEMA_VERSION:
        errors.append("Slice 4 visible dry-run bundles cannot be sealed.")
    if payload.get("benchmark_id") == _DRY_RUN_BENCHMARK_ID:
        errors.append("Slice 4 dry-run benchmark_id cannot enter a sealed package.")
    if payload.get("claim_status") == _DRY_RUN_CLAIM_STATUS:
        errors.append("Visible dry-run claim_status cannot be promoted.")
    if "tasks" in payload or "groups" in payload:
        errors.append("Benchmark task/group collections are forbidden in this harness.")

    expected_fields = {
        "schema_version",
        "fixture_id",
        "fixture_only",
        "confirmatory_eligible",
        "sealed_materialized",
        "source_status",
        "records",
    }
    if set(payload) != expected_fields:
        errors.append("Synthetic payload fields do not match the fixture-only contract.")
    if payload.get("schema_version") != PAYLOAD_SCHEMA_VERSION:
        errors.append("Synthetic payload schema_version is invalid.")
    if not isinstance(payload.get("fixture_id"), str) or not payload.get("fixture_id"):
        errors.append("Synthetic payload fixture_id is required.")
    if payload.get("fixture_only") is not True:
        errors.append("Synthetic payload must be fixture_only=true.")
    if payload.get("confirmatory_eligible") is not False:
        errors.append("Synthetic payload must be confirmatory_eligible=false.")
    if payload.get("sealed_materialized") is not False:
        errors.append("Synthetic payload must be sealed_materialized=false.")
    if payload.get("source_status") != "synthetic_throwaway":
        errors.append("Synthetic payload source_status must be synthetic_throwaway.")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        errors.append("Synthetic payload records must be a non-empty array.")
    elif any(not isinstance(record, Mapping) for record in records):
        errors.append("Every synthetic payload record must be an object.")
    return errors


def read_external_key(
    key_path: str | Path | None,
    *,
    package_dir: str | Path | None = None,
) -> bytes:
    """Read an exact 32-byte key and require it to remain outside the package."""

    if key_path is None:
        raise SealingKeyError("An external key path is required.")
    path = Path(key_path).resolve()
    if package_dir is not None:
        package = Path(package_dir).resolve()
        if path == package or package in path.parents:
            raise SealingKeyError("The external key must not be inside the package.")
    try:
        key = path.read_bytes()
    except OSError as exc:
        raise SealingKeyError(f"External key could not be read: {exc}") from exc
    return _validated_key(key)


def seal_synthetic_payload(
    payload: Mapping[str, Any],
    *,
    key: bytes | None,
    package_dir: str | Path,
    package_id: str = "egma-synthetic-sealed-fixture-v1",
) -> dict[str, Any]:
    """Seal only a synthetic throwaway payload into a new fixture package."""

    validated_key = _validated_key(key)
    payload_errors = validate_synthetic_payload(payload)
    if payload_errors:
        raise SealingBoundaryError(" ".join(payload_errors))
    if not package_id or not package_id.replace("-", "").replace("_", "").isalnum():
        raise SealingBoundaryError("package_id must contain only letters, digits, - or _.")

    output = Path(package_dir)
    manifest_path = output / MANIFEST_FILENAME
    ciphertext_path = output / CIPHERTEXT_FILENAME
    if manifest_path.exists() or ciphertext_path.exists():
        raise FileExistsError("Refusing to overwrite an existing sealed fixture package.")
    output.mkdir(parents=True, exist_ok=True)

    payload_bytes = _canonical_json_bytes(payload)
    approved_metadata = {
        "record_count": len(payload["records"]),
        "source_status": "synthetic_throwaway",
    }
    public_header = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "package_id": package_id,
        "payload_contract": PAYLOAD_SCHEMA_VERSION,
        "fixture_only": True,
        "confirmatory_eligible": False,
        "sealed_materialized": False,
        "approved_metadata": approved_metadata,
    }
    aad = _canonical_json_bytes(public_header)
    salt = secrets.token_bytes(SALT_BYTES)
    nonce = secrets.token_bytes(NONCE_BYTES)
    encryption_key, mac_key = _derive_keys(validated_key, salt)
    ciphertext = _xor_bytes(payload_bytes, _keystream(encryption_key, nonce, len(payload_bytes)))
    tag = hmac.digest(mac_key, aad + nonce + ciphertext, "sha256")

    manifest: dict[str, Any] = {
        **public_header,
        "cipher": {
            "algorithm": CIPHER_ALGORITHM,
            "kdf": KDF_ALGORITHM,
            "salt_b64": _b64(salt),
            "nonce_b64": _b64(nonce),
            "tag_b64": _b64(tag),
            "ciphertext_file": CIPHERTEXT_FILENAME,
            "ciphertext_bytes": len(ciphertext),
            "ciphertext_sha256": _sha256_bytes(ciphertext),
        },
        "commitments": {
            "payload_sha256": _sha256_bytes(payload_bytes),
            "aad_sha256": _sha256_bytes(aad),
            "manifest_sha256": "",
        },
    }
    manifest["commitments"]["manifest_sha256"] = manifest_sha256(manifest)
    manifest_errors = validate_sealed_manifest(manifest)
    if manifest_errors:
        raise SealingIntegrityError(" ".join(manifest_errors))

    ciphertext_path.write_bytes(ciphertext)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def unlock_synthetic_payload(
    package_dir: str | Path,
    *,
    key: bytes | None,
    audit_path: str | Path,
    timestamp_utc: str | None = None,
) -> dict[str, Any]:
    """Authenticate, decrypt, validate, then append one hash-chained audit event."""

    validated_key = _validated_key(key)
    package = Path(package_dir)
    manifest = _load_json_object(package / MANIFEST_FILENAME)
    manifest_errors = validate_sealed_manifest(manifest)
    if manifest_errors:
        raise SealingIntegrityError(" ".join(manifest_errors))

    cipher = manifest["cipher"]
    ciphertext_path = package / cipher["ciphertext_file"]
    try:
        ciphertext = ciphertext_path.read_bytes()
    except OSError as exc:
        raise SealingIntegrityError(f"Ciphertext could not be read: {exc}") from exc
    if len(ciphertext) != cipher["ciphertext_bytes"]:
        raise SealingIntegrityError("Ciphertext length commitment does not match.")
    if _sha256_bytes(ciphertext) != cipher["ciphertext_sha256"]:
        raise SealingIntegrityError("Ciphertext SHA-256 commitment does not match.")

    public_header = _public_header(manifest)
    aad = _canonical_json_bytes(public_header)
    if _sha256_bytes(aad) != manifest["commitments"]["aad_sha256"]:
        raise SealingIntegrityError("AAD SHA-256 commitment does not match.")
    salt = _decode_b64(cipher["salt_b64"], "salt")
    nonce = _decode_b64(cipher["nonce_b64"], "nonce")
    tag = _decode_b64(cipher["tag_b64"], "tag")
    encryption_key, mac_key = _derive_keys(validated_key, salt)
    expected_tag = hmac.digest(mac_key, aad + nonce + ciphertext, "sha256")
    if not hmac.compare_digest(tag, expected_tag):
        raise SealingIntegrityError("Ciphertext authentication failed.")

    payload_bytes = _xor_bytes(
        ciphertext,
        _keystream(encryption_key, nonce, len(ciphertext)),
    )
    if _sha256_bytes(payload_bytes) != manifest["commitments"]["payload_sha256"]:
        raise SealingIntegrityError("Payload SHA-256 commitment does not match.")
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SealingIntegrityError("Decrypted payload is not canonical JSON.") from exc
    if not isinstance(payload, dict):
        raise SealingIntegrityError("Decrypted payload must be an object.")
    payload_errors = validate_synthetic_payload(payload)
    if payload_errors:
        raise SealingBoundaryError(" ".join(payload_errors))

    _append_unlock_audit(
        audit_path,
        manifest=manifest,
        timestamp_utc=timestamp_utc,
    )
    return payload


def validate_sealed_manifest(manifest: Mapping[str, Any]) -> list[str]:
    """Validate the public fixture-only manifest and all reproducible hashes."""

    errors: list[str] = []
    required = {
        "schema_version",
        "package_id",
        "payload_contract",
        "fixture_only",
        "confirmatory_eligible",
        "sealed_materialized",
        "approved_metadata",
        "cipher",
        "commitments",
    }
    if set(manifest) != required:
        errors.append("Manifest fields do not match the sealed fixture contract.")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        errors.append("Manifest schema_version is invalid.")
    if manifest.get("payload_contract") != PAYLOAD_SCHEMA_VERSION:
        errors.append("Manifest payload_contract is invalid.")
    if manifest.get("fixture_only") is not True:
        errors.append("Manifest must be fixture_only=true.")
    if manifest.get("confirmatory_eligible") is not False:
        errors.append("Manifest must be confirmatory_eligible=false.")
    if manifest.get("sealed_materialized") is not False:
        errors.append("Manifest must be sealed_materialized=false.")

    approved = manifest.get("approved_metadata")
    if not isinstance(approved, Mapping):
        errors.append("Manifest approved_metadata must be an object.")
    else:
        if set(approved) != {"record_count", "source_status"}:
            errors.append("Manifest approved_metadata contains an unapproved field.")
        if not isinstance(approved.get("record_count"), int) or approved.get(
            "record_count"
        ) < 1:
            errors.append("Manifest record_count must be a positive integer.")
        if approved.get("source_status") != "synthetic_throwaway":
            errors.append("Manifest source_status is invalid.")

    cipher = manifest.get("cipher")
    if not isinstance(cipher, Mapping):
        errors.append("Manifest cipher contract must be an object.")
    else:
        if cipher.get("algorithm") != CIPHER_ALGORITHM:
            errors.append("Manifest cipher algorithm is invalid.")
        if cipher.get("kdf") != KDF_ALGORITHM:
            errors.append("Manifest KDF algorithm is invalid.")
        if cipher.get("ciphertext_file") != CIPHERTEXT_FILENAME:
            errors.append("Manifest ciphertext filename is invalid.")
        if not isinstance(cipher.get("ciphertext_bytes"), int) or cipher.get(
            "ciphertext_bytes"
        ) < 1:
            errors.append("Manifest ciphertext byte count is invalid.")
        for field in ("salt_b64", "nonce_b64", "tag_b64", "ciphertext_sha256"):
            if not isinstance(cipher.get(field), str) or not cipher.get(field):
                errors.append(f"Manifest {field} is required.")

    commitments = manifest.get("commitments")
    if not isinstance(commitments, Mapping):
        errors.append("Manifest commitments must be an object.")
    else:
        if set(commitments) != {
            "payload_sha256",
            "aad_sha256",
            "manifest_sha256",
        }:
            errors.append("Manifest commitments contain an unknown field.")
        for field in ("payload_sha256", "aad_sha256", "manifest_sha256"):
            value = commitments.get(field)
            if not isinstance(value, str) or len(value) != 64:
                errors.append(f"Manifest {field} must be a SHA-256 hex digest.")
        if commitments.get("manifest_sha256") != manifest_sha256(manifest):
            errors.append("Manifest SHA-256 commitment does not reproduce.")
    return errors


def manifest_sha256(manifest: Mapping[str, Any]) -> str:
    """Hash a manifest with its self-commitment field blanked."""

    body = deepcopy(dict(manifest))
    commitments = body.get("commitments")
    if isinstance(commitments, dict):
        commitments["manifest_sha256"] = ""
    return _sha256_bytes(_canonical_json_bytes(body))


def validate_unlock_audit(audit_path: str | Path) -> list[str]:
    """Validate sequence, previous-event links, and every audit event hash."""

    path = Path(audit_path)
    if not path.exists():
        return []
    errors: list[str] = []
    previous: str | None = None
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"Unlock audit line {index} is not JSON.")
            continue
        if not isinstance(record, dict):
            errors.append(f"Unlock audit line {index} must be an object.")
            continue
        if record.get("sequence") != index:
            errors.append(f"Unlock audit line {index} has a non-append sequence.")
        if record.get("previous_event_hash") != previous:
            errors.append(f"Unlock audit line {index} breaks the hash chain.")
        event_hash = record.get("event_hash")
        hash_body = dict(record)
        hash_body.pop("event_hash", None)
        if event_hash != _sha256_bytes(_canonical_json_bytes(hash_body)):
            errors.append(f"Unlock audit line {index} event_hash does not reproduce.")
        previous = str(event_hash or "")
    return errors


def scan_for_secret_material(
    paths: Sequence[str | Path],
    *,
    secret: bytes | None,
    additional_blobs: Mapping[str, bytes | str] | None = None,
) -> list[str]:
    """Return locations containing raw or commonly serialized key material."""

    validated_key = _validated_key(secret)
    variants = {
        validated_key,
        validated_key.hex().encode("ascii"),
        base64.b64encode(validated_key),
        base64.urlsafe_b64encode(validated_key),
        base64.b64encode(validated_key).rstrip(b"="),
        base64.urlsafe_b64encode(validated_key).rstrip(b"="),
    }
    hits: list[str] = []
    files: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            files.extend(candidate for candidate in path.rglob("*") if candidate.is_file())
        elif path.is_file():
            files.append(path)
    for path in files:
        content = path.read_bytes()
        if any(variant and variant in content for variant in variants):
            hits.append(str(path))
    for label, blob in (additional_blobs or {}).items():
        content = blob.encode("utf-8") if isinstance(blob, str) else blob
        if any(variant and variant in content for variant in variants):
            hits.append(str(label))
    return hits


def _append_unlock_audit(
    audit_path: str | Path,
    *,
    manifest: Mapping[str, Any],
    timestamp_utc: str | None,
) -> dict[str, Any]:
    path = Path(audit_path)
    existing_errors = validate_unlock_audit(path)
    if existing_errors:
        raise SealingIntegrityError("Existing unlock audit is invalid: " + " ".join(existing_errors))
    existing_lines = (
        path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    )
    previous = json.loads(existing_lines[-1])["event_hash"] if existing_lines else None
    record: dict[str, Any] = {
        "audit_schema_version": AUDIT_SCHEMA_VERSION,
        "sequence": len(existing_lines) + 1,
        "event_type": "synthetic_fixture_unlock",
        "timestamp_utc": timestamp_utc
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "package_id": manifest["package_id"],
        "manifest_sha256": manifest["commitments"]["manifest_sha256"],
        "ciphertext_sha256": manifest["cipher"]["ciphertext_sha256"],
        "payload_sha256": manifest["commitments"]["payload_sha256"],
        "fixture_only": True,
        "confirmatory_eligible": False,
        "sealed_materialized": False,
        "previous_event_hash": previous,
    }
    record["event_hash"] = _sha256_bytes(_canonical_json_bytes(record))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record


def _validated_key(key: bytes | None) -> bytes:
    if key is None:
        raise SealingKeyError("A 32-byte external key is required.")
    if not isinstance(key, bytes):
        raise SealingKeyError("The external key must be bytes.")
    if len(key) != KEY_BYTES:
        raise SealingKeyError(f"The external key must contain exactly {KEY_BYTES} bytes.")
    return key


def _derive_keys(key: bytes, salt: bytes) -> tuple[bytes, bytes]:
    pseudorandom_key = hmac.digest(salt, key, "sha256")
    return (
        hmac.digest(pseudorandom_key, b"egma-fixture-encryption-v1", "sha256"),
        hmac.digest(pseudorandom_key, b"egma-fixture-authentication-v1", "sha256"),
    )


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    blocks: list[bytes] = []
    counter = 0
    produced = 0
    while produced < length:
        blocks.append(hmac.digest(key, nonce + counter.to_bytes(8, "big"), "sha256"))
        produced += len(blocks[-1])
        counter += 1
    return b"".join(blocks)[:length]


def _xor_bytes(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right, strict=True))


def _public_header(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": manifest["schema_version"],
        "package_id": manifest["package_id"],
        "payload_contract": manifest["payload_contract"],
        "fixture_only": manifest["fixture_only"],
        "confirmatory_eligible": manifest["confirmatory_eligible"],
        "sealed_materialized": manifest["sealed_materialized"],
        "approved_metadata": manifest["approved_metadata"],
    }


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SealingIntegrityError(f"Manifest could not be loaded: {exc}") from exc
    if not isinstance(payload, dict):
        raise SealingIntegrityError("Manifest must contain one JSON object.")
    return payload


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _b64(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


def _decode_b64(value: str, label: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise SealingIntegrityError(f"Manifest {label} is not valid base64.") from exc
