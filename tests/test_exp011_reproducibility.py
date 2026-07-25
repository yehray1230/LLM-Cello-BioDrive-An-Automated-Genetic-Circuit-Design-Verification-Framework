from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parent.parent
for import_root in (ROOT, ROOT / "src"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

import scripts.run_exp011_benchmark as exp011  # noqa: E402
from scripts.run_exp011_benchmark import (  # noqa: E402
    build_parser,
    compute_exp011_decision,
    get_cost,
    main,
    validate_run_matrix,
)


def _run(
    task_id: str,
    config_name: str,
    rep: int,
    *,
    passed: bool = True,
    cost: float = 0.001,
) -> dict[str, object]:
    return {
        "task_id": task_id,
        "config_name": config_name,
        "rep": rep,
        "passed": passed,
        "estimated_cost_usd": cost,
        "artifact_manifest_sha256": "a" * 64,
        "error": None,
        "eval_details": {},
    }


def test_exp011_cli_parser_defaults_are_current_and_non_destructive() -> None:
    args = build_parser().parse_args([])
    assert args.repeats == 3
    assert args.single_model == "gemini/gemini-3.5-flash"
    assert args.routed_model == "routed:gemini/gemini-3.5-flash"
    assert args.revision == "exp011_v3"
    assert args.offline is False
    assert args.preflight_only is False
    assert args.authorize_paid_live_run is False
    assert args.calibration_only is False
    assert args.max_total_cost_usd is None
    assert Path(args.output_dir).name == "exp011_v3"


def test_cost_requires_an_explicit_frozen_rate() -> None:
    assert get_cost("gemini/gemini-2.5-flash", 1_000_000, 1_000_000) == 2.8
    with pytest.raises(ValueError, match="No frozen EXP-011 rate card"):
        get_cost("gemini/unknown-model", 10, 10)


def test_compute_exp011_decision_respects_eligibility_and_offline_boundary() -> None:
    runs_single_better = [
        _run("a", "Single-Model", 1, passed=True),
        _run("a", "Routed-Model", 1, passed=False),
    ]
    assert compute_exp011_decision(runs_single_better) == "single_favored"
    assert (
        compute_exp011_decision(runs_single_better, comparison_eligible=False)
        == "not_comparable"
    )

    routed_cost = [
        _run("a", "Single-Model", 1, cost=0.010),
        _run("a", "Routed-Model", 1, cost=0.002),
    ]
    assert compute_exp011_decision(routed_cost) == "routed_favored"

    equal = [
        _run("a", "Single-Model", 1, cost=0.005),
        _run("a", "Routed-Model", 1, cost=0.0049),
    ]
    assert compute_exp011_decision(equal) == "no_meaningful_difference"

    offline = [dict(run, eval_details={"offline_mock": True}) for run in equal]
    assert compute_exp011_decision(offline) == "offline_synthetic_only"


def test_run_matrix_reconciles_exact_pairs_and_rejects_errors() -> None:
    runs = [
        _run(task_id, config, rep)
        for task_id in ("a", "b")
        for config in ("Single-Model", "Routed-Model")
        for rep in (1, 2, 3)
    ]
    assert validate_run_matrix(runs, ["a", "b"], 3, offline=False) == []
    runs[0]["error"] = "provider failure"
    assert validate_run_matrix(runs, ["a", "b"], 3, offline=False) == [
        "run_error_present"
    ]
    assert "offline_synthetic_harness" in validate_run_matrix(
        runs, ["a", "b"], 3, offline=True
    )


def test_empty_artifact_tree_is_rejected(tmp_path: Path) -> None:
    artifact_hash = exp011._tree_hash([tmp_path])
    assert artifact_hash == ""

    runs = [
        _run("a", "Single-Model", 1, passed=True, cost=0.1),
        _run("a", "Routed-Model", 1, passed=True, cost=0.1),
    ]
    for run in runs:
        run["artifact_manifest_sha256"] = artifact_hash

    assert exp011.validate_run_matrix(runs, ["a"], 1, offline=False) == [
        "missing_artifact_manifest_hash"
    ]


def test_current_service_payload_is_normalized_without_losing_evidence() -> None:
    payload = {
        "status": "completed",
        "run_dir": "runs/example",
        "summary": {
            "is_completed": True,
            "used_budget": 2,
            "best_topology": {
                "source": "mock_cello_wrapper",
                "cello_mode": "mock",
                "mapping_status": "unmapped",
            },
        },
        "artifacts": {"summary": "summary.json"},
        "warnings": [],
        "safety": {"status": "safe"},
        "error": None,
        "error_type": None,
    }
    normalized = exp011._normalize_live_workflow_result(payload)
    assert normalized["status"] == "success"
    assert normalized["service_status"] == "completed"
    assert normalized["data"]["is_completed"] is True
    assert normalized["data"]["best_topology"]["mapping_status"] == "unmapped"
    assert normalized["data"]["artifacts"] == {"summary": "summary.json"}
    assert exp011._mapping_gate(normalized)["eligible_for_real_mapping_claim"] is False

    needs_input = exp011._normalize_live_workflow_result(
        {"status": "needs_human_input", "summary": {"is_completed": False}}
    )
    assert needs_input["status"] == "success"
    assert needs_input["data"]["status"] == "needs_human_input"

    failed = exp011._normalize_live_workflow_result(
        {"status": "error", "summary": {}, "error": "workflow failed"}
    )
    assert failed["status"] == "error"
    assert failed["error"] == "workflow failed"


def test_combinational_adapter_derives_truth_table_without_weakening_simulation_gate() -> None:
    task = SimpleNamespace(
        expected={
            "evaluation_mode": "combinational_logic",
            "logic_expression": "A OR B",
            "inputs": ["A", "B"],
            "outputs": ["reporter"],
            "truth_table": [
                {"A": 0, "B": 0, "reporter": 0},
                {"A": 0, "B": 1, "reporter": 1},
                {"A": 1, "B": 0, "reporter": 1},
                {"A": 1, "B": 1, "reporter": 1},
            ],
        }
    )
    topology = {
        "verilog": "module circuit(input A, input B, output Y); or(Y, A, B); endmodule",
        "simulation_result": {
            "status": "simulated",
            "scenario_results": [{"scenario": "frozen-evidence"}],
        },
        "benchmark_report": {"component_scores": {"functional": 0.25}},
    }
    research_result, details = exp011._adapt_combinational_evidence(task, topology)
    assert research_result["candidate"]["truth_table"] == [
        {"A": 0, "B": 0, "reporter": 0},
        {"A": 0, "B": 1, "reporter": 1},
        {"A": 1, "B": 0, "reporter": 1},
        {"A": 1, "B": 1, "reporter": 1},
    ]
    assert research_result["simulation_result"]["status"] == "simulated"
    assert research_result["evaluation"]["component_scores"]["functional"] == 1.0
    assert details["truth_table_exact_match"] is True
    assert details["output_mapping"] == {
        "task_output": "reporter",
        "verilog_output": "Y",
    }

    incomplete = {
        **topology,
        "simulation_result": {"status": "simulated", "scenario_results": []},
    }
    research_result, details = exp011._adapt_combinational_evidence(task, incomplete)
    assert research_result["simulation_result"]["status"] == "incomplete_evidence"
    assert research_result["evaluation"]["component_scores"]["functional"] == 1.0
    assert details["truth_table_exact_match"] is True
    assert details["simulation_evidence_complete"] is False

    malformed_trace = {
        **topology,
        "simulation_result": {"status": "simulated"},
        "ode_trace": {
            "time": ["not-a-number"],
            "output_protein": [],
        },
    }
    research_result, details = exp011._adapt_combinational_evidence(
        task, malformed_trace
    )
    assert research_result["simulation_result"]["status"] == "incomplete_evidence"
    assert details["truth_table_exact_match"] is True
    assert details["simulation_evidence_complete"] is False
    assert details["ode_trace_present"] is True
    assert details["ode_trace_valid"] is False
    assert details["ode_trace_errors"] == [
        "missing_ode_trace_output_protein",
        "ode_trace_requires_finite_numeric_values",
    ]

    wrong_logic = {
        **topology,
        "verilog": "module circuit(input A, input B, output Y); and(Y, A, B); endmodule",
    }
    research_result, details = exp011._adapt_combinational_evidence(task, wrong_logic)
    assert details["truth_table_exact_match"] is False
    assert research_result["evaluation"]["component_scores"]["functional"] == 0.0

    negated_group = {
        **topology,
        "verilog": (
            "module circuit(input A, input B, output Y); "
            "assign Y = ~(A | B); endmodule"
        ),
    }
    research_result, details = exp011._adapt_combinational_evidence(
        task, negated_group
    )
    assert research_result["candidate"]["truth_table"] == [
        {"A": 0, "B": 0, "reporter": 1},
        {"A": 0, "B": 1, "reporter": 0},
        {"A": 1, "B": 0, "reporter": 0},
        {"A": 1, "B": 1, "reporter": 0},
    ]
    assert details["truth_table_exact_match"] is False
    assert details["adapter_errors"] == []

    double_nor = {
        **topology,
        "verilog": (
            "module circuit(input A, input B, output Y); wire nor_out; "
            "assign nor_out = ~(A | B); assign Y = ~(nor_out); endmodule"
        ),
    }
    research_result, details = exp011._adapt_combinational_evidence(task, double_nor)
    assert research_result["candidate"]["truth_table"] == [
        {"A": 0, "B": 0, "reporter": 0},
        {"A": 0, "B": 1, "reporter": 1},
        {"A": 1, "B": 0, "reporter": 1},
        {"A": 1, "B": 1, "reporter": 1},
    ]
    assert details["truth_table_exact_match"] is True
    assert details["adapter_errors"] == []

    unsupported = {
        **topology,
        "verilog": (
            "module circuit(input A, input B, output Y); "
            "assign Y = A ? B : 1'b0; endmodule"
        ),
    }
    research_result, details = exp011._adapt_combinational_evidence(task, unsupported)
    assert research_result["candidate"].get("truth_table") is None
    assert details["truth_table_rows"] == 0
    assert details["truth_table_exact_match"] is False
    assert details["adapter_errors"] == [
        "verilog_simulation_error:unsupported_boolean_expression_token"
    ]


def test_temporal_adapter_requires_existing_valid_trace_and_never_synthesizes() -> None:
    valid = {
        "ode_trace": {
            "time": [0.0, 1.0, 2.0, 3.0],
            "output_protein": [0.0, 0.2, 0.8, 0.9],
        },
        "simulation_result": {"status": "simulated"},
        "benchmark_report": {"component_scores": {"temporal": 0.5}},
    }
    research_result, details = exp011._adapt_temporal_evidence(
        valid, "stateful_temporal"
    )
    assert research_result["candidate"]["ode_trace"] == valid["ode_trace"]
    assert research_result["simulation_result"]["status"] == "simulated"
    assert details == {
        "contract_version": "exp011-live-evaluator-adapter-v3",
        "mode": "stateful_temporal",
        "status": "evidence_ready",
        "source": "best_topology.ode_trace",
        "trace_sample_count": 4,
        "evidence_complete": True,
        "adapter_errors": [],
        "synthetic_trace_generated": False,
    }

    missing = {"simulation_result": {"status": "simulated"}}
    research_result, details = exp011._adapt_temporal_evidence(
        missing, "oscillatory_temporal"
    )
    assert research_result["candidate"].get("ode_trace") is None
    assert research_result["simulation_result"]["status"] == "incomplete_evidence"
    assert details["evidence_complete"] is False
    assert details["synthetic_trace_generated"] is False
    assert details["adapter_errors"] == ["missing_ode_trace"]

    malformed = {
        "ode_trace": {
            "time": [0.0, 2.0, 1.0],
            "output_protein": [0.0, float("nan"), 1.0],
        },
        "simulation_result": {"status": "simulated"},
    }
    research_result, details = exp011._adapt_temporal_evidence(
        malformed, "oscillatory_temporal"
    )
    assert research_result["simulation_result"]["status"] == "incomplete_evidence"
    assert details["adapter_errors"] == [
        "ode_trace_requires_finite_numeric_values",
    ]


def test_topology_summary_preserves_existing_ode_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_stub = ModuleType("schemas.state")
    state_stub.DesignState = type("DesignState", (), {})
    state_stub.SearchNode = type("SearchNode", (), {})
    monkeypatch.setitem(sys.modules, "schemas.state", state_stub)

    serializer_path = ROOT / "src" / "mcp_server" / "serializers.py"
    spec = importlib.util.spec_from_file_location(
        "exp011_serializers_under_test", serializer_path
    )
    assert spec is not None and spec.loader is not None
    serializers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serializers)

    trace = {
        "time": [0.0, 1.0, 2.0],
        "output_protein": [0.1, 0.5, 0.9],
    }
    topology = {
        "score": 0.8,
        "simulation_result": {"status": "simulated"},
        "ode_trace": trace,
        "internal_only": "excluded",
    }
    summary = serializers.summarize_topology(topology)

    assert summary["ode_trace"] == trace
    assert summary["simulation_result"] == {"status": "simulated"}
    assert "internal_only" not in summary
    assert summary["ode_trace"] is not trace


def test_preflight_freezes_contract_without_executing_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "preflight"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_exp011_benchmark.py",
            "--preflight-only",
            "--output-dir",
            str(output_dir),
            "--revision",
            "exp011_v3_preflight_test",
        ],
    )
    assert main() == 0
    payload = json.loads(
        (output_dir / "exp011_v3_preflight_test_preflight.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["provenance"]["evidence_class"] == "preflight_only"
    assert payload["provenance"]["comparison_eligible"] is False
    assert payload["preflight"]["status"] == "ready"
    freeze = payload["preflight"]["freeze_packet"]
    assert len(freeze["config_sha256"]) == 64
    assert freeze["config"]["routed_role_models"]["critic"] == (
        "gemini/gemini-3.5-flash"
    )
    assert freeze["config"]["routed_role_models"]["builder"] == (
        "gemini/gemini-2.5-flash"
    )
    assert freeze["config"]["evaluator_adapter_contract"] == (
        "exp011-live-evaluator-adapter-v3"
    )
    assert len(freeze["config"]["functional_scorer_sha256"]) == 64
    assert freeze["config"]["evidence_serializer_contract"] == (
        "exp011-evidence-serializer-v1"
    )
    assert len(freeze["config"]["evidence_serializer_sha256"]) == 64
    assert freeze["config"]["workflow_evidence_contract"] == "workflow-evidence-v1"
    assert len(freeze["config"]["workflow_evidence_contract_sha256"]) == 64
    assert freeze["environment"]["credential_value_recorded"] is False


def test_live_execution_is_blocked_without_cost_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "blocked"
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_exp011_benchmark.py", "--output-dir", str(output_dir)],
    )
    assert main() == 2
    payload = json.loads(
        (output_dir / "exp011_v3_preflight.json").read_text(encoding="utf-8")
    )
    assert payload["preflight"]["status"] == "blocked"
    assert "paid_live_run_not_authorized" in payload["preflight"]["errors"]
    assert "positive_live_cost_soft_stop_required" in payload["preflight"]["errors"]


def test_calibration_preflight_is_separate_cost_capped_and_non_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "calibration-preflight"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_exp011_benchmark.py",
            "--calibration-only",
            "--preflight-only",
            "--repeats",
            "1",
            "--max-total-cost-usd",
            "1.25",
            "--revision",
            "exp011_v3_calibration_r1",
            "--output-dir",
            str(output_dir),
        ],
    )
    assert main() == 0
    payload = json.loads(
        (output_dir / "exp011_v3_calibration_r1_preflight.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["provenance"]["evidence_class"] == "calibration_preflight_only"
    assert payload["provenance"]["comparison_eligible"] is False
    assert payload["provenance"]["acceptance_eligible"] is False
    freeze = payload["preflight"]["freeze_packet"]["config"]
    assert freeze["execution_profile"] == "calibration_non_acceptance"
    assert freeze["selected_task_ids"] == ["reporter_a_or_b_v1"]
    assert freeze["expected_workflow_rows"] == 2
    assert freeze["acceptance_contract"] is False
    assert freeze["live_cost_soft_stop_usd"] == 1.25


def test_calibration_profile_rejects_shared_revision_root_and_missing_cap() -> None:
    args = build_parser().parse_args(
        [
            "--calibration-only",
            "--preflight-only",
            "--repeats",
            "1",
        ]
    )
    errors = exp011._validate_args(args)
    assert "calibration_profile_requires_distinct_revision" in errors
    assert "calibration_profile_requires_distinct_output_root" in errors
    assert "positive_calibration_cost_soft_stop_required" in errors


def test_calibration_live_harness_emits_two_non_acceptance_rows_without_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "calibration-live-harness"

    def fake_live_workflow(
        task: object,
        model_string: str,
        task_output: Path,
        compute_budget: int,
        api_key: str,
        api_base: str | None,
    ) -> tuple[dict[str, object], list[dict[str, object]], float, None]:
        del task, compute_budget, api_key, api_base
        task_output.mkdir(parents=True, exist_ok=True)
        (task_output / "summary.json").write_text("{}", encoding="utf-8")
        called_model = (
            "gemini/gemini-2.5-flash"
            if model_string.startswith("routed:")
            else model_string
        )
        return (
            {"status": "success", "data": {"is_completed": True}},
            [
                {
                    "model": called_model,
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "latency_seconds": 0.01,
                    "status": "local_test_double",
                }
            ],
            0.01,
            None,
        )

    monkeypatch.setattr(exp011, "_load_live_settings", lambda: ("test-key", None))
    monkeypatch.setattr(exp011, "_run_live_workflow", fake_live_workflow)
    monkeypatch.setattr(
        exp011,
        "_evaluate_task",
        lambda task, result: (True, {"passed": True, "local_test_double": True}),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_exp011_benchmark.py",
            "--calibration-only",
            "--repeats",
            "1",
            "--max-total-cost-usd",
            "1.25",
            "--authorize-paid-live-run",
            "--revision",
            "exp011_v3_calibration_harness",
            "--output-dir",
            str(output_dir),
        ],
    )
    assert main() == 0
    payload = json.loads(
        (output_dir / "exp011_v3_calibration_harness_comparison.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["provenance"]["evidence_class"] == (
        "live_calibration_non_acceptance"
    )
    assert payload["provenance"]["comparison_eligible"] is False
    assert payload["provenance"]["acceptance_eligible"] is False
    assert payload["provenance"]["decision"] == "not_comparable"
    assert payload["provenance"]["invalid_reasons"] == [
        "calibration_non_acceptance_scope"
    ]
    assert len(payload["runs"]) == 2


def test_offline_exp011_benchmark_is_non_comparative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "offline"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_exp011_benchmark.py",
            "--offline",
            "--repeats",
            "2",
            "--revision",
            "exp011_v3_offline_test",
            "--output-dir",
            str(output_dir),
        ],
    )
    assert main() == 0
    payload = json.loads(
        (output_dir / "exp011_v3_offline_test_comparison.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["provenance"]["decision"] == "offline_synthetic_only"
    assert payload["provenance"]["evidence_class"] == "synthetic_harness"
    assert payload["provenance"]["comparison_eligible"] is False
    assert payload["provenance"]["invalid_reasons"] == [
        "missing_artifact_manifest_hash",
        "offline_synthetic_harness"
    ]
    assert len(payload["runs"]) == 20
    assert all(
        not run["mapping_gate"]["eligible_for_real_mapping_claim"]
        for run in payload["runs"]
    )
