from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.cello21_adapter import (
    AUTHORITY_ENV,
    Cello21AdapterError,
    QUALIFIED_IMAGE_DIGEST,
    QUALIFIED_SOURCE_COMMIT,
    permutation_preflight,
    read_git_head,
    require_execution_authority,
    validate_qualified_toolchain_identity,
)
from tools.cello21_artifact_parser import Cello21SummaryParser
from tools.cello_wrapper import (
    CELLO21_NATIVE_SUFFIXES,
    CELLO21_PREFLIGHT_CLAIM_LEVEL,
    CELLO21_PREFLIGHT_MAPPING_STATUS,
    CelloWrapper,
    _external_success_claims,
)
from tools.topology_selection import is_successfully_mapped


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _constraint_files(root: Path) -> tuple[Path, Path, Path]:
    ucf = root / "Bth.UCF.json"
    inputs = root / "Bth.input.json"
    outputs = root / "Bth.output.json"
    _write_json(
        ucf,
        [
            {"collection": "gates", "name": "g1", "group": "A"},
            {"collection": "gates", "name": "g2", "group": "B"},
            {"collection": "gates", "name": "g3", "group": "C"},
        ],
    )
    _write_json(
        inputs,
        [
            {"collection": "input_sensors", "name": "in1"},
            {"collection": "input_sensors", "name": "in2"},
            {"collection": "input_sensors", "name": "in3"},
        ],
    )
    _write_json(
        outputs,
        [
            {"collection": "output_devices", "name": "out1"},
            {"collection": "output_devices", "name": "out2"},
        ],
    )
    return ucf, inputs, outputs


def test_permutation_preflight_matches_cello21_formula(tmp_path: Path) -> None:
    ucf, inputs, outputs = _constraint_files(tmp_path)
    result = permutation_preflight(
        ucf_path=ucf,
        input_path=inputs,
        output_path=outputs,
        required_inputs=2,
        required_outputs=1,
        required_gates=2,
        max_permutations=100,
    )
    assert result["permutation_count"] == 3 * 2 * 2 * 3 * 2
    assert result["passed"] is True
    blocked = permutation_preflight(
        ucf_path=ucf,
        input_path=inputs,
        output_path=outputs,
        required_inputs=2,
        required_outputs=1,
        required_gates=2,
        max_permutations=71,
    )
    assert blocked["permutation_count"] == 72
    assert blocked["passed"] is False


def test_execution_authority_is_double_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(AUTHORITY_ENV, raising=False)
    with pytest.raises(Cello21AdapterError, match="Execution is disabled"):
        require_execution_authority(execute=False, experiment_id="EXP-1")
    with pytest.raises(Cello21AdapterError, match="Fail-closed"):
        require_execution_authority(execute=True, experiment_id="EXP-1")
    monkeypatch.setenv(AUTHORITY_ENV, "EXP-2")
    with pytest.raises(Cello21AdapterError, match="Fail-closed"):
        require_execution_authority(execute=True, experiment_id="EXP-1")
    monkeypatch.setenv(AUTHORITY_ENV, "EXP-1")
    require_execution_authority(execute=True, experiment_id="EXP-1")


def test_source_identity_file_supports_gitless_container_context(
    tmp_path: Path,
) -> None:
    identity = "f5b664422ecb051f244724289e33bb596817c278"
    (tmp_path / "CELLO21_SOURCE_COMMIT").write_text(identity + "\n", encoding="utf-8")
    assert read_git_head(tmp_path) == identity
    (tmp_path / "CELLO21_SOURCE_COMMIT").write_text("not-a-commit\n", encoding="utf-8")
    with pytest.raises(Cello21AdapterError, match="source identity"):
        read_git_head(tmp_path)


def test_qualified_toolchain_identity_is_exact_and_fail_closed() -> None:
    validate_qualified_toolchain_identity(
        source_commit=QUALIFIED_SOURCE_COMMIT,
        image_digest=QUALIFIED_IMAGE_DIGEST,
    )
    with pytest.raises(Cello21AdapterError, match="qualified Cello 2.1 R1 revision"):
        validate_qualified_toolchain_identity(
            source_commit="0" * 40,
            image_digest=QUALIFIED_IMAGE_DIGEST,
        )
    with pytest.raises(Cello21AdapterError, match="qualified Cello 2.1 R1 image"):
        validate_qualified_toolchain_identity(
            source_commit=QUALIFIED_SOURCE_COMMIT,
            image_digest="sha256:" + "0" * 64,
        )


def _valid_fixture(root: Path) -> Path:
    native = root / "output" / "candidate"
    native.mkdir(parents=True)
    verilog = root / "candidate_0.v"
    ucf, inputs, outputs = _constraint_files(root)
    verilog.write_text(
        "module and2(input A,B,output Y); assign Y=A&B; endmodule\n", encoding="utf-8"
    )
    yosys = native / "candidate_Bth.UCF_yosys.json"
    activity = native / "candidate_Bth.UCF_activity-table.csv"
    score = native / "candidate_Bth.UCF_circuit-score.csv"
    yosys.write_text('{"modules": {}}\n', encoding="utf-8")
    activity.write_text("Scores...\nA,1.0\n", encoding="utf-8")
    score.write_text("circuit_score,2.5\n", encoding="utf-8")

    def identity(path: Path) -> dict[str, object]:
        return {
            "filename": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": _sha(path),
        }

    def artifact(path: Path) -> dict[str, object]:
        return {
            "relative_path": path.relative_to(native).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": _sha(path),
        }

    summary = {
        "schema_version": "cello21.mapping-summary.v1",
        "adapter": {"name": "cello21_noninteractive", "version": "1.0"},
        "experiment": {"experiment_id": "C21-MAP-AND2-2026-08-20-R1"},
        "toolchain": {
            "name": "Cello-v2-1-Core",
            "source_commit": QUALIFIED_SOURCE_COMMIT,
            "image_digest": QUALIFIED_IMAGE_DIGEST,
        },
        "search": {
            "search_mode": "exhaustive",
            "permutation_count": 72,
            "max_permutations": 50000,
            "passed": True,
        },
        "inputs": {
            "verilog": identity(verilog),
            "ucf": identity(ucf),
            "input": identity(inputs),
            "output": identity(outputs),
        },
        "mapping": {
            "status": "MAPPING_PASS",
            "score": 2.5,
            "assignments": {
                "inputs": [
                    {"role": "input", "logic_node_id": "A", "part_id": "in1"},
                    {"role": "input", "logic_node_id": "B", "part_id": "in2"},
                ],
                "gates": [
                    {
                        "role": "gate",
                        "logic_node_id": "g0",
                        "part_id": "g1",
                        "gate_group": "A",
                        "gate_type": "NOR",
                    }
                ],
                "outputs": [
                    {"role": "output", "logic_node_id": "Y", "part_id": "out1"}
                ],
            },
        },
        "artifacts": {
            "mapping_pass": True,
            "export_pass": False,
            "mapping_required": [artifact(yosys), artifact(activity), artifact(score)],
            "missing_export_suffixes": ["_eugene.eug"],
        },
    }
    summary_path = native / "cello21_mapping_summary.json"
    _write_json(summary_path, summary)
    return summary_path


def test_parser_accepts_hash_bound_mapping_and_keeps_export_separate(
    tmp_path: Path,
) -> None:
    _valid_fixture(tmp_path)
    result = Cello21SummaryParser().parse_directory(tmp_path)
    assert result.assignment_provenance == "cello21_mapping_summary"
    assert result.metadata["validated"] is True
    assert result.metadata["export_pass"] is False
    assert {row["assignment_role"] for row in result.assignments} == {
        "input",
        "gate",
        "output",
    }


def test_parser_rejects_tampered_native_artifact(tmp_path: Path) -> None:
    summary_path = _valid_fixture(tmp_path)
    native = summary_path.parent
    next(native.glob("*_activity-table.csv")).write_text(
        "Scores...\nA,9.9\n", encoding="utf-8"
    )
    result = Cello21SummaryParser().parse_directory(tmp_path)
    assert result.assignments == []
    assert any("hash mismatch" in warning for warning in result.warnings)


def test_parser_rejects_unseeded_annealing_summary(tmp_path: Path) -> None:
    summary_path = _valid_fixture(tmp_path)
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    payload["search"]["search_mode"] = "annealing"
    _write_json(summary_path, payload)
    result = Cello21SummaryParser().parse_directory(tmp_path)
    assert result.assignments == []
    assert any("exhaustive" in warning for warning in result.warnings)


def test_wrapper_selects_cello21_parser_without_changing_legacy_default() -> None:
    legacy = CelloWrapper()
    cello21 = CelloWrapper(cello_artifact_format="cello21")
    assert legacy.artifact_parser.name == "cello_v2_json"
    assert cello21.artifact_parser.name == "cello21_mapping_summary"
    assert cello21.required_native_suffixes == CELLO21_NATIVE_SUFFIXES


def test_cello21_success_claims_remain_preflight_only() -> None:
    claims = _external_success_claims("cello21")
    assert claims["mapping_status"] == CELLO21_PREFLIGHT_MAPPING_STATUS
    assert claims["cello_claim_level"] == CELLO21_PREFLIGHT_CLAIM_LEVEL
    assert claims["cello_buildable"] is False
    assert "does not establish mapping success" in claims["cello_warning"]
    assert is_successfully_mapped({"cello_mode": "external", **claims}) is False


def test_legacy_cello_v2_success_claims_are_unchanged() -> None:
    claims = _external_success_claims("cello_v2")
    assert claims["mapping_status"] == "mapped"
    assert claims["cello_claim_level"] == "externally_mapped"
    assert claims["cello_buildable"] is True
    assert is_successfully_mapped({"cello_mode": "external", **claims}) is True
