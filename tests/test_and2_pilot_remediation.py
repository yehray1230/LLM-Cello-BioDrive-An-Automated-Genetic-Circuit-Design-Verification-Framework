from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path

import pytest

from agents.builder_agent import call_builder
from mcp_server.artifact_writer import (
    build_and2_runtime_input_paths,
    build_offline_provider_identity,
    build_toolchain_identity,
    hash_input_paths,
    validate_and2_toolchain_lock,
    validate_and2_pilot_bundle,
    write_and2_pilot_artifacts,
    write_state_artifacts,
)
from mcp_server.service import design_circuit_quick
from schemas.and2_pilot import (
    AttemptBudgetExceeded,
    PilotAttemptBudget,
    validate_and2_verilog,
    validate_failure_record,
)
from schemas.state import DesignState
from tools.cello_wrapper import CelloWrapper
from tools.cello_artifact_parser import load_cello_json_payload


AND2_VERILOG = (
    "module and2(input A, input B, output GFP); assign GFP = A & B; endmodule"
)
LOCKED_IMAGE = (
    "docker.io/cidarlab/cello-dnacompiler@sha256:"
    "b84f2bf5b418238354acfb5114fcaa308f8d5b259bb91efced4cdad16f3c8cf5"
)
# Unit and integration rehearsals must not depend on a host Podman installation.
# The command is never executed in these tests; the current Python executable is
# an accessible, hashable stand-in for the toolchain identity contract.
LOCKED_COMMAND = [sys.executable, "run", "--pull=never", "--network=none", LOCKED_IMAGE]


def _external_inputs(tmp_path: Path) -> dict[str, str]:
    paths = {}
    for name in ("ucf", "sensor", "device"):
        path = tmp_path / f"{name}.json"
        path.write_text(
            json.dumps([{"collection": "parts", "name": "TetR"}]),
            encoding="utf-8",
        )
        paths[name] = str(path)
    return paths


def _locked_external_inputs(tmp_path: Path) -> dict[str, str]:
    inputs = _external_inputs(tmp_path)
    hashes = {
        key: hashlib.sha256(Path(path).read_bytes()).hexdigest()
        for key, path in inputs.items()
    }
    lock_path = tmp_path / "synthetic_toolchain_lock.json"
    lock_path.write_text(
        json.dumps(
            {
                "schema_version": "exp024-cello-toolchain-lock@1.0.0",
                "qualification_status": "synthetic_test_fixture_only",
                "container": {"image_reference": LOCKED_IMAGE},
                "cello": {
                    "license_status": "allowed",
                    "license_scope": "synthetic_test_fixture_no_external_cello",
                },
                "libraries": {
                    "ucf_sha256": hashes["ucf"],
                    "input_sensor_sha256": hashes["sensor"],
                    "output_device_sha256": hashes["device"],
                },
            }
        ),
        encoding="utf-8",
    )
    inputs["toolchain_lock"] = str(lock_path)
    return inputs


def _native_fixture_command(
    *,
    assignments: bool,
    part_id: str = "TetR",
    omit_suffix: str | None = None,
    logic_node_id: str = "GFP",
    extra_expected_node: str | None = None,
    fallback_key: str | None = None,
) -> list[str]:
    nodes = [{"name": logic_node_id, "nodeType": "PRIMARY_OUTPUT"}]
    if extra_expected_node:
        nodes.append({"name": extra_expected_node, "nodeType": "NOR"})
    payload = (
        {
            "nodes": nodes,
            "placements": [
                [
                    {
                        "components": [
                            {
                                "name": "fixture",
                                "node": logic_node_id,
                                "parts": [part_id],
                            }
                        ]
                    }
                ]
            ],
        }
        if assignments
        else {"nodes": nodes, "placements": []}
    )
    if fallback_key:
        generic_record = {
            "name": logic_node_id,
            "nodeType": "PRIMARY_OUTPUT",
            "logic_node_id": logic_node_id,
            "part_id": part_id,
        }
        payload = {"nodes": nodes, fallback_key: [generic_record]}
        if fallback_key == "nodes":
            payload = {"nodes": [generic_record]}
    entries = [
        ("candidate_0_logic.csv", "logic"),
        ("candidate_0_activity.csv", "activity"),
        ("candidate_0_toxicity.csv", "toxicity"),
        ("candidate_0_outputNetlist.json", payload),
        ("candidate_0_eugeneScript.eug", "eugene"),
        ("candidate_0.xml", "<xml/>"),
    ]
    if omit_suffix:
        entries = [entry for entry in entries if not entry[0].endswith(omit_suffix)]
    serialized = [
        (name, json.dumps(value) if isinstance(value, dict) else value)
        for name, value in entries
    ]
    script = (
        "import pathlib,sys;"
        "out=pathlib.Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);"
        f"[(out/name).write_text(text,encoding='utf-8') for name,text in {serialized!r}]"
    )
    return [sys.executable, "-c", script, "{output_dir}"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _offline_provider_lock(
    tmp_path: Path,
    *,
    model_name: str = "local",
    api_base: str = "http://127.0.0.1:1234",
) -> str:
    runtime = tmp_path / "offline-provider.exe"
    model = tmp_path / "offline-model.gguf"
    runtime.write_bytes(b"offline provider fixture")
    model.write_bytes(b"offline model fixture")
    lock = tmp_path / "offline-provider-lock.json"
    lock.write_text(
        json.dumps(
            {
                "schema_version": "and2-offline-provider-lock@1.0.0",
                "provider_type": "local_offline_inference",
                "api_base": api_base,
                "model_name": model_name,
                "network_policy": "offline_no_egress",
                "remote_forwarding_allowed": False,
                "paid_cost_cap_usd": 0.0,
                "runtime_executable": {
                    "path": str(runtime),
                    "sha256": _sha256(runtime),
                },
                "model_artifact": {
                    "path": str(model),
                    "sha256": _sha256(model),
                },
            }
        ),
        encoding="utf-8",
    )
    return str(lock)


@pytest.mark.parametrize(
    "verilog",
    [
        "module or2(input A,input B,output GFP); assign GFP=A|B; endmodule",
        "module inv(input A,input B,output GFP); nand g1(GFP,A,B); endmodule",
        "module renamed(input X,input B,output GFP); assign GFP=X&B; endmodule",
        "module missing(input A,output GFP); assign GFP=A; endmodule",
        "module seq(input A,input B,input clk,output reg GFP); always @(posedge clk) GFP<=A&B; endmodule",
        "module unresolved(input A,input B,output GFP); assign GFP=A&B|UNKNOWN; endmodule",
        "module undriven(input A,input B,output GFP); wire X; assign X=A&B; endmodule",
        "module unsupported(input A,input B,output GFP); parameter X=0; assign GFP=A&B; endmodule",
        "module vector(input [1:0] A,input B,output GFP); assign GFP=A&B; endmodule",
        "module self_cycle(input A,input B,output GFP); wire X; assign X=X; assign GFP=A&B; endmodule",
        "module two_cycle(input A,input B,output GFP); wire X; wire Y; assign X=Y; assign Y=X; assign GFP=A&B; endmodule",
    ],
)
def test_and2_semantic_contract_fails_closed(verilog: str) -> None:
    assert validate_and2_verilog(verilog)["passed"] is False


def test_and2_semantic_contract_accepts_exact_truth_table() -> None:
    result = validate_and2_verilog(AND2_VERILOG)

    assert result["passed"] is True
    assert result["truth_table_rows_checked"] == 4
    assert result["functional_score"] == 1.0


def test_cello_json_policy_only_normalizes_trailing_commas(tmp_path: Path) -> None:
    path = tmp_path / "candidate_outputNetlist.json"
    path.write_text(
        '{"label":"a,}","nodes":[{"name":"GFP",},],}',
        encoding="utf-8",
    )

    payload, normalization = load_cello_json_payload(path)

    assert normalization == "cello_trailing_commas_removed"
    assert payload == {"label": "a,}", "nodes": [{"name": "GFP"}]}


def test_cello_json_policy_rejects_root_level_comma_at_eof(tmp_path: Path) -> None:
    path = tmp_path / "candidate_outputNetlist.json"
    path.write_text('{"x":1},', encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        load_cello_json_payload(path)


def test_attempt_budget_blocks_second_cello_and_fourth_provider_call() -> None:
    budget = PilotAttemptBudget(max_provider_calls=3, max_cello_subprocesses=1)
    for stage in ("builder", "translator", "critic"):
        budget.consume_provider(stage)
    budget.consume_cello("cello")

    with pytest.raises(AttemptBudgetExceeded):
        budget.consume_provider("retry")
    with pytest.raises(AttemptBudgetExceeded):
        budget.consume_cello("retry")


def test_external_required_rejects_missing_configuration_before_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = DesignState(verilog_codes=[AND2_VERILOG])
    invoked = False

    def forbidden_run(*args, **kwargs):
        nonlocal invoked
        invoked = True
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr("tools.cello_wrapper.subprocess.run", forbidden_run)
    result = CelloWrapper(external_required=True).run(state)

    assert invoked is False
    assert "external-required" in str(result.last_error)


def test_and2_mismatch_stops_before_cello_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _external_inputs(tmp_path)
    state = DesignState(
        verilog_codes=[
            "module or2(input A,input B,output GFP); assign GFP=A|B; endmodule"
        ]
    )
    invoked = False

    def forbidden_run(*args, **kwargs):
        nonlocal invoked
        invoked = True
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr("tools.cello_wrapper.subprocess.run", forbidden_run)
    result = CelloWrapper(
        cello_command=["cello"],
        ucf_path=inputs["ucf"],
        sensor_path=inputs["sensor"],
        device_path=inputs["device"],
        external_required=True,
        semantic_validator=validate_and2_verilog,
    ).run(state)

    assert invoked is False
    assert result.candidate_topologies[0]["mapping_status"] == (
        "AND2_SEMANTIC_MISMATCH"
    )


def test_external_required_rejects_empty_assignments_after_exit_zero(
    tmp_path: Path,
) -> None:
    inputs = _external_inputs(tmp_path)
    state = DesignState(verilog_codes=[AND2_VERILOG])

    result = CelloWrapper(
        cello_command=_native_fixture_command(assignments=False),
        ucf_path=inputs["ucf"],
        sensor_path=inputs["sensor"],
        device_path=inputs["device"],
        artifact_dir=tmp_path / "artifacts",
        timeout_seconds=5,
        external_required=True,
        semantic_validator=validate_and2_verilog,
        attempt_budget=PilotAttemptBudget(),
    ).run(state)

    topology = result.candidate_topologies[0]
    assert topology["mapping_status"] == "MAPPING_FAILED"
    assert topology["mapping_error_category"] == "INCOMPLETE_NATIVE_MAPPING"
    assert "assignments" in topology["mapping_error_summary"]


def test_external_required_accepts_complete_native_fixture(tmp_path: Path) -> None:
    inputs = _external_inputs(tmp_path)
    state = DesignState(verilog_codes=[AND2_VERILOG])
    budget = PilotAttemptBudget()

    result = CelloWrapper(
        cello_command=_native_fixture_command(assignments=True),
        ucf_path=inputs["ucf"],
        sensor_path=inputs["sensor"],
        device_path=inputs["device"],
        artifact_dir=tmp_path / "artifacts",
        timeout_seconds=5,
        external_required=True,
        semantic_validator=validate_and2_verilog,
        attempt_budget=budget,
    ).run(state)

    topology = result.candidate_topologies[0]
    assert topology["mapping_status"] == "mapped"
    assert topology["cello_buildable"] is True
    assert topology["part_assignments"]
    assert topology["cello_parser"]["assignment_provenance"] == (
        "output_netlist_placements"
    )
    assert all(
        assignment["assignment_provenance"] == "output_netlist_placements"
        for assignment in topology["part_assignments"]
    )
    assert topology["and2_semantic_evaluation"]["passed"] is True
    assert budget.cello_subprocesses == 1


@pytest.mark.parametrize(
    ("command", "expected_summary"),
    [
        (
            _native_fixture_command(assignments=True, omit_suffix="_activity.csv"),
            "activity",
        ),
        (
            _native_fixture_command(assignments=True, part_id="UNKNOWN_PART"),
            "outside locked inputs",
        ),
    ],
)
def test_external_required_rejects_partial_or_unlocked_native_mapping(
    tmp_path: Path,
    command: list[str],
    expected_summary: str,
) -> None:
    inputs = _external_inputs(tmp_path)
    result = CelloWrapper(
        cello_command=command,
        ucf_path=inputs["ucf"],
        sensor_path=inputs["sensor"],
        device_path=inputs["device"],
        artifact_dir=tmp_path / "artifacts",
        timeout_seconds=5,
        external_required=True,
        semantic_validator=validate_and2_verilog,
        attempt_budget=PilotAttemptBudget(),
    ).run(DesignState(verilog_codes=[AND2_VERILOG]))

    topology = result.candidate_topologies[0]
    assert topology["mapping_status"] == "MAPPING_FAILED"
    assert expected_summary in topology["mapping_error_summary"]


def test_external_required_rejects_gate_name_as_physical_part(tmp_path: Path) -> None:
    inputs = _external_inputs(tmp_path)
    Path(inputs["ucf"]).write_text(
        json.dumps(
            [
                {"collection": "gates", "name": "NOR_gate_model"},
                {"collection": "parts", "name": "TetR"},
            ]
        ),
        encoding="utf-8",
    )
    result = CelloWrapper(
        cello_command=_native_fixture_command(
            assignments=True,
            part_id="NOR_gate_model",
        ),
        ucf_path=inputs["ucf"],
        sensor_path=inputs["sensor"],
        device_path=inputs["device"],
        artifact_dir=tmp_path / "artifacts",
        external_required=True,
        semantic_validator=validate_and2_verilog,
        attempt_budget=PilotAttemptBudget(),
    ).run(DesignState(verilog_codes=[AND2_VERILOG]))

    topology = result.candidate_topologies[0]
    assert topology["mapping_status"] == "MAPPING_FAILED"
    assert "outside locked inputs" in topology["mapping_error_summary"]


def test_external_required_rejects_incomplete_logic_node_coverage(
    tmp_path: Path,
) -> None:
    inputs = _external_inputs(tmp_path)
    result = CelloWrapper(
        cello_command=_native_fixture_command(
            assignments=True,
            extra_expected_node="$1",
        ),
        ucf_path=inputs["ucf"],
        sensor_path=inputs["sensor"],
        device_path=inputs["device"],
        artifact_dir=tmp_path / "artifacts",
        external_required=True,
        semantic_validator=validate_and2_verilog,
        attempt_budget=PilotAttemptBudget(),
    ).run(DesignState(verilog_codes=[AND2_VERILOG]))

    topology = result.candidate_topologies[0]
    assert topology["mapping_status"] == "MAPPING_FAILED"
    assert "logic-node coverage mismatch" in topology["mapping_error_summary"]


@pytest.mark.parametrize("fallback_key", ["assignments", "gates", "nodes"])
def test_external_required_rejects_generic_assignment_fallbacks(
    tmp_path: Path,
    fallback_key: str,
) -> None:
    inputs = _external_inputs(tmp_path)
    result = CelloWrapper(
        cello_command=_native_fixture_command(
            assignments=False,
            fallback_key=fallback_key,
        ),
        ucf_path=inputs["ucf"],
        sensor_path=inputs["sensor"],
        device_path=inputs["device"],
        artifact_dir=tmp_path / "artifacts",
        external_required=True,
        semantic_validator=validate_and2_verilog,
        attempt_budget=PilotAttemptBudget(),
    ).run(DesignState(verilog_codes=[AND2_VERILOG]))

    topology = result.candidate_topologies[0]
    assert topology["mapping_status"] == "MAPPING_FAILED"
    assert "placements-derived" in topology["mapping_error_summary"]


def test_offline_provider_lock_hashes_all_zero_cost_inputs(tmp_path: Path) -> None:
    lock_path = _offline_provider_lock(tmp_path)
    identity, paths, errors = build_offline_provider_identity(
        "http://127.0.0.1:1234",
        "local",
        lock_path,
    )

    assert errors == []
    assert identity["cost_evidence"] == "qualified_offline_provider_lock"
    assert identity["paid_cost_usd"] == 0.0
    assert {
        "provider_lock",
        "provider_runtime_executable",
        "provider_model_artifact",
    } == set(paths)
    assert len(hash_input_paths(paths, require_all=True)) == 3


@pytest.mark.parametrize("tamper", ["remote_forwarding", "runtime_hash", "model_hash"])
def test_offline_provider_lock_fails_closed(tmp_path: Path, tamper: str) -> None:
    lock_path = Path(_offline_provider_lock(tmp_path))
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    if tamper == "remote_forwarding":
        payload["remote_forwarding_allowed"] = True
    elif tamper == "runtime_hash":
        payload["runtime_executable"]["sha256"] = "0" * 64
    else:
        payload["model_artifact"]["sha256"] = "0" * 64
    lock_path.write_text(json.dumps(payload), encoding="utf-8")

    identity, _paths, errors = build_offline_provider_identity(
        "http://127.0.0.1:1234",
        "local",
        str(lock_path),
    )

    assert errors
    assert identity["cost_evidence"] == "unverified"


def test_offline_provider_lock_rejects_boolean_zero_cost_cap(tmp_path: Path) -> None:
    lock_path = Path(_offline_provider_lock(tmp_path))
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    payload["paid_cost_cap_usd"] = False
    lock_path.write_text(json.dumps(payload), encoding="utf-8")

    identity, _paths, errors = build_offline_provider_identity(
        "http://127.0.0.1:1234",
        "local",
        str(lock_path),
    )

    assert "provider lock paid_cost_cap_usd must be exactly 0.0" in errors
    assert identity["cost_evidence"] == "unverified"


def test_offline_provider_lock_converts_permission_error_to_validation_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = Path(_offline_provider_lock(tmp_path)).resolve()
    original_is_file = Path.is_file

    def guarded_is_file(path: Path) -> bool:
        if path.resolve() == lock_path:
            raise PermissionError("provider lock blocked by host policy")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", guarded_is_file)

    identity, paths, errors = build_offline_provider_identity(
        "http://127.0.0.1:1234",
        "local",
        str(lock_path),
    )

    assert paths == {}
    assert identity["cost_evidence"] == "unverified"
    assert len(errors) == 1
    assert "provider_lock_path is unreadable" in errors[0]
    assert "PermissionError" in errors[0]


def test_toolchain_lock_rejects_digest_hidden_outside_image_reference(
    tmp_path: Path,
) -> None:
    inputs = _locked_external_inputs(tmp_path)
    spoofed_command = [
        sys.executable,
        "run",
        "--pull=never",
        "--network=none",
        f"--label=qualified={LOCKED_IMAGE}",
        "docker.io/cidarlab/cello-dnacompiler:latest",
    ]

    errors = validate_and2_toolchain_lock(
        build_toolchain_identity(spoofed_command),
        inputs,
    )

    assert any("exact digest-pinned image reference" in error for error in errors)


def test_repository_toolchain_lock_remains_blocked_until_license_is_allowed() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    lock_path = (
        repository_root
        / "benchmark_suite/protocols/exp024_cello_toolchain_lock.json"
    )
    if not lock_path.is_file():
        pytest.skip("local-only EXP-024 toolchain lock is not part of a fresh checkout")

    errors = validate_and2_toolchain_lock(
        build_toolchain_identity(LOCKED_COMMAND),
        {"toolchain_lock": str(lock_path)},
    )

    assert (
        "toolchain lock must record cello.license_status=allowed before execution"
        in errors
    )


def test_provider_lock_tamper_between_validation_and_freeze_blocks_agents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _locked_external_inputs(tmp_path)
    lock_path = Path(_offline_provider_lock(tmp_path))
    original_hash_input_paths = hash_input_paths
    freeze_calls = 0
    agent_constructions = 0

    def tamper_before_first_freeze(paths, *, require_all=False):
        nonlocal freeze_calls
        if require_all and "provider_lock" in paths:
            freeze_calls += 1
            if freeze_calls == 1:
                payload = json.loads(lock_path.read_text(encoding="utf-8"))
                payload["remote_forwarding_allowed"] = True
                lock_path.write_text(json.dumps(payload), encoding="utf-8")
        return original_hash_input_paths(paths, require_all=require_all)

    def forbidden_builder(*args, **kwargs):
        nonlocal agent_constructions
        agent_constructions += 1
        raise AssertionError("agent construction must remain blocked")

    monkeypatch.setattr(
        "mcp_server.and2_pilot_preflight.hash_input_paths",
        tamper_before_first_freeze,
    )
    monkeypatch.setattr("agents.builder_agent.BuilderAgent", forbidden_builder)
    result = design_circuit_quick(
        "AND2",
        compute_budget=1,
        enable_rag=False,
        enable_skill_extraction=False,
        model_name="local",
        api_base="http://127.0.0.1:1234",
        provider_lock_path=str(lock_path),
        toolchain_lock_path=inputs["toolchain_lock"],
        output_dir=str(tmp_path / "runs"),
        cello_command=LOCKED_COMMAND,
        ucf_path=inputs["ucf"],
        sensor_path=inputs["sensor"],
        device_path=inputs["device"],
        integrated_pilot=True,
        cello_timeout_seconds=300,
    )

    record = json.loads(
        Path(result["artifacts"]["pilot_failure_record_json"]).read_text(
            encoding="utf-8"
        )
    )
    assert agent_constructions == 0
    assert record["stage_id"] == "preconstruction"
    assert record["status"] == "rejected"
    assert record["failure_category"] == "PROVIDER_SNAPSHOT_DRIFT"
    assert record["provider_call_count"] == 0


def test_failure_record_schema_requires_all_fields() -> None:
    record = {
        "schema_version": "and2-pilot-failure-record@2.0.0",
        "case_id": "FINAL-CLOSEOUT-AND2-001",
        "attempt_id": "attempt-01",
        "stage_id": "cello",
        "status": "timeout",
        "failure_category": "TIMEOUT",
        "command": ["cello"],
        "runtime_identity": {"python": "test"},
        "input_sha256s": {},
        "pre_input_sha256s": {},
        "post_input_sha256s": {},
        "input_hashes_equal": True,
        "exit_code": None,
        "elapsed_seconds": 300.0,
        "provider_call_count": 2,
        "paid_cost_usd": 0.0,
        "cleanup_result": "pass",
        "artifact_inventory": [],
        "final_disposition": "NO_GO",
    }

    assert validate_failure_record(record) == []
    record.pop("cleanup_result")
    assert validate_failure_record(record) == ["missing:cleanup_result"]

    record["cleanup_result"] = "pass"
    record["input_sha256s"] = {"ucf": "not-a-hash"}
    assert (
        "input_sha256s values must be lowercase SHA-256 hex"
        in validate_failure_record(record)
    )


def test_single_proposal_builder_uses_single_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    response = {
        "proposal": {
            "strategy_name": "AND2 Pilot",
            "optimization_goal": "exact AND2",
            "truth_table_or_logic_matrix": [
                {"A": 0, "B": 0, "GFP": 0},
                {"A": 0, "B": 1, "GFP": 0},
                {"A": 1, "B": 0, "GFP": 0},
                {"A": 1, "B": 1, "GFP": 1},
            ],
            "logic_blueprint": "GFP = A AND B",
            "verilog_draft": AND2_VERILOG,
            "translator_directives": [],
        }
    }

    def fake_call_llm(**kwargs):
        captured.update(kwargs)
        return json.dumps(response)

    monkeypatch.setattr("agents.builder_agent.call_llm", fake_call_llm)
    state = call_builder(
        DesignState(user_intent="AND2"),
        api_key=None,
        model_name="local",
        proposal_limit=1,
        attempt_budget=PilotAttemptBudget(),
    )

    assert len(state.logic_proposals) == 1
    assert "exactly one" in captured["system_prompt"].lower()
    assert "three alternative" not in captured["user_content"].lower()


def test_single_proposal_builder_rejects_extra_top_level_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {
        "proposal": {
            "strategy_name": "AND2 Pilot",
            "optimization_goal": "exact AND2",
            "truth_table_or_logic_matrix": [],
            "logic_blueprint": "GFP = A AND B",
            "verilog_draft": AND2_VERILOG,
            "translator_directives": [],
        },
        "unexpected_second_proposal": {},
    }
    monkeypatch.setattr(
        "agents.builder_agent.call_llm",
        lambda **_kwargs: json.dumps(response),
    )

    state = call_builder(
        DesignState(user_intent="AND2"),
        api_key=None,
        model_name="local",
        proposal_limit=1,
        attempt_budget=PilotAttemptBudget(),
    )

    assert state.logic_proposals == []
    assert "ERROR:" in state.last_error


def test_runtime_dependency_closure_contains_direct_pilot_dependencies(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    inputs = _external_inputs(tmp_path)
    command = LOCKED_COMMAND
    paths = build_and2_runtime_input_paths(repository_root, inputs, command)
    base_paths = build_and2_runtime_input_paths(repository_root, {}, [])

    values = {Path(path).name for path in paths.values()}
    assert {
        "functional_scorer.py",
        "cello_artifact_parser.py",
        "cello21_artifact_parser.py",
        "cello_constraint_evaluator.py",
        "part_library.py",
        "demo_cello_v1.json",
    } <= values
    relative_paths = {
        Path(path).resolve().relative_to(repository_root).as_posix()
        for path in base_paths.values()
        if Path(path).resolve().is_relative_to(repository_root)
    }
    assert {
        "benchmark_suite/__init__.py",
        "src/mcp_server/__init__.py",
        "src/mcp_server/and2_pilot_preflight.py",
        "src/schemas/__init__.py",
        "src/utils/lazy_exports.py",
    } <= relative_paths
    public_runtime_paths = {
        key: value
        for key, value in base_paths.items()
        if key not in {"toolchain_lock", "mapping_protocol"}
    }
    assert len(public_runtime_paths) == 71
    assert build_toolchain_identity(command)["immutable_reference"] is True


def test_toolchain_identity_converts_host_permission_error_to_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocked = tmp_path / "blocked-podman.exe"
    original_is_file = Path.is_file

    def permission_denied(path: Path) -> bool:
        if path == blocked:
            raise PermissionError("blocked by host application control")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", permission_denied)
    identity = build_toolchain_identity(
        [str(blocked), "run", "--pull=never", "--network=none", LOCKED_IMAGE]
    )

    assert identity["executable_status"] == "unreadable"
    assert identity["executable_sha256"] is None
    assert "PermissionError" in identity["executable_error"]


def test_pilot_artifact_writer_closes_named_lineage(tmp_path: Path) -> None:
    cello_manifest = tmp_path / "cello_manifest.json"
    cello_manifest.write_text("{}", encoding="utf-8")
    inputs = _external_inputs(tmp_path)
    state = DesignState(
        logic_proposals=[json.dumps({"logic": "A AND B"})],
        verilog_codes=[AND2_VERILOG],
        best_topology={
            "verilog": AND2_VERILOG,
            "and2_semantic_evaluation": validate_and2_verilog(AND2_VERILOG),
            "ode_trace": {"time": [0.0, 1.0], "output_protein": [0.0, 1.0]},
            "benchmark_report": {"score": 1.0},
            "cello_artifact_manifest_path": str(cello_manifest),
            "mapping_status": "mapped",
            "cello_mode": "external",
            "cello_buildable": True,
            "part_assignments": [{"logic_node_id": "GFP", "part_id": "TetR"}],
        },
        is_completed=True,
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    budget = PilotAttemptBudget()
    budget.consume_provider("builder")
    artifacts = write_state_artifacts(state, run_dir)

    artifacts = write_and2_pilot_artifacts(
        state,
        run_dir,
        artifacts,
        attempt_budget=budget,
        input_paths=inputs,
        frozen_input_sha256s=hash_input_paths(inputs, require_all=True),
        toolchain_identity=build_toolchain_identity(LOCKED_COMMAND),
        provider_identity={
            "cost_evidence": "qualified_offline_provider_lock",
            "paid_cost_usd": 0.0,
        },
    )

    expected = {
        "builder_logic_proposals_json",
        "generated_verilog_candidate_0",
        "semantic_evaluation_json",
        "ode_trace_json",
        "evaluator_result_json",
        "cello_artifact_manifest_json",
        "e3_run_manifest_json",
    }
    assert expected <= set(artifacts)
    closure = json.loads(Path(artifacts["e3_run_manifest_json"]).read_text())
    assert closure["attempt_budget"]["provider_calls"] == 1
    assert {"ucf", "sensor", "device"} <= set(closure["input_sha256s"])
    assert closure["output_sha256s"]
    assert closure["toolchain_identity"]["immutable_reference"] is True


def test_pilot_artifact_writer_rejects_input_changed_after_freeze(
    tmp_path: Path,
) -> None:
    cello_manifest = tmp_path / "cello_manifest.json"
    cello_manifest.write_text("{}", encoding="utf-8")
    inputs = _external_inputs(tmp_path)
    frozen = hash_input_paths(inputs, require_all=True)
    Path(inputs["ucf"]).write_text("[]", encoding="utf-8")
    state = DesignState(
        logic_proposals=[json.dumps({"logic": "A AND B"})],
        verilog_codes=[AND2_VERILOG],
        best_topology={
            "verilog": AND2_VERILOG,
            "and2_semantic_evaluation": validate_and2_verilog(AND2_VERILOG),
            "ode_trace": {"time": [0.0], "output_protein": [0.0]},
            "benchmark_report": {"score": 1.0},
            "cello_artifact_manifest_path": str(cello_manifest),
            "mapping_status": "mapped",
            "cello_mode": "external",
            "cello_buildable": True,
            "part_assignments": [{"logic_node_id": "GFP", "part_id": "TetR"}],
        },
        is_completed=True,
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    with pytest.raises(ValueError, match="changed after preflight"):
        write_and2_pilot_artifacts(
            state,
            run_dir,
            write_state_artifacts(state, run_dir),
            attempt_budget=PilotAttemptBudget(),
            input_paths=inputs,
            frozen_input_sha256s=frozen,
        )


def test_pilot_artifact_writer_rejects_missing_success_artifact(tmp_path: Path) -> None:
    state = DesignState(
        logic_proposals=[json.dumps({"logic": "A AND B"})],
        verilog_codes=[AND2_VERILOG],
        best_topology={
            "verilog": AND2_VERILOG,
            "and2_semantic_evaluation": validate_and2_verilog(AND2_VERILOG),
            "benchmark_report": {"score": 1.0},
            "mapping_status": "mapped",
            "cello_mode": "external",
            "cello_buildable": True,
            "part_assignments": [{"logic_node_id": "GFP", "part_id": "TetR"}],
        },
        is_completed=True,
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    with pytest.raises(ValueError, match="ode_trace"):
        write_and2_pilot_artifacts(
            state,
            run_dir,
            write_state_artifacts(state, run_dir),
            attempt_budget=PilotAttemptBudget(),
            input_paths=_external_inputs(tmp_path),
        )


def test_service_pilot_preflight_writes_rejected_failure_record(tmp_path: Path) -> None:
    result = design_circuit_quick(
        "AND2",
        model_name="mock",
        output_dir=str(tmp_path),
        integrated_pilot=True,
    )

    assert result["status"] == "error"
    failure_path = Path(result["artifacts"]["pilot_failure_record_json"])
    record = json.loads(failure_path.read_text(encoding="utf-8"))
    assert record["status"] == "rejected"
    assert record["provider_call_count"] == 0
    assert record["paid_cost_usd"] == 0.0


def test_service_records_unreadable_toolchain_as_structured_preflight_no_go(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _locked_external_inputs(tmp_path)
    blocked = tmp_path / "blocked-podman.exe"
    original_is_file = Path.is_file

    def permission_denied(path: Path) -> bool:
        if path == blocked:
            raise PermissionError("blocked by host application control")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", permission_denied)
    monkeypatch.setattr(
        "mcp_server.service.run_reflexion_workflow",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("workflow must not start")
        ),
    )
    result = design_circuit_quick(
        "AND2",
        compute_budget=1,
        enable_rag=False,
        enable_skill_extraction=False,
        model_name="local",
        api_base="http://127.0.0.1:1234",
        provider_lock_path=_offline_provider_lock(tmp_path),
        toolchain_lock_path=inputs["toolchain_lock"],
        output_dir=str(tmp_path / "runs"),
        cello_command=[
            str(blocked),
            "run",
            "--pull=never",
            "--network=none",
            LOCKED_IMAGE,
        ],
        ucf_path=inputs["ucf"],
        sensor_path=inputs["sensor"],
        device_path=inputs["device"],
        integrated_pilot=True,
        cello_timeout_seconds=300,
    )

    failure_path = Path(result["artifacts"]["pilot_failure_record_json"])
    record = json.loads(failure_path.read_text(encoding="utf-8"))
    assert record["status"] == "rejected"
    assert record["failure_category"] == "TOOLCHAIN_EXECUTABLE_UNREADABLE"
    assert record["toolchain_identity"]["executable_status"] == "unreadable"
    assert record["provider_call_count"] == 0
    assert validate_and2_pilot_bundle(failure_path.parent)["status"] == "pass"


def test_integrated_pilot_success_rehearsal_writes_validated_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _locked_external_inputs(tmp_path)
    cello_manifest = tmp_path / "native_manifest.json"
    cello_manifest.write_text(
        json.dumps({"command": LOCKED_COMMAND, "return_code": 0}),
        encoding="utf-8",
    )

    def successful_state(**kwargs):
        budget = kwargs["critic"].kwargs["attempt_budget"]
        budget.consume_provider("builder")
        budget.consume_provider("translator")
        budget.consume_provider("critic")
        budget.consume_cello("cello")
        return DesignState(
            user_intent="AND2",
            logic_proposals=[json.dumps({"logic": "A AND B"})],
            verilog_codes=[AND2_VERILOG],
            best_topology={
                "verilog": AND2_VERILOG,
                "and2_semantic_evaluation": validate_and2_verilog(AND2_VERILOG),
                "ode_trace": {
                    "time": [0.0, 1.0],
                    "output_protein": [0.0, 1.0],
                },
                "benchmark_report": {"score": 1.0},
                "cello_artifact_manifest_path": str(cello_manifest),
                "cello_artifact_manifest": {
                    "command": LOCKED_COMMAND,
                    "return_code": 0,
                },
                "mapping_status": "mapped",
                "cello_mode": "external",
                "cello_buildable": True,
                "part_assignments": [
                    {"logic_node_id": "GFP", "part_id": "TetR"}
                ],
            },
            is_completed=True,
        )

    monkeypatch.setattr("mcp_server.service.run_reflexion_workflow", successful_state)
    monkeypatch.setattr("mcp_server.service.render_charts", lambda *args: [])
    result = design_circuit_quick(
        "AND2",
        compute_budget=1,
        enable_rag=False,
        enable_ode=True,
        enable_skill_extraction=False,
        model_name="local",
        api_base="http://127.0.0.1:1234",
        provider_lock_path=_offline_provider_lock(tmp_path),
        toolchain_lock_path=inputs["toolchain_lock"],
        output_dir=str(tmp_path / "runs"),
        cello_command=LOCKED_COMMAND,
        ucf_path=inputs["ucf"],
        sensor_path=inputs["sensor"],
        device_path=inputs["device"],
        integrated_pilot=True,
        cello_timeout_seconds=300,
        provider_call_cap=3,
    )

    assert result["status"] == "completed"
    validation = validate_and2_pilot_bundle(result["run_dir"])
    assert validation["status"] == "pass", validation["errors"]
    assert validation["bundle_kind"] == "success"
    assert validation["verified_artifact_count"] >= 9

    semantic_path = Path(result["artifacts"]["semantic_evaluation_json"])
    semantic_path.write_text("{}", encoding="utf-8")
    tampered = validate_and2_pilot_bundle(result["run_dir"])
    assert tampered["status"] == "fail"
    assert "artifact hash mismatch: semantic_evaluation_json" in tampered["errors"]


def test_service_propagates_frozen_limits_without_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _locked_external_inputs(tmp_path)
    captured = {}

    def stop_before_execution(**kwargs):
        captured["wrapper"] = kwargs["cello_wrapper"]
        captured["builder"] = kwargs["builder"]
        captured["translator"] = kwargs["translator"]
        captured["critic"] = kwargs["critic"]
        raise RuntimeError("test stop before workflow execution")

    monkeypatch.setattr(
        "mcp_server.service.run_reflexion_workflow", stop_before_execution
    )
    result = design_circuit_quick(
        "AND2",
        compute_budget=1,
        enable_rag=False,
        enable_ode=True,
        enable_skill_extraction=False,
        model_name="mock",
        api_base="http://127.0.0.1:1234",
        provider_lock_path=_offline_provider_lock(tmp_path, model_name="mock"),
        toolchain_lock_path=inputs["toolchain_lock"],
        output_dir=str(tmp_path / "runs"),
        cello_command=LOCKED_COMMAND,
        ucf_path=inputs["ucf"],
        sensor_path=inputs["sensor"],
        device_path=inputs["device"],
        integrated_pilot=True,
        cello_timeout_seconds=300,
        provider_call_cap=3,
    )

    assert result["status"] == "error"
    assert captured["wrapper"].timeout_seconds == 300
    assert captured["wrapper"].external_required is True
    assert captured["builder"].kwargs["proposal_limit"] == 1
    assert captured["translator"].kwargs["max_retries"] == 1
    budget = captured["critic"].kwargs["attempt_budget"]
    assert budget.max_provider_calls == 3
    assert budget.max_cello_subprocesses == 1
    failure_path = Path(result["artifacts"]["pilot_failure_record_json"])
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["command"] == LOCKED_COMMAND
    assert {"ucf", "sensor", "device"} <= set(failure["input_sha256s"])
    assert "toolchain_executable" in failure["input_sha256s"]
    assert failure["toolchain_identity"]["executable_sha256"]
    assert (
        failure["provider_identity"]["endpoint_class"] == "qualified_offline_loopback"
    )
    assert failure["provider_identity"]["api_key_present"] is False
    assert failure["provider_identity"]["cost_evidence"] == (
        "qualified_offline_provider_lock"
    )
    assert {
        "provider_lock",
        "provider_runtime_executable",
        "provider_model_artifact",
    } <= set(failure["input_sha256s"])


@pytest.mark.parametrize("missing_key", ["ucf", "sensor", "device"])
def test_service_rejects_each_missing_external_file_before_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_key: str,
) -> None:
    inputs = _locked_external_inputs(tmp_path)
    inputs[missing_key] = str(tmp_path / f"missing-{missing_key}.json")

    def forbidden_workflow(**kwargs):
        raise AssertionError("workflow must not start")

    monkeypatch.setattr("mcp_server.service.run_reflexion_workflow", forbidden_workflow)
    result = design_circuit_quick(
        "AND2",
        compute_budget=1,
        enable_rag=False,
        enable_skill_extraction=False,
        model_name="local",
        api_base="http://127.0.0.1:1234",
        provider_lock_path=_offline_provider_lock(tmp_path),
        toolchain_lock_path=inputs["toolchain_lock"],
        output_dir=str(tmp_path / "runs"),
        cello_command=LOCKED_COMMAND,
        ucf_path=inputs["ucf"],
        sensor_path=inputs["sensor"],
        device_path=inputs["device"],
        integrated_pilot=True,
        cello_timeout_seconds=300,
    )

    record = json.loads(
        Path(result["artifacts"]["pilot_failure_record_json"]).read_text(
            encoding="utf-8"
        )
    )
    assert record["status"] == "rejected"
    assert record["provider_call_count"] == 0


def test_service_rejects_non_loopback_provider_before_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _locked_external_inputs(tmp_path)
    monkeypatch.setattr(
        "mcp_server.service.run_reflexion_workflow",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("workflow must not start")
        ),
    )
    result = design_circuit_quick(
        "AND2",
        compute_budget=1,
        enable_rag=False,
        enable_skill_extraction=False,
        model_name="remote",
        api_base="https://provider.example/v1",
        provider_lock_path=_offline_provider_lock(
            tmp_path,
            model_name="remote",
            api_base="https://provider.example/v1",
        ),
        toolchain_lock_path=inputs["toolchain_lock"],
        output_dir=str(tmp_path / "runs"),
        cello_command=LOCKED_COMMAND,
        ucf_path=inputs["ucf"],
        sensor_path=inputs["sensor"],
        device_path=inputs["device"],
        integrated_pilot=True,
        cello_timeout_seconds=300,
    )

    record = json.loads(
        Path(result["artifacts"]["pilot_failure_record_json"]).read_text(
            encoding="utf-8"
        )
    )
    assert record["status"] == "rejected"
    assert record["provider_call_count"] == 0
    assert record["paid_cost_usd"] == 0.0


@pytest.mark.parametrize("invalid_kind", ["image_digest", "input_hash"])
def test_service_rejects_unqualified_toolchain_before_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_kind: str,
) -> None:
    inputs = _locked_external_inputs(tmp_path)
    command = list(LOCKED_COMMAND)
    if invalid_kind == "image_digest":
        command[-1] = "image@sha256:" + "a" * 64
    else:
        Path(inputs["ucf"]).write_text(
            json.dumps([{"collection": "parts", "name": "tampered"}]),
            encoding="utf-8",
        )
    monkeypatch.setattr(
        "mcp_server.service.run_reflexion_workflow",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("workflow must not start")
        ),
    )

    result = design_circuit_quick(
        "AND2",
        compute_budget=1,
        enable_rag=False,
        enable_skill_extraction=False,
        model_name="local",
        api_base="http://127.0.0.1:1234",
        provider_lock_path=_offline_provider_lock(tmp_path),
        toolchain_lock_path=inputs["toolchain_lock"],
        output_dir=str(tmp_path / "runs"),
        cello_command=command,
        ucf_path=inputs["ucf"],
        sensor_path=inputs["sensor"],
        device_path=inputs["device"],
        integrated_pilot=True,
        cello_timeout_seconds=300,
    )

    record = json.loads(
        Path(result["artifacts"]["pilot_failure_record_json"]).read_text(
            encoding="utf-8"
        )
    )
    assert record["status"] == "rejected"
    assert record["provider_call_count"] == 0


@pytest.mark.parametrize(
    ("category", "return_code", "expected_status"),
    [("TIMEOUT", None, "timeout"), ("CELLO_ERROR", 2, "failed")],
)
def test_service_preserves_native_failure_record_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    category: str,
    return_code: int | None,
    expected_status: str,
) -> None:
    inputs = _locked_external_inputs(tmp_path)
    cello_manifest = tmp_path / "native_manifest.json"
    command = LOCKED_COMMAND
    cello_manifest.write_text(
        json.dumps({"command": command, "return_code": return_code}),
        encoding="utf-8",
    )

    def failed_state(**kwargs):
        return DesignState(
            user_intent="AND2",
            verilog_codes=[AND2_VERILOG],
            best_topology={
                "verilog": AND2_VERILOG,
                "mapping_status": "MAPPING_FAILED",
                "mapping_error_category": category,
                "return_code": return_code,
                "cello_artifact_manifest_path": str(cello_manifest),
                "cello_artifact_manifest": {
                    "command": command,
                    "return_code": return_code,
                },
            },
            is_completed=False,
        )

    monkeypatch.setattr("mcp_server.service.run_reflexion_workflow", failed_state)
    result = design_circuit_quick(
        "AND2",
        compute_budget=1,
        enable_rag=False,
        enable_skill_extraction=False,
        model_name="local",
        api_base="http://127.0.0.1:1234",
        provider_lock_path=_offline_provider_lock(tmp_path),
        toolchain_lock_path=inputs["toolchain_lock"],
        output_dir=str(tmp_path / "runs"),
        cello_command=command,
        ucf_path=inputs["ucf"],
        sensor_path=inputs["sensor"],
        device_path=inputs["device"],
        integrated_pilot=True,
        cello_timeout_seconds=300,
    )

    record = json.loads(
        Path(result["artifacts"]["pilot_failure_record_json"]).read_text(
            encoding="utf-8"
        )
    )
    assert record["status"] == expected_status
    assert record["failure_category"] == category
    assert record["exit_code"] == return_code
    assert record["cleanup_result"] == "pass"
    assert record["paid_cost_usd"] == 0.0


@pytest.mark.parametrize(
    ("category", "return_code"),
    [("TIMEOUT", None), ("CELLO_ERROR", 2)],
)
def test_failed_attempt_records_input_hash_drift_before_terminal_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    category: str,
    return_code: int | None,
) -> None:
    inputs = _locked_external_inputs(tmp_path)
    lock_path = _offline_provider_lock(tmp_path)
    model_path = tmp_path / "offline-model.gguf"
    cello_manifest = tmp_path / "native_manifest.json"
    cello_manifest.write_text(
        json.dumps({"command": LOCKED_COMMAND, "return_code": return_code}),
        encoding="utf-8",
    )

    def failed_state(**kwargs):
        model_path.write_bytes(b"changed after preflight")
        return DesignState(
            user_intent="AND2",
            verilog_codes=[AND2_VERILOG],
            best_topology={
                "verilog": AND2_VERILOG,
                "mapping_status": "MAPPING_FAILED",
                "mapping_error_category": category,
                "return_code": return_code,
                "cello_artifact_manifest_path": str(cello_manifest),
                "cello_artifact_manifest": {
                    "command": LOCKED_COMMAND,
                    "return_code": return_code,
                },
            },
            is_completed=False,
        )

    monkeypatch.setattr("mcp_server.service.run_reflexion_workflow", failed_state)
    result = design_circuit_quick(
        "AND2",
        compute_budget=1,
        enable_rag=False,
        enable_skill_extraction=False,
        model_name="local",
        api_base="http://127.0.0.1:1234",
        provider_lock_path=lock_path,
        toolchain_lock_path=inputs["toolchain_lock"],
        output_dir=str(tmp_path / "runs"),
        cello_command=LOCKED_COMMAND,
        ucf_path=inputs["ucf"],
        sensor_path=inputs["sensor"],
        device_path=inputs["device"],
        integrated_pilot=True,
        cello_timeout_seconds=300,
    )

    record = json.loads(
        Path(result["artifacts"]["pilot_failure_record_json"]).read_text(
            encoding="utf-8"
        )
    )
    assert record["failure_category"] == "INPUT_HASH_DRIFT"
    assert record["final_disposition"] == "INPUT_HASH_DRIFT"
    assert record["input_hashes_equal"] is False
    assert record["pre_input_sha256s"] != record["post_input_sha256s"]
    assert record["provider_call_count"] == 0
