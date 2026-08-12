from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shlex
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from benchmark_suite.cello_constraint_evaluator import evaluate_cello_constraints
from schemas.state import DesignState
from tools.cello_artifact_parser import CelloV2JsonParser, load_cello_json_payload
from tools.part_library import PartLibrary


STRICT_NATIVE_SUFFIXES = (
    "_logic.csv",
    "_activity.csv",
    "_toxicity.csv",
    "_outputNetlist.json",
    "_eugeneScript.eug",
    ".xml",
)


def _split_command_string(command: str, *, windows: bool | None = None) -> list[str]:
    """Split a configured command without treating Windows backslashes as escapes."""
    use_windows_rules = os.name == "nt" if windows is None else windows
    if not use_windows_rules:
        return shlex.split(command)

    parts = shlex.split(command, posix=False)
    return [
        part[1:-1]
        if len(part) >= 2 and part[0] == part[-1] and part[0] in {'"', "'"}
        else part
        for part in parts
    ]


class CelloWrapper:
    def __init__(
        self,
        cello_command: str | list[str] | None = None,
        ucf_path: str | None = None,
        work_dir: str | Path | None = None,
        artifact_dir: str | Path | None = None,
        part_library_path: str | Path | None = None,
        timeout_seconds: int = 120,
        max_log_chars: int = 4000,
        sensor_path: str | None = None,
        device_path: str | None = None,
        external_required: bool = False,
        required_native_suffixes: tuple[str, ...] = STRICT_NATIVE_SUFFIXES,
        attempt_budget: Any | None = None,
        semantic_validator: Any | None = None,
    ):
        self.cello_command = cello_command
        self.ucf_path = ucf_path
        self.sensor_path = sensor_path
        self.device_path = device_path
        self.work_dir = Path(work_dir) if work_dir else None
        self.artifact_dir = (
            Path(artifact_dir) if artifact_dir else Path("outputs") / "cello_artifacts"
        )
        self.part_library = (
            PartLibrary.from_json(part_library_path)
            if part_library_path
            else PartLibrary.demo()
        )
        self.artifact_parser = CelloV2JsonParser(self.part_library)
        self.timeout_seconds = timeout_seconds
        self.max_log_chars = max_log_chars
        self.external_required = external_required
        self.required_native_suffixes = tuple(required_native_suffixes)
        self.attempt_budget = attempt_budget
        self.semantic_validator = semantic_validator

    def run(self, state: DesignState) -> DesignState:
        node = (
            state.tree_nodes.get(state.current_node_id)
            if state.current_node_id
            else None
        )
        codes = node.verilog_codes if node else state.verilog_codes
        valid_codes = [code for code in codes if code and not code.startswith("ERROR:")]
        if not valid_codes:
            state.last_error = "ERROR: CelloWrapper received no valid Verilog."
            return state

        if self.external_required:
            missing = [
                label
                for label, value in (
                    ("cello_command", self.cello_command),
                    ("ucf_path", self.ucf_path),
                    ("sensor_path", self.sensor_path),
                    ("device_path", self.device_path),
                )
                if not value
            ]
            missing.extend(
                label
                for label, value in (
                    ("ucf_path", self.ucf_path),
                    ("sensor_path", self.sensor_path),
                    ("device_path", self.device_path),
                )
                if value and not Path(value).is_file()
            )
            if missing:
                state.last_error = (
                    "ERROR: external-required Cello configuration is incomplete: "
                    + ", ".join(sorted(set(missing)))
                )
                return state

        topologies = []
        for index, code in enumerate(valid_codes):
            validation_error = self._validate_verilog(index, code)
            if validation_error is not None:
                topologies.append(validation_error)
            elif self.external_required and self.semantic_validator is not None:
                semantic_result = self.semantic_validator(code)
                if semantic_result.get("passed") is not True:
                    topologies.append(
                        self._custom_failed_topology(
                            index,
                            code,
                            category="AND2_SEMANTIC_MISMATCH",
                            summary=str(
                                semantic_result.get("reason") or "AND2 contract failed."
                            ),
                            error_type="LOGIC_ERROR",
                        )
                    )
                else:
                    topology = self._run_external_cello(index, code)
                    topology["and2_semantic_evaluation"] = semantic_result
                    topologies.append(topology)
            elif self.cello_command is None:
                topologies.append(self._mock_topology(index, code))
            else:
                topologies.append(self._run_external_cello(index, code))

        proposals = node.logic_proposals if node else state.logic_proposals
        for i, topo in enumerate(topologies):
            copy_number = 1
            chassis = state.host_organism
            if proposals and i < len(proposals):
                try:
                    data = json.loads(proposals[i])
                    copy_number = int(data.get("copy_number", 1))
                    chassis = str(data.get("chassis") or state.host_organism)
                except Exception:
                    pass
            topo["copy_number"] = copy_number
            topo["chassis"] = chassis

        if node:
            node.candidate_topologies = topologies
        state.candidate_topologies = topologies
        state.last_error = None
        return state

    def _validate_verilog(self, index: int, code: str) -> dict[str, Any] | None:
        import re

        # 1. Sequential Logic Check
        # Check for sequential keywords: \bclk\b, \bclock\b, always\s*@, posedge, negedge
        if re.search(r"\bclk\b", code, re.IGNORECASE) or re.search(
            r"\bclock\b", code, re.IGNORECASE
        ):
            return self._custom_failed_topology(
                index,
                code,
                category="SEQUENTIAL_LOGIC_BLOCKED",
                summary="Sequential logic detected: 'clk' or 'clock' signal is not supported by combinational Cello mapping.",
                error_type="LOGIC_ERROR",
            )
        if (
            re.search(r"always\s*@", code)
            or re.search(r"\bposedge\b", code)
            or re.search(r"\bnegedge\b", code)
        ):
            return self._custom_failed_topology(
                index,
                code,
                category="SEQUENTIAL_LOGIC_BLOCKED",
                summary="Sequential logic detected: 'always @' or edge triggers are not supported by combinational Cello mapping.",
                error_type="LOGIC_ERROR",
            )

        # 2. UCF Capacity Check
        # Strip comments first to avoid false positives
        clean_code = re.sub(r"/\*[\s\S]*?\*/", "", code)
        clean_code = re.sub(r"//.*", "", clean_code)

        gate_keywords = r"\b(?:and|or|not|nand|nor|xor|xnor)\b"
        statements = re.findall(rf"{gate_keywords}[^;]*;", clean_code, re.IGNORECASE)
        gate_count = sum(stmt.count("(") for stmt in statements)

        if self.ucf_path and Path(self.ucf_path).exists():
            try:
                with open(self.ucf_path, "r", encoding="utf-8") as f:
                    ucf_data = json.load(f)
                if isinstance(ucf_data, list):
                    ucf_capacity = sum(
                        1
                        for item in ucf_data
                        if isinstance(item, dict) and item.get("collection") == "gates"
                    )
                    if gate_count > ucf_capacity:
                        return self._custom_failed_topology(
                            index,
                            code,
                            category="UCF_CAPACITY_EXCEEDED",
                            summary=f"Required gate count ({gate_count}) exceeds the available gates count ({ucf_capacity}) in UCF.",
                            error_type="LOGIC_ERROR",
                        )
            except Exception:
                pass
        return None

    def _custom_failed_topology(
        self,
        index: int,
        code: str,
        category: str,
        summary: str,
        error_type: str,
    ) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(dir=self.work_dir) as temp_dir:
            temp_path = Path(temp_dir)
            netlist_path = temp_path / f"candidate_{index}.v"
            netlist_path.write_text(code, encoding="utf-8")

            artifact_data = self._persist_artifacts(
                index=index,
                temp_path=temp_path,
                command=["early_validation_intercept"],
                status="early_validation_failed",
                stdout="",
                stderr=summary,
                return_code=-1,
            )

            topology = {
                "source": "external_cello_wrapper"
                if self.cello_command is not None
                else "mock_cello_wrapper",
                "cello_mode": "external" if self.cello_command is not None else "mock",
                "cello_claim_level": "external_mapping_failed"
                if self.cello_command is not None
                else "mock_failed",
                "cello_warning": f"Early validation intercept: {category}. Do not claim Cello assignment or buildability.",
                "verilog_index": index,
                "verilog": code,
                "score": 0.0,
                "mapping_status": category,
                "error_type": error_type,
                "orthogonality_score": 0.05,
                "cello_assignment_score": 0.0,
                "cello_buildable": False,
                "mapping_error_category": category,
                "mapping_error_summary": summary,
                "raw_error_log": summary,
                "return_code": -1,
                "ucf_path": self.ucf_path,
                **artifact_data,
            }
            topology.update(_topology_cello_metrics(topology))
            return topology

    def _mock_topology(self, index: int, code: str) -> dict[str, Any]:
        return {
            "source": "mock_cello_wrapper",
            "cello_mode": "mock",
            "cello_claim_level": "mock_only",
            "cello_warning": "Mock Cello output is only a workflow placeholder. Do not interpret it as real part mapping or buildability.",
            "verilog_index": index,
            "verilog": code,
            "score": 0.0,
            "mapping_status": "unmapped",
            "orthogonality_score": 1.0,
            "cello_assignment_score": 0.0,
            "cello_buildable": False,
        }

    def _run_external_cello(self, index: int, code: str) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(dir=self.work_dir) as temp_dir:
            temp_path = Path(temp_dir)
            netlist_path = temp_path / f"candidate_{index}.v"
            output_dir = temp_path / "output"
            output_dir.mkdir(exist_ok=True)
            netlist_path.write_text(code, encoding="utf-8")

            # Copy configuration files into temp directory for container mounting
            if self.ucf_path and Path(self.ucf_path).exists():
                shutil.copy(self.ucf_path, temp_path / "ucf.json")
            if self.sensor_path and Path(self.sensor_path).exists():
                shutil.copy(self.sensor_path, temp_path / "sensors.json")
            if self.device_path and Path(self.device_path).exists():
                shutil.copy(self.device_path, temp_path / "devices.json")

            command = self._build_command(index, temp_path, netlist_path, output_dir)
            if self.attempt_budget is not None:
                self.attempt_budget.consume_cello("cello")
            completed: subprocess.CompletedProcess[str] | None = None
            try:
                completed = subprocess.run(
                    command,
                    cwd=temp_path,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                stdout = _exception_stream_text(exc.stdout)
                stderr = _exception_stream_text(exc.stderr)
                artifact_data = self._persist_artifacts(
                    index=index,
                    temp_path=temp_path,
                    command=command,
                    status="timeout",
                    stdout=stdout,
                    stderr=stderr,
                    return_code=None,
                )
                return self._failed_topology(
                    index,
                    code,
                    "TIMEOUT",
                    f"Cello mapping timed out after {self.timeout_seconds} seconds.",
                    stdout + "\n" + stderr,
                    artifact_data=artifact_data,
                )
            except Exception as exc:
                artifact_data = self._persist_artifacts(
                    index=index,
                    temp_path=temp_path,
                    command=command,
                    status="wrapper_exception",
                    stdout="",
                    stderr=repr(exc),
                    return_code=None,
                )
                return self._failed_topology(
                    index,
                    code,
                    "WRAPPER_EXCEPTION",
                    f"Cello wrapper crashed before mapping: {exc}",
                    repr(exc),
                    artifact_data=artifact_data,
                )

            log = (
                f"STDOUT:\n{completed.stdout or ''}\nSTDERR:\n{completed.stderr or ''}"
            )
            if completed.returncode != 0:
                category = _classify_error_log(log)
                artifact_data = self._persist_artifacts(
                    index=index,
                    temp_path=temp_path,
                    command=command,
                    status="mapping_failed",
                    stdout=completed.stdout or "",
                    stderr=completed.stderr or "",
                    return_code=completed.returncode,
                )
                return self._failed_topology(
                    index,
                    code,
                    category,
                    _summarize_error(category, log),
                    log,
                    return_code=completed.returncode,
                    artifact_data=artifact_data,
                )

            artifact_data = self._persist_artifacts(
                index=index,
                temp_path=temp_path,
                command=command,
                status="mapped",
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
                return_code=completed.returncode,
            )
            parse_result = self.artifact_parser.parse_directory(
                artifact_data["cello_artifact_dir"],
                placements_only=self.external_required,
            )
            if self.external_required:
                strict_error = self._strict_external_success_error(
                    artifact_data,
                    parse_result,
                )
                if strict_error:
                    return self._failed_topology(
                        index,
                        code,
                        "INCOMPLETE_NATIVE_MAPPING",
                        strict_error,
                        log,
                        return_code=completed.returncode,
                        artifact_data=artifact_data,
                    )
            topology = {
                "source": "external_cello_wrapper",
                "cello_mode": "external",
                "cello_claim_level": "externally_mapped",
                "cello_warning": "External Cello completed. Buildability still depends on the selected UCF/library and expert review.",
                "verilog_index": index,
                "verilog": code,
                "score": 0.0,
                "mapping_status": "mapped",
                "orthogonality_score": 1.0,
                "cello_assignment_score": 0.0,
                "cello_buildable": True,
                "cello_stdout": _truncate_error_log(
                    completed.stdout or "", self.max_log_chars
                ),
                "ucf_path": self.ucf_path,
                "part_assignments": parse_result.assignments,
                "cello_parser": {
                    "name": parse_result.parser,
                    "version": parse_result.parser_version,
                    "source_files": parse_result.source_files,
                    "warnings": parse_result.warnings,
                    "assignment_provenance": parse_result.assignment_provenance,
                },
                "part_library": {
                    "library_id": self.part_library.library_id,
                    "version": self.part_library.version,
                    "evidence_level": self.part_library.evidence_level,
                    "source_path": self.part_library.source_path,
                },
                **artifact_data,
            }
            topology.update(_topology_cello_metrics(topology))
            return topology

    def _strict_external_success_error(
        self,
        artifact_data: dict[str, Any],
        parse_result: Any,
    ) -> str | None:
        artifact_root = Path(str(artifact_data["cello_artifact_dir"]))
        files = [path for path in artifact_root.rglob("*") if path.is_file()]
        missing_suffixes = [
            suffix
            for suffix in self.required_native_suffixes
            if not any(
                path.name.endswith(suffix) and path.stat().st_size > 0 for path in files
            )
        ]
        if missing_suffixes:
            return "Missing or empty required native artifacts: " + ", ".join(
                missing_suffixes
            )
        output_netlists = [
            path for path in files if path.name.endswith("_outputNetlist.json")
        ]
        output_netlist_payloads: dict[Path, Any] = {}
        try:
            for path in output_netlists:
                payload, _normalization = load_cello_json_payload(path)
                output_netlist_payloads[path.resolve()] = payload
        except (OSError, json.JSONDecodeError) as exc:
            return f"Native output netlist violates bounded Cello JSON policy: {exc}"
        expected_nodes: set[str] = set()
        for payload in output_netlist_payloads.values():
            expected_nodes.update(_expected_mapped_logic_nodes(payload))
        if not expected_nodes:
            return "Native output netlist exposes no mapped non-input logic nodes."
        assignments = list(parse_result.assignments)
        if not assignments:
            return "No placements-derived native Cello device assignments were parsed."
        if parse_result.assignment_provenance != "output_netlist_placements":
            return "Native assignments must be derived from output-netlist placements."
        source_files = [Path(path) for path in parse_result.source_files]
        if not source_files or any(
            not path.name.endswith("_outputNetlist.json") for path in source_files
        ):
            return "Assignments must originate only from native _outputNetlist.json artifacts."
        native_sources = set(output_netlist_payloads)
        if any(path.resolve() not in native_sources for path in source_files):
            return (
                "Assignment source is not one of the persisted native output netlists."
            )
        allowed_parts = self._allowed_native_part_ids()
        if not allowed_parts:
            return "Locked UCF/sensor/device inputs expose no allowable native part identifiers."
        for assignment in assignments:
            logic_node_id = str(assignment.get("logic_node_id") or "").strip()
            part_id = str(assignment.get("part_id") or "").strip()
            evidence_source = Path(str(assignment.get("evidence_source") or ""))
            if not logic_node_id or not part_id:
                return "Every native assignment requires non-empty logic_node_id and part_id."
            if assignment.get("assignment_provenance") != "output_netlist_placements":
                return (
                    "Every native assignment must carry placements-derived provenance."
                )
            if part_id not in allowed_parts:
                return f"Native assignment references a part outside locked inputs: {part_id}"
            if not evidence_source.name.endswith("_outputNetlist.json"):
                return "Assignment provenance is not a native output netlist."
            if evidence_source.resolve() not in native_sources:
                return "Assignment evidence source is outside persisted native output netlists."
        assigned_nodes = {
            str(assignment.get("logic_node_id") or "").strip()
            for assignment in assignments
        }
        if assigned_nodes != expected_nodes:
            return (
                "Native assignment logic-node coverage mismatch: "
                f"expected={sorted(expected_nodes)}, assigned={sorted(assigned_nodes)}"
            )
        return None

    def _allowed_native_part_ids(self) -> set[str]:
        identifiers: set[str] = set()
        for value in (self.ucf_path, self.sensor_path, self.device_path):
            if not value:
                continue
            try:
                payload = json.loads(Path(value).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            identifiers.update(_collect_locked_identifiers(payload))
        return identifiers

    def _build_command(
        self,
        index: int,
        temp_path: Path,
        netlist_path: Path,
        output_dir: Path,
    ) -> list[str]:
        if isinstance(self.cello_command, str):
            command = _split_command_string(self.cello_command)
        else:
            command = list(self.cello_command or [])

        wsl_temp_dir = _to_wsl_path(temp_path)
        wsl_output_dir = _to_wsl_path(output_dir)
        wsl_netlist = _to_wsl_path(netlist_path)

        local_ucf = temp_path / "ucf.json" if self.ucf_path else None
        wsl_ucf = _to_wsl_path(local_ucf) if local_ucf else ""

        local_sensor = temp_path / "sensors.json" if self.sensor_path else None
        wsl_sensor = _to_wsl_path(local_sensor) if local_sensor else ""

        local_device = temp_path / "devices.json" if self.device_path else None
        wsl_device = _to_wsl_path(local_device) if local_device else ""

        expanded: list[str] = []
        for part in command:
            val = (
                part.replace("{input_netlist}", str(netlist_path))
                .replace("{wsl_input_netlist}", wsl_netlist)
                .replace("{output_dir}", str(output_dir))
                .replace("{wsl_output_dir}", wsl_output_dir)
                .replace("{ucf_path}", str(local_ucf) if local_ucf else "")
                .replace("{wsl_ucf_path}", wsl_ucf)
                .replace("{sensor_path}", str(local_sensor) if local_sensor else "")
                .replace("{wsl_sensor_path}", wsl_sensor)
                .replace("{device_path}", str(local_device) if local_device else "")
                .replace("{wsl_device_path}", wsl_device)
                .replace("{temp_dir}", str(temp_path))
                .replace("{wsl_temp_dir}", wsl_temp_dir)
                .replace("{candidate_filename}", netlist_path.name)
                .replace("{index}", str(index))
            )
            expanded.append(val)
        return expanded

    def _failed_topology(
        self,
        index: int,
        code: str,
        category: str,
        summary: str,
        raw_log: str,
        return_code: int | None = None,
        artifact_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        topology = {
            "source": "external_cello_wrapper",
            "cello_mode": "external",
            "cello_claim_level": "external_mapping_failed",
            "cello_warning": "External Cello was attempted but mapping failed. Do not claim Cello assignment or buildability.",
            "verilog_index": index,
            "verilog": code,
            "score": 0.0,
            "mapping_status": "MAPPING_FAILED",
            "error_type": "PART_ERROR",
            "orthogonality_score": 1.0,
            "cello_assignment_score": 0.0,
            "cello_buildable": False,
            "mapping_error_category": category,
            "mapping_error_summary": summary,
            "raw_error_log": _truncate_error_log(raw_log, self.max_log_chars),
            "return_code": return_code,
            "ucf_path": self.ucf_path,
            **(artifact_data or {}),
        }
        topology.update(_topology_cello_metrics(topology))
        return topology

    def _persist_artifacts(
        self,
        *,
        index: int,
        temp_path: Path,
        command: list[str],
        status: str,
        stdout: str,
        stderr: str,
        return_code: int | None,
    ) -> dict[str, Any]:
        created_at = datetime.now(timezone.utc)
        run_id = f"candidate_{index}_{created_at.strftime('%Y%m%dT%H%M%S%fZ')}_{uuid4().hex[:8]}"
        artifact_root = self.artifact_dir.resolve()
        artifact_root.mkdir(parents=True, exist_ok=True)
        persistent_dir = artifact_root / run_id

        (temp_path / "stdout.log").write_text(stdout, encoding="utf-8")
        (temp_path / "stderr.log").write_text(stderr, encoding="utf-8")
        shutil.copytree(temp_path, persistent_dir)

        manifest_path = persistent_dir / "artifact_manifest.json"
        file_entries = [
            _artifact_file_entry(path, persistent_dir)
            for path in sorted(persistent_dir.rglob("*"))
            if path.is_file() and path != manifest_path
        ]
        manifest = {
            "schema_version": "1.0",
            "run_id": run_id,
            "candidate_index": index,
            "created_at": created_at.isoformat(),
            "status": status,
            "command": command,
            "return_code": return_code,
            "ucf_path": str(Path(self.ucf_path).resolve()) if self.ucf_path else None,
            "artifact_root": str(persistent_dir.resolve()),
            "files": file_entries,
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return {
            "cello_artifact_dir": str(persistent_dir.resolve()),
            "cello_artifact_manifest_path": str(manifest_path.resolve()),
            "cello_artifact_manifest": manifest,
            "cello_artifacts": file_entries,
        }


def _topology_cello_metrics(topology: dict[str, Any]) -> dict[str, Any]:
    metrics = evaluate_cello_constraints(topology)
    return {
        "orthogonality_score": metrics["orthogonality_score"],
        "cello_assignment_score": metrics["cello_assignment_score"],
        "cello_assignment_raw_score": metrics["raw_assignment_score"],
        "cello_buildable": metrics["cello_buildable"],
        "toxicity": metrics["toxicity"],
        "toxicity_score": metrics["toxicity_score"],
        "cello_constraint_report": metrics,
    }


def _collect_locked_identifiers(payload: Any) -> set[str]:
    identifiers: set[str] = set()
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            if item.get("collection") == "parts":
                for key in ("name", "id", "part_id"):
                    value = item.get(key)
                    if isinstance(value, (str, int)) and str(value).strip():
                        identifiers.add(str(value).strip())
            elif item.get("collection") == "structures":
                for device in item.get("devices", []):
                    if not isinstance(device, dict):
                        continue
                    value = device.get("name")
                    if isinstance(value, (str, int)) and str(value).strip():
                        identifiers.add(str(value).strip())
    elif isinstance(payload, dict):
        if payload.get("collection") == "parts":
            for key in ("name", "id", "part_id"):
                value = payload.get(key)
                if isinstance(value, (str, int)) and str(value).strip():
                    identifiers.add(str(value).strip())
    return identifiers


def _expected_mapped_logic_nodes(payload: Any) -> set[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), list):
        return set()
    expected = set()
    for node in payload["nodes"]:
        if not isinstance(node, dict):
            continue
        name = str(node.get("name") or "").strip()
        node_type = str(node.get("nodeType") or node.get("type") or "").upper()
        if name and node_type not in {"PRIMARY_INPUT", "INPUT"}:
            expected.add(name)
    return expected


def _artifact_file_entry(path: Path, root: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    media_type, _ = mimetypes.guess_type(path.name)
    return {
        "relative_path": path.relative_to(root).as_posix(),
        "absolute_path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
        "media_type": media_type or "application/octet-stream",
    }


def _exception_stream_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _classify_error_log(log: str) -> str:
    lowered = log.lower()
    if "ucf" in lowered and any(
        token in lowered
        for token in ("missing", "mismatch", "incompatible", "constraint")
    ):
        return "UCF_INCOMPATIBLE"
    if any(token in lowered for token in ("syntax error", "parse error", "verilog")):
        return "VERILOG_SYNTAX_ERROR"
    if any(
        token in lowered
        for token in ("unsupported gate", "unsupported primitive", "cannot map")
    ):
        return "UNSUPPORTED_GATE"
    if any(
        token in lowered
        for token in ("part unavailable", "no gate", "not found in library")
    ):
        return "PART_UNAVAILABLE"
    if "timeout" in lowered:
        return "TIMEOUT"
    return "MAPPING_FAILED"


def _summarize_error(category: str, log: str) -> str:
    first_line = next((line.strip() for line in log.splitlines() if line.strip()), "")
    last_exception = next(
        (
            line.strip()
            for line in reversed(log.splitlines())
            if any(token in line.lower() for token in ("exception", "error", "failed"))
        ),
        "",
    )
    detail = last_exception or first_line or "Cello mapping failed."
    return f"{category}: {detail[:500]}"


def _truncate_error_log(log: str, max_chars: int = 4000) -> str:
    text = (log or "").strip()
    if len(text) <= max_chars:
        return text
    head_len = max(500, max_chars // 2)
    tail_len = max(500, max_chars - head_len - 120)
    head = text[:head_len].rstrip()
    tail = text[-tail_len:].lstrip()
    omitted = len(text) - len(head) - len(tail)
    return f"{head}\n\n... [truncated {omitted} chars of middle stack trace/log] ...\n\n{tail}"


def _to_wsl_path(path: Path | None) -> str:
    if not path:
        return ""
    path_str = str(path.resolve())
    try:
        res = subprocess.run(
            ["wsl", "wslpath", "-u", path_str],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    if len(path_str) >= 2 and path_str[1] == ":":
        drive = path_str[0].lower()
        subpath = path_str[2:].replace("\\", "/")
        return f"/mnt/{drive}{subpath}"
    return path_str.replace("\\", "/")
