from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from mcp_server.artifact_writer import (
    build_and2_runtime_input_paths,
    build_offline_provider_identity,
    build_toolchain_identity,
    hash_input_paths,
    validate_and2_toolchain_lock,
)
from schemas.and2_pilot import is_loopback_api_base


class PilotOptions(Protocol):
    compute_budget: int
    monte_carlo_samples: int
    enable_ode: bool
    enable_rag: bool
    enable_skill_extraction: bool
    output_dir: str | None
    cello_command: str | list[str] | None
    ucf_path: str | None
    sensor_path: str | None
    device_path: str | None
    cello_timeout_seconds: int
    provider_call_cap: int
    provider_lock_path: str | None
    toolchain_lock_path: str | None


@dataclass(frozen=True)
class PilotPreflightContext:
    configured_command: list[str]
    toolchain_identity: dict[str, Any]
    provider_identity: dict[str, Any]
    provider_input_paths: dict[str, str]
    input_paths: dict[str, str]
    frozen_input_sha256s: dict[str, str]


@dataclass(frozen=True)
class PilotPreflightResult:
    context: PilotPreflightContext
    errors: tuple[str, ...] = ()
    stage_id: str = "preflight"
    failure_category: str = "PILOT_CONFIGURATION_INVALID"

    @property
    def passed(self) -> bool:
        return not self.errors


def prepare_and2_pilot_preflight(
    options: PilotOptions,
    *,
    configured_command: list[str],
    repository_root: Path,
    resolved_model: str,
    resolved_api_base: str | None,
    resolved_api_key: str | None,
    progress_callback_present: bool,
    initial_state_present: bool,
) -> PilotPreflightResult:
    """Validate and freeze every input before any agent is constructed."""

    toolchain_identity = build_toolchain_identity(configured_command)
    provider_identity, provider_input_paths, provider_errors = (
        build_offline_provider_identity(
            resolved_api_base,
            resolved_model,
            options.provider_lock_path,
        )
    )
    provider_identity["api_key_present"] = bool(resolved_api_key)
    if not is_loopback_api_base(resolved_api_base):
        provider_identity["endpoint_class"] = "rejected"
    input_paths = build_and2_runtime_input_paths(
        repository_root,
        {
            "ucf": options.ucf_path,
            "sensor": options.sensor_path,
            "device": options.device_path,
            **provider_input_paths,
        },
        configured_command,
        toolchain_lock_path=options.toolchain_lock_path,
    )
    context = PilotPreflightContext(
        configured_command=configured_command,
        toolchain_identity=toolchain_identity,
        provider_identity=provider_identity,
        provider_input_paths=provider_input_paths,
        input_paths=input_paths,
        frozen_input_sha256s={},
    )
    errors = list(provider_errors)
    failure_category = "PILOT_CONFIGURATION_INVALID"
    if not resolved_model:
        errors.append("model_name is required")
    if options.compute_budget != 1:
        errors.append("compute_budget must be exactly 1")
    if options.cello_timeout_seconds != 300:
        errors.append("cello_timeout_seconds must be exactly 300")
    if options.provider_call_cap != 3:
        errors.append("provider_call_cap must be exactly 3")
    if options.monte_carlo_samples != 1:
        errors.append("monte_carlo_samples must be exactly 1")
    if not options.enable_ode:
        errors.append("enable_ode must be true")
    if options.enable_rag:
        errors.append("enable_rag must be false")
    if options.enable_skill_extraction:
        errors.append("enable_skill_extraction must be false")
    if progress_callback_present:
        errors.append("progress_callback must be omitted")
    if initial_state_present:
        errors.append("initial_state must be omitted")
    if not is_loopback_api_base(resolved_api_base):
        errors.append("api_base must resolve to localhost, 127.0.0.1, or ::1")
    if resolved_api_key:
        errors.append("api_key and provider API-key environment variables must be absent")
    if provider_identity.get("cost_evidence") != "qualified_offline_provider_lock":
        errors.append("a qualified offline provider lock is required")
    if not toolchain_identity["immutable_reference"]:
        errors.append("cello_command must contain an immutable sha256 image digest")
    executable_status = toolchain_identity.get("executable_status")
    if executable_status == "unreadable":
        errors.append(
            "cello_command executable is present but unreadable under host policy"
        )
        failure_category = "TOOLCHAIN_EXECUTABLE_UNREADABLE"
    elif executable_status in {"missing", "not_configured"}:
        errors.append("cello_command executable is missing")
        failure_category = "TOOLCHAIN_EXECUTABLE_MISSING"
    if not options.output_dir:
        errors.append("output_dir is required")
    for label, value in (
        ("cello_command", options.cello_command),
        ("ucf_path", options.ucf_path),
        ("sensor_path", options.sensor_path),
        ("device_path", options.device_path),
    ):
        if not value:
            errors.append(f"{label} is required")
    for label, value in (
        ("ucf_path", options.ucf_path),
        ("sensor_path", options.sensor_path),
        ("device_path", options.device_path),
    ):
        if value:
            try:
                is_file = Path(value).is_file()
            except OSError as exc:
                errors.append(f"{label} is unreadable: {type(exc).__name__}: {exc}")
            else:
                if not is_file:
                    errors.append(f"{label} must be an existing file")
    if not any(
        "must be an existing file" in error or "is unreadable" in error
        for error in errors
    ):
        errors.extend(validate_and2_toolchain_lock(toolchain_identity, input_paths))
    if errors:
        return PilotPreflightResult(
            context=context,
            errors=tuple(errors),
            failure_category=failure_category,
        )

    freeze_errors: list[str] = []
    frozen_hashes: dict[str, str] = {}
    try:
        frozen_hashes = hash_input_paths(input_paths, require_all=True)
        expected_provider_hashes = {
            "provider_lock": provider_identity.get("provider_lock_sha256"),
            "provider_runtime_executable": (
                provider_identity.get("verified_artifacts", {})
                .get("runtime_executable", {})
                .get("sha256")
            ),
            "provider_model_artifact": (
                provider_identity.get("verified_artifacts", {})
                .get("model_artifact", {})
                .get("sha256")
            ),
        }
        for label, expected_hash in expected_provider_hashes.items():
            if expected_hash != frozen_hashes.get(label):
                freeze_errors.append(f"{label} changed after provider-lock validation")
        boundary_identity, boundary_paths, boundary_errors = (
            build_offline_provider_identity(
                resolved_api_base,
                resolved_model,
                options.provider_lock_path,
            )
        )
        freeze_errors.extend(boundary_errors)
        if boundary_paths != provider_input_paths:
            freeze_errors.append(
                "provider input paths changed after provider-lock validation"
            )
        if hash_input_paths(input_paths, require_all=True) != frozen_hashes:
            freeze_errors.append(
                "runtime inputs changed during the pre-construction freeze"
            )
        if not freeze_errors:
            provider_identity = boundary_identity
    except (OSError, ValueError) as exc:
        freeze_errors.append(f"pre-construction freeze failed: {exc}")
    frozen_context = PilotPreflightContext(
        configured_command=configured_command,
        toolchain_identity=toolchain_identity,
        provider_identity=provider_identity,
        provider_input_paths=provider_input_paths,
        input_paths=input_paths,
        frozen_input_sha256s=frozen_hashes,
    )
    if freeze_errors:
        return PilotPreflightResult(
            context=frozen_context,
            errors=tuple(freeze_errors),
            stage_id="preconstruction",
            failure_category="PROVIDER_SNAPSHOT_DRIFT",
        )
    return PilotPreflightResult(context=frozen_context)


def revalidate_and2_provider_snapshot(
    context: PilotPreflightContext,
    *,
    resolved_api_base: str | None,
    resolved_model: str,
    provider_lock_path: str | None,
) -> dict[str, Any]:
    """Recheck the frozen provider/runtime snapshot at agent construction."""

    identity, provider_paths, errors = build_offline_provider_identity(
        resolved_api_base,
        resolved_model,
        provider_lock_path,
    )
    current_hashes = hash_input_paths(context.input_paths, require_all=True)
    if (
        errors
        or provider_paths != context.provider_input_paths
        or current_hashes != context.frozen_input_sha256s
    ):
        details = errors or ["provider/runtime snapshot changed before agent construction"]
        raise RuntimeError("; ".join(details))
    return identity
