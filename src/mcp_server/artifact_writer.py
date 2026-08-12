from __future__ import annotations

import ast
import hashlib
import json
import platform
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mcp_server.serializers import summarize_state, summarize_topology, to_jsonable


DEFAULT_OUTPUT_DIR = Path("outputs") / "mcp_runs"


def create_run_dir(
    output_dir: str | Path | None = None, run_id: str | None = None
) -> Path:
    base_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    base_dir.mkdir(parents=True, exist_ok=True)
    selected_run_id = run_id or datetime.now(timezone.utc).strftime(
        "run_%Y%m%dT%H%M%SZ"
    )
    run_dir = base_dir / selected_run_id
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = base_dir / f"{selected_run_id}_{suffix}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(to_jsonable(payload), indent=2, ensure_ascii=False), encoding="utf-8"
    )


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def write_state_artifacts(
    state: Any, run_dir: Path, charts: list[Path] | None = None
) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    full_state_path = run_dir / "state.json"
    summary_path = run_dir / "summary.json"
    topology_path = run_dir / "best_topology.json"
    verilog_path = run_dir / "best_design.v"
    markdown_path = run_dir / "run_summary.md"

    write_json(full_state_path, state)
    write_json(summary_path, summarize_state(state))
    write_json(topology_path, summarize_topology(state.best_topology))
    artifacts["state_json"] = str(full_state_path.resolve())
    artifacts["summary_json"] = str(summary_path.resolve())
    artifacts["best_topology_json"] = str(topology_path.resolve())

    best_verilog = ""
    if state.best_topology:
        best_verilog = str(state.best_topology.get("verilog") or "")
    if not best_verilog and state.verilog_codes:
        best_verilog = str(state.verilog_codes[0])
    if best_verilog:
        write_text(verilog_path, best_verilog)
        artifacts["best_verilog"] = str(verilog_path.resolve())

    write_text(markdown_path, _summary_markdown(state))
    artifacts["run_summary_md"] = str(markdown_path.resolve())

    for chart in charts or []:
        artifacts[chart.stem] = str(chart.resolve())

    manifest_path = run_dir / "manifest.json"
    artifacts["manifest_json"] = str(manifest_path.resolve())
    write_json(manifest_path, _artifact_manifest(state, run_dir, artifacts))
    return artifacts


def write_and2_pilot_artifacts(
    state: Any,
    run_dir: Path,
    artifacts: dict[str, str],
    *,
    attempt_budget: Any,
    input_paths: dict[str, str],
    frozen_input_sha256s: dict[str, str] | None = None,
    toolchain_identity: dict[str, Any] | None = None,
    provider_identity: dict[str, Any] | None = None,
    case_id: str = "FINAL-CLOSEOUT-AND2-001",
) -> dict[str, str]:
    if (
        getattr(attempt_budget, "provider_calls", 0) > 0
        and (provider_identity or {}).get("cost_evidence")
        != "qualified_offline_provider_lock"
    ):
        raise ValueError("Provider calls lack qualified zero-cost evidence")
    entry_input_sha256s = hash_input_paths(input_paths, require_all=True)
    if frozen_input_sha256s is not None and entry_input_sha256s != frozen_input_sha256s:
        raise ValueError("AND2 pilot inputs changed after preflight hash freeze")
    node = (
        state.tree_nodes.get(state.current_node_id) if state.current_node_id else None
    )
    proposals = list(node.logic_proposals if node else state.logic_proposals)
    codes = list(node.verilog_codes if node else state.verilog_codes)
    topology = dict(state.best_topology or {})
    semantic = topology.get("and2_semantic_evaluation")
    ode_trace = topology.get("ode_trace")
    evaluator = topology.get("benchmark_report")
    cello_manifest = topology.get("cello_artifact_manifest_path")
    missing = []
    if state.is_completed is not True:
        missing.append("workflow_completed")
    if not proposals:
        missing.append("builder_logic_proposals")
    if len(codes) != 1 or not codes[0]:
        missing.append("single_generated_verilog")
    if not isinstance(semantic, dict) or semantic.get("passed") is not True:
        missing.append("semantic_evaluation")
    if not isinstance(ode_trace, dict) or not ode_trace:
        missing.append("ode_trace")
    if not isinstance(evaluator, dict) or not evaluator:
        missing.append("evaluator_result")
    if not cello_manifest or not Path(str(cello_manifest)).is_file():
        missing.append("cello_artifact_manifest")
    if (
        topology.get("mapping_status") != "mapped"
        or topology.get("cello_mode") != "external"
        or topology.get("cello_buildable") is not True
        or not topology.get("part_assignments")
    ):
        missing.append("external_mapping_acceptance")
    if missing:
        raise ValueError("AND2 pilot evidence is incomplete: " + ", ".join(missing))

    generated_dir = run_dir / "generated_verilog"
    ode_dir = run_dir / "ode"
    evaluation_dir = run_dir / "evaluation"
    generated_dir.mkdir(parents=True, exist_ok=True)
    ode_dir.mkdir(parents=True, exist_ok=True)
    evaluation_dir.mkdir(parents=True, exist_ok=True)
    builder_path = run_dir / "builder_logic_proposals.json"
    verilog_path = generated_dir / "candidate_0.v"
    semantic_path = run_dir / "semantic_evaluation.json"
    ode_path = ode_dir / "ode_trace.json"
    evaluator_path = evaluation_dir / "evaluator_result.json"
    closure_path = run_dir / "E3_RUN_MANIFEST.json"
    write_json(builder_path, {"case_id": case_id, "proposals": proposals})
    write_text(verilog_path, codes[0])
    write_json(semantic_path, semantic)
    write_json(ode_path, ode_trace)
    write_json(evaluator_path, evaluator)
    artifacts.update(
        {
            "builder_logic_proposals_json": str(builder_path.resolve()),
            "generated_verilog_candidate_0": str(verilog_path.resolve()),
            "semantic_evaluation_json": str(semantic_path.resolve()),
            "ode_trace_json": str(ode_path.resolve()),
            "evaluator_result_json": str(evaluator_path.resolve()),
            "cello_artifact_manifest_json": str(Path(str(cello_manifest)).resolve()),
        }
    )
    current_input_sha256s = hash_input_paths(input_paths, require_all=True)
    if (
        frozen_input_sha256s is not None
        and current_input_sha256s != frozen_input_sha256s
    ):
        raise ValueError("AND2 pilot inputs changed after preflight hash freeze")
    closure = {
        "schema_version": "and2-e3-run-manifest@1.0.0",
        "case_id": case_id,
        "status": "completed",
        "input_sha256s": current_input_sha256s,
        "output_sha256s": {
            key: _sha256_file(Path(path))
            for key, path in sorted(artifacts.items())
            if key != "manifest_json" and Path(path).is_file()
        },
        "attempt_budget": attempt_budget.to_dict(),
        "paid_cost_usd": float((provider_identity or {}).get("paid_cost_usd", 0.0)),
        "cost_control": (
            "qualified_offline_provider_lock"
            if (provider_identity or {}).get("cost_evidence")
            == "qualified_offline_provider_lock"
            else "zero_provider_calls_only"
        ),
        "toolchain_identity": dict(toolchain_identity or {}),
        "provider_identity": dict(provider_identity or {}),
        "claim_boundary": "Computational external-Cello mapping only; no biological or wet-lab claim.",
    }
    write_json(closure_path, closure)
    artifacts["e3_run_manifest_json"] = str(closure_path.resolve())
    write_json(run_dir / "manifest.json", _artifact_manifest(state, run_dir, artifacts))
    return artifacts


def write_pilot_failure_record(run_dir: Path, record: dict[str, Any]) -> Path:
    from schemas.and2_pilot import validate_failure_record

    errors = validate_failure_record(record)
    if errors:
        raise ValueError("Invalid AND2 pilot failure record: " + ", ".join(errors))
    path = run_dir / "pilot_failure_record.json"
    write_json(path, record)
    return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _probe_regular_file(path: Path) -> tuple[str, Path | None, str | None]:
    """Return a fail-closed file status without leaking host permission errors."""

    try:
        if not path.is_file():
            return "missing", None, None
        return "available", path.resolve(), None
    except OSError as exc:
        return "unreadable", None, f"{type(exc).__name__}: {exc}"


def hash_input_paths(
    input_paths: dict[str, str],
    *,
    require_all: bool,
) -> dict[str, str]:
    hashes: dict[str, str] = {}
    missing = []
    for key, value in sorted(input_paths.items()):
        path = Path(value)
        status, resolved_path, error = _probe_regular_file(path)
        if status != "available" or resolved_path is None:
            detail = f" ({error})" if error else ""
            missing.append(f"{key}:{path}{detail}")
            continue
        try:
            hashes[key] = _sha256_file(resolved_path)
        except OSError as exc:
            missing.append(f"{key}:{path} ({type(exc).__name__}: {exc})")
    if require_all and missing:
        raise ValueError("Missing frozen pilot inputs: " + ", ".join(missing))
    return hashes


def build_and2_runtime_input_paths(
    repository_root: Path,
    external_inputs: dict[str, str | None],
    command: list[str],
    *,
    toolchain_lock_path: str | None = None,
) -> dict[str, str]:
    roots = [
        repository_root / "src/mcp_server/service.py",
        repository_root / "src/tools/cello_wrapper.py",
        repository_root / "src/schemas/and2_pilot.py",
        repository_root / "src/workflows/reflexion_controller.py",
    ]
    discovered = _discover_local_python_dependencies(repository_root, roots)
    paths = {
        f"source:{path.relative_to(repository_root).as_posix()}": str(path)
        for path in sorted(discovered)
    }
    runtime_data_paths = [
        repository_root / "src/part_libraries/demo_cello_v1.json",
    ]
    for path in runtime_data_paths:
        if path.is_file():
            paths[f"runtime_data:{path.relative_to(repository_root).as_posix()}"] = str(
                path.resolve()
            )
    for key, value in external_inputs.items():
        if value:
            paths[key] = str(value)
    protocol_paths = {
        "toolchain_lock": (
            Path(toolchain_lock_path)
            if toolchain_lock_path
            else repository_root
            / "benchmark_suite/protocols/exp024_cello_toolchain_lock.json"
        ),
        "mapping_protocol": repository_root
        / "benchmark_suite/protocols/exp024_real_cello_mapping_protocol.json",
    }
    for label, candidate in protocol_paths.items():
        if candidate.is_file():
            paths[label] = str(candidate.resolve())
    if command:
        executable = Path(command[0])
        status, resolved_path, _ = _probe_regular_file(executable)
        if status == "available" and resolved_path is not None:
            paths["toolchain_executable"] = str(resolved_path)
    return dict(sorted(paths.items()))


def build_offline_provider_identity(
    api_base: str | None,
    model_name: str,
    provider_lock_path: str | None,
) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    identity: dict[str, Any] = {
        "model": model_name,
        "api_base": api_base,
        "endpoint_class": "rejected",
        "api_key_present": False,
        "cost_evidence": "unverified",
        "paid_cost_usd": 0.0,
    }
    input_paths: dict[str, str] = {}
    errors: list[str] = []
    if not provider_lock_path:
        return identity, input_paths, ["provider_lock_path is required"]
    lock_path = Path(provider_lock_path)
    lock_status, resolved_lock_path, lock_error = _probe_regular_file(lock_path)
    if lock_status != "available" or resolved_lock_path is None:
        detail = f": {lock_error}" if lock_error else ""
        return identity, input_paths, [
            f"provider_lock_path is {lock_status}{detail}"
        ]
    lock_path = resolved_lock_path
    input_paths["provider_lock"] = str(lock_path)
    try:
        lock_bytes = lock_path.read_bytes()
        lock = json.loads(lock_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return identity, input_paths, [f"provider lock is unreadable: {exc}"]
    lock_sha256 = hashlib.sha256(lock_bytes).hexdigest()
    if lock.get("schema_version") != "and2-offline-provider-lock@1.0.0":
        errors.append("provider lock schema_version is invalid")
    if lock.get("provider_type") != "local_offline_inference":
        errors.append("provider lock must declare local_offline_inference")
    try:
        hostname = (urlparse(str(api_base or "")).hostname or "").lower()
    except ValueError:
        hostname = ""
    if hostname not in {"localhost", "127.0.0.1", "::1"}:
        errors.append("provider api_base must be loopback")
    if str(lock.get("api_base") or "") != str(api_base or ""):
        errors.append("provider lock api_base does not match")
    if str(lock.get("model_name") or "") != str(model_name or ""):
        errors.append("provider lock model_name does not match")
    if lock.get("network_policy") != "offline_no_egress":
        errors.append("provider lock must declare offline_no_egress")
    if lock.get("remote_forwarding_allowed") is not False:
        errors.append("provider lock must forbid remote forwarding")
    paid_cap = lock.get("paid_cost_cap_usd")
    if (
        not isinstance(paid_cap, (int, float))
        or isinstance(paid_cap, bool)
        or paid_cap != 0
    ):
        errors.append("provider lock paid_cost_cap_usd must be exactly 0.0")

    verified_artifacts = {}
    for label in ("runtime_executable", "model_artifact"):
        entry = lock.get(label)
        if not isinstance(entry, dict):
            errors.append(f"provider lock {label} entry is required")
            continue
        path_value = str(entry.get("path") or "")
        expected_hash = str(entry.get("sha256") or "").lower()
        path = Path(path_value)
        artifact_status, resolved_artifact_path, artifact_error = _probe_regular_file(
            path
        )
        if artifact_status != "available" or resolved_artifact_path is None:
            detail = f": {artifact_error}" if artifact_error else ""
            errors.append(
                f"provider lock {label} path is {artifact_status}{detail}"
            )
            continue
        path = resolved_artifact_path
        try:
            artifact_bytes = path.read_bytes()
        except OSError as exc:
            errors.append(f"provider lock {label} path is unreadable: {exc}")
            continue
        actual_hash = hashlib.sha256(artifact_bytes).hexdigest()
        if (
            not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
            or actual_hash != expected_hash
        ):
            errors.append(f"provider lock {label} hash does not match")
            continue
        input_paths[f"provider_{label}"] = str(path.resolve())
        verified_artifacts[label] = {
            "path": str(path.resolve()),
            "sha256": actual_hash,
        }

    if not errors:
        identity.update(
            {
                "endpoint_class": "qualified_offline_loopback",
                "provider_type": "local_offline_inference",
                "network_policy": "offline_no_egress",
                "remote_forwarding_allowed": False,
                "cost_evidence": "qualified_offline_provider_lock",
                "provider_lock_sha256": lock_sha256,
                "verified_artifacts": verified_artifacts,
            }
        )
    return identity, dict(sorted(input_paths.items())), errors


def build_toolchain_identity(command: list[str]) -> dict[str, Any]:
    image_digests = sorted(
        {
            match.group(0).lower()
            for part in command
            for match in re.finditer(r"sha256:[0-9a-fA-F]{64}", part)
        }
    )
    executable_path = None
    executable_sha256 = None
    executable_status = "not_configured"
    executable_error = None
    if command:
        executable_status, executable, executable_error = _probe_regular_file(
            Path(command[0])
        )
        if executable_status == "available" and executable is not None:
            executable_path = str(executable)
            try:
                executable_sha256 = _sha256_file(executable)
                executable_status = "hashable"
            except OSError as exc:
                executable_status = "unreadable"
                executable_error = f"{type(exc).__name__}: {exc}"
    return {
        "command": list(command),
        "image_digests": image_digests,
        "executable_path": executable_path,
        "executable_sha256": executable_sha256,
        "executable_status": executable_status,
        "executable_error": executable_error,
        "immutable_reference": bool(image_digests),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
    }


def validate_and2_toolchain_lock(
    toolchain_identity: dict[str, Any],
    input_paths: dict[str, str],
) -> list[str]:
    errors = []
    lock_value = input_paths.get("toolchain_lock")
    if not lock_value or not Path(lock_value).is_file():
        return ["toolchain lock file is missing"]
    try:
        lock = json.loads(Path(lock_value).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"toolchain lock is unreadable: {exc}"]
    if lock.get("schema_version") != "exp024-cello-toolchain-lock@1.0.0":
        errors.append("toolchain lock schema_version is invalid")
    if lock.get("cello", {}).get("license_status") != "allowed":
        errors.append(
            "toolchain lock must record cello.license_status=allowed before execution"
        )
    expected_reference = str(lock.get("container", {}).get("image_reference") or "")
    expected_digest_match = re.search(r"sha256:[0-9a-fA-F]{64}", expected_reference)
    expected_digest = (
        expected_digest_match.group(0).lower() if expected_digest_match else ""
    )
    if not expected_digest or expected_digest not in toolchain_identity.get(
        "image_digests", []
    ):
        errors.append(
            "cello_command image digest does not match the qualified toolchain lock"
        )
    if not toolchain_identity.get("executable_sha256"):
        errors.append("cello_command executable must be an existing hashable file")
    command = list(toolchain_identity.get("command") or [])
    if expected_reference not in command:
        errors.append(
            "cello_command must contain the exact digest-pinned image reference "
            "from the qualified toolchain lock"
        )
    if "--network=none" not in command:
        errors.append("cello_command must use --network=none")
    if "--pull=never" not in command:
        errors.append("cello_command must use --pull=never")
    libraries = lock.get("libraries", {})
    for key, expected_key in (
        ("ucf", "ucf_sha256"),
        ("sensor", "input_sensor_sha256"),
        ("device", "output_device_sha256"),
    ):
        path_value = input_paths.get(key)
        expected_hash = str(libraries.get(expected_key) or "").lower()
        if not path_value or not Path(path_value).is_file():
            continue
        if not expected_hash or _sha256_file(Path(path_value)) != expected_hash:
            errors.append(f"{key} hash does not match the qualified toolchain lock")
    return errors


def validate_and2_pilot_bundle(run_dir: str | Path) -> dict[str, Any]:
    """Validate a completed or failed AND2 pilot bundle without modifying it."""

    root = Path(run_dir)
    success_path = root / "E3_RUN_MANIFEST.json"
    failure_path = root / "pilot_failure_record.json"
    errors: list[str] = []
    if success_path.exists() and failure_path.exists():
        errors.append("bundle contains both success and failure terminal records")
    if success_path.exists():
        verified = _validate_success_bundle(root, success_path, errors)
        bundle_kind = "success"
    elif failure_path.exists():
        verified = _validate_failure_bundle(failure_path, errors)
        bundle_kind = "failure"
    else:
        verified = 0
        bundle_kind = "unknown"
        errors.append("bundle has no terminal success or failure record")
    return {
        "status": "pass" if not errors else "fail",
        "bundle_kind": bundle_kind,
        "run_dir": str(root.resolve()),
        "verified_artifact_count": verified,
        "errors": errors,
    }


def _validate_success_bundle(
    run_dir: Path, success_path: Path, errors: list[str]
) -> int:
    closure = _read_json_object(success_path, "success manifest", errors)
    manifest = _read_json_object(run_dir / "manifest.json", "artifact manifest", errors)
    if closure.get("schema_version") != "and2-e3-run-manifest@1.0.0":
        errors.append("success manifest schema_version is invalid")
    if closure.get("status") != "completed":
        errors.append("success manifest status must be completed")
    if closure.get("paid_cost_usd") != 0 and closure.get("paid_cost_usd") != 0.0:
        errors.append("success manifest paid_cost_usd must be exactly 0.0")
    budget = closure.get("attempt_budget")
    if not isinstance(budget, dict):
        errors.append("success manifest attempt_budget must be an object")
    else:
        for used_key, cap_key, label in (
            ("provider_calls", "max_provider_calls", "provider call"),
            ("cello_subprocesses", "max_cello_subprocesses", "Cello subprocess"),
        ):
            used = budget.get(used_key)
            cap = budget.get(cap_key)
            if (
                not isinstance(used, int)
                or isinstance(used, bool)
                or used < 0
                or not isinstance(cap, int)
                or isinstance(cap, bool)
                or cap < 0
            ):
                errors.append(f"{label} budget values must be non-negative integers")
            elif used > cap:
                errors.append(f"{label} budget was exceeded")
    output_hashes = closure.get("output_sha256s")
    if not isinstance(output_hashes, dict):
        errors.append("success manifest output_sha256s must be an object")
        output_hashes = {}
    entries = manifest.get("artifacts")
    paths_by_key = {
        entry.get("key"): entry.get("path")
        for entry in entries
        if isinstance(entry, dict)
    } if isinstance(entries, list) else {}
    required = {
        "state_json",
        "summary_json",
        "best_topology_json",
        "builder_logic_proposals_json",
        "generated_verilog_candidate_0",
        "semantic_evaluation_json",
        "ode_trace_json",
        "evaluator_result_json",
        "cello_artifact_manifest_json",
    }
    missing = sorted(required - set(output_hashes))
    if missing:
        errors.append("success bundle is missing hashed artifacts: " + ", ".join(missing))
    verified = 0
    for key, expected_hash in sorted(output_hashes.items()):
        if not isinstance(expected_hash, str) or not re.fullmatch(
            r"[0-9a-f]{64}", expected_hash
        ):
            errors.append(f"output hash for {key} is invalid")
            continue
        path_value = paths_by_key.get(key)
        if not isinstance(path_value, str):
            errors.append(f"artifact manifest has no path for {key}")
            continue
        status, resolved_path, probe_error = _probe_regular_file(Path(path_value))
        if status != "available" or resolved_path is None:
            detail = f": {probe_error}" if probe_error else ""
            errors.append(f"artifact {key} is {status}{detail}")
            continue
        try:
            actual_hash = _sha256_file(resolved_path)
        except OSError as exc:
            errors.append(f"artifact {key} is unreadable: {type(exc).__name__}: {exc}")
            continue
        if actual_hash != expected_hash:
            errors.append(f"artifact hash mismatch: {key}")
            continue
        verified += 1
    semantic_path = paths_by_key.get("semantic_evaluation_json")
    if isinstance(semantic_path, str):
        semantic = _read_json_object(Path(semantic_path), "semantic evaluation", errors)
        if semantic.get("passed") is not True:
            errors.append("semantic evaluation did not pass")
    return verified


def _validate_failure_bundle(failure_path: Path, errors: list[str]) -> int:
    from schemas.and2_pilot import validate_failure_record

    record = _read_json_object(failure_path, "failure record", errors)
    errors.extend(validate_failure_record(record))
    return 1 if record and not errors else 0


def _read_json_object(
    path: Path, label: str, errors: list[str]
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"{label} is unreadable: {type(exc).__name__}: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"{label} must be a JSON object")
        return {}
    return payload


def _discover_local_python_dependencies(
    repository_root: Path,
    entry_paths: list[Path],
) -> set[Path]:
    pending = [path.resolve() for path in entry_paths]
    discovered: set[Path] = set()
    while pending:
        path = pending.pop()
        if path in discovered or not path.is_file():
            continue
        discovered.add(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            raise ValueError(
                f"Could not inspect runtime dependency {path}: {exc}"
            ) from exc
        for module_name in _imported_module_names(path, tree, repository_root):
            for dependency in _resolve_local_module(repository_root, module_name):
                if dependency not in discovered:
                    pending.append(dependency)
    return discovered


def _imported_module_names(
    path: Path,
    tree: ast.AST,
    repository_root: Path,
) -> set[str]:
    package = _module_package(path, repository_root)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base_parts = package.split(".") if package else []
            if node.level:
                keep = max(0, len(base_parts) - node.level + 1)
                base_parts = base_parts[:keep]
            else:
                base_parts = []
            if node.module:
                base_parts.extend(node.module.split("."))
            base = ".".join(base_parts)
            if base:
                names.add(base)
            for alias in node.names:
                if alias.name != "*" and base:
                    names.add(f"{base}.{alias.name}")
    return names


def _module_package(path: Path, repository_root: Path) -> str:
    source_root = repository_root / "src"
    try:
        relative = path.relative_to(source_root)
    except ValueError:
        relative = path.relative_to(repository_root)
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    else:
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_local_module(repository_root: Path, module_name: str) -> list[Path]:
    relative = Path(*module_name.split("."))
    found: set[Path] = set()
    for base in (repository_root / "src", repository_root):
        resolved_base = base.resolve()
        module_file = (base / relative).with_suffix(".py")
        package_file = base / relative / "__init__.py"
        if module_file.is_file():
            found.add(module_file.resolve())
        if package_file.is_file():
            found.add(package_file.resolve())
        for resolved_module in list(found):
            try:
                resolved_module.relative_to(resolved_base)
            except ValueError:
                continue
            parent = resolved_module.parent
            while parent != resolved_base:
                initializer = parent / "__init__.py"
                if initializer.is_file():
                    found.add(initializer.resolve())
                parent = parent.parent
    return sorted(found)


def _artifact_manifest(
    state: Any, run_dir: Path, artifacts: dict[str, str]
) -> dict[str, Any]:
    descriptions = {
        "state_json": ("json", "Full serialized design state."),
        "summary_json": ("json", "Agent-friendly summary of the design state."),
        "best_topology_json": ("json", "Best topology summary and benchmark details."),
        "best_verilog": ("verilog", "Best available Cello-compatible Verilog design."),
        "run_summary_md": ("markdown", "Human-readable run summary."),
        "manifest_json": (
            "json",
            "Manifest describing all artifacts written by this run.",
        ),
        "score_breakdown": ("image", "Score breakdown chart."),
        "ode_summary": ("image", "ODE simulation summary chart."),
    }
    artifact_entries = []
    for key, path in artifacts.items():
        artifact_type, description = descriptions.get(
            key, ("file", f"Generated artifact: {key}.")
        )
        artifact_entries.append(
            {
                "key": key,
                "path": path,
                "type": artifact_type,
                "description": description,
            }
        )
    return {
        "run_id": run_dir.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "user_intent": getattr(state, "user_intent", None),
        "host_organism": getattr(state, "host_organism", None),
        "artifacts": artifact_entries,
    }


def _summary_markdown(state: Any) -> str:
    best = summarize_topology(state.best_topology)
    lines = [
        "# MCP Genetic Circuit Run",
        "",
        f"- Intent: {state.user_intent}",
        f"- Host: {state.host_organism}",
        f"- Completed: {state.is_completed}",
        f"- Approved: {state.is_approved}",
        f"- Requires human input: {state.requires_human_input}",
        f"- Pause reason: {state.pause_reason or ''}",
        f"- Score: {best.get('score', '')}",
        f"- Mapping status: {best.get('mapping_status', '')}",
        f"- Cello mode: {best.get('cello_mode', '')}",
        f"- Cello claim level: {best.get('cello_claim_level', '')}",
        f"- Cello assignment score (normalized): {best.get('cello_assignment_score', '')}",
        f"- Cello assignment score (raw): {best.get('cello_assignment_raw_score', '')}",
        f"- Cello warning: {best.get('cello_warning', '')}",
        f"- ODE status: {best.get('ode_status', '')}",
        f"- Critic feedback: {state.latest_critic_feedback}",
        "",
    ]
    if state.human_feedback_prompt:
        lines.extend(["## Human Feedback Prompt", "", state.human_feedback_prompt, ""])
    if best.get("verilog"):
        lines.extend(["## Verilog", "", "```verilog", str(best["verilog"]), "```", ""])
    return "\n".join(lines)
