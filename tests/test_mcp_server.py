from __future__ import annotations

import json
import importlib
import threading
import time
import sys
import types
from pathlib import Path

from mcp_server.run_store import RunStore
from mcp_server.service import (
    cancel_design_run,
    compare_design_revisions,
    compare_design_runs,
    design_circuit_quick,
    diagnose_design_run,
    evaluate_verilog,
    explain_design_run,
    export_design,
    get_design_ir,
    get_design_run_artifacts,
    get_design_run_result,
    get_design_run_status,
    get_design_run_events,
    get_design_run_progress,
    list_compatible_replacements,
    list_design_runs,
    start_design_run,
    submit_design_feedback,
    summarize_design_state,
    replace_design_part,
    validate_design_part_replacement,
)


def _wait_for_completed(fetch_result):
    result = fetch_result()
    for _ in range(100):
        if result["status"] == "completed":
            return result
        time.sleep(0.02)
        result = fetch_result()
    return result


def _wait_for_completed_topology(fetch_result):
    result = fetch_result()
    for _ in range(100):
        summary = result.get("summary", {})
        if (
            result.get("status") == "completed"
            and isinstance(summary, dict)
            and isinstance(summary.get("best_topology"), dict)
        ):
            return result
        time.sleep(0.02)
        result = fetch_result()
    return result


def test_evaluate_verilog_writes_agent_artifacts(tmp_path: Path) -> None:
    result = evaluate_verilog(
        "module genetic_circuit(input A, input B, output Y); assign Y = A & ~B; endmodule",
        enable_ode=False,
        output_dir=str(tmp_path),
    )

    assert result["status"] == "completed"
    artifacts = result["artifacts"]
    assert Path(artifacts["summary_json"]).exists()
    assert Path(artifacts["best_topology_json"]).exists()
    assert Path(artifacts["best_verilog"]).exists()
    assert Path(artifacts["run_summary_md"]).exists()
    assert Path(artifacts["manifest_json"]).exists()
    assert result["error"] is None
    assert result["error_type"] is None
    assert result["best_topology"]["mapping_status"] == "unmapped"
    manifest = json.loads(Path(artifacts["manifest_json"]).read_text(encoding="utf-8"))
    assert {item["key"] for item in manifest["artifacts"]} >= {"summary_json", "manifest_json"}


def test_artifact_manifest_entries_are_complete_and_exist(tmp_path: Path) -> None:
    result = evaluate_verilog(
        "module genetic_circuit(input A, input B, output Y); assign Y = A & ~B; endmodule",
        enable_ode=False,
        output_dir=str(tmp_path),
    )

    manifest = json.loads(Path(result["artifacts"]["manifest_json"]).read_text(encoding="utf-8"))
    entries = manifest["artifacts"]

    assert manifest["run_id"]
    assert manifest["created_at"]
    assert any(entry["key"] == "manifest_json" for entry in entries)
    for entry in entries:
        assert set(entry) == {"key", "path", "type", "description"}
        assert entry["key"]
        assert entry["type"]
        assert entry["description"]
        assert Path(entry["path"]).exists()


def test_service_validation_errors_use_standard_shape(tmp_path: Path) -> None:
    verilog_result = evaluate_verilog("", output_dir=str(tmp_path))
    design_result = design_circuit_quick(" ", output_dir=str(tmp_path))

    assert verilog_result["status"] == "error"
    assert verilog_result["error_type"] == "validation_error"
    assert verilog_result["summary"] == {}
    assert verilog_result["artifacts"] == {}
    assert design_result["status"] == "error"
    assert design_result["error_type"] == "validation_error"


def test_summarize_design_state_accepts_saved_state_shape() -> None:
    result = summarize_design_state(
        {
            "user_intent": "A and not B",
            "host_organism": "Escherichia coli",
            "is_completed": True,
            "is_approved": False,
            "requires_human_input": False,
            "pause_reason": None,
            "current_node_id": "root",
            "best_topology": {"score": 0.72, "mapping_status": "mapped"},
        }
    )

    assert result["status"] == "completed"
    assert result["summary"]["best_topology"]["score"] == 0.72


def test_run_store_background_task_persists_result(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path, max_workers=1)
    started = store.start(
        task=lambda: {
            "status": "completed",
            "summary": {"user_intent": "A and not B", "best_topology": {"score": 0.8}},
            "artifacts": {"summary_json": "summary.json"},
        },
        request={"user_intent": "A and not B", "api_key": "secret"},
    )

    run_id = started["run_id"]
    result = _wait_for_completed(lambda: store.result(run_id))

    status = store.status(run_id)
    assert status["status"] == "completed"
    assert status["summary"]["score"] == 0.8
    assert result["async_run_id"] == run_id
    assert Path(status["result_path"]).exists()
    metadata = json.loads((tmp_path / run_id / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["request"]["api_key"] == "***"
    assert "secret" not in json.dumps(metadata)


def test_run_store_persists_events_and_progress(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path, max_workers=1)
    started = store.start(
        task=lambda: {"status": "completed", "summary": {"user_intent": "events"}},
        request={"user_intent": "events"},
    )
    run_id = started["run_id"]
    store.append_event(run_id, "translator", "running", 0.4, "Translating.")
    _wait_for_completed(lambda: store.result(run_id))

    events = get_design_run_events(run_id, run_store=store)
    progress = get_design_run_progress(run_id, run_store=store)

    assert events["count"] >= 3
    assert [item["event_id"] for item in events["events"]] == sorted(
        item["event_id"] for item in events["events"]
    )
    assert any(item["stage"] == "translator" for item in events["events"])
    assert progress["progress"] == 1.0
    assert progress["event_count"] == events["events"][-1]["event_id"]


def test_run_store_lists_runs_newest_first_and_limits(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path, max_workers=1)
    first = store.start(task=lambda: {"status": "completed"}, request={"user_intent": "first"})
    time.sleep(0.01)
    second = store.start(task=lambda: {"status": "completed"}, request={"user_intent": "second"})

    _wait_for_completed(lambda: store.result(first["run_id"]))
    _wait_for_completed(lambda: store.result(second["run_id"]))

    listed = store.list_runs(limit=1)
    assert listed["status"] == "completed"
    assert listed["count"] == 1
    assert listed["total"] == 2
    assert listed["runs"][0]["run_id"] == second["run_id"]
    assert store.list_runs(limit=0)["count"] == 1
    assert store.list_runs(limit=101)["count"] == 2


def test_run_store_cancel_running_task_reports_request(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path, max_workers=1)
    release = threading.Event()

    def slow_task():
        release.wait(1)
        return {"status": "completed", "summary": {"user_intent": "slow"}}

    started = store.start(task=slow_task, request={"user_intent": "slow"})
    run_id = started["run_id"]
    time.sleep(0.05)

    cancelled = store.cancel(run_id)
    assert cancelled["status"] in {"cancellation_requested", "cancelled", "completed"}
    assert cancelled["error_type"] in {None, "cancelled"}

    release.set()
    result = _wait_for_completed(lambda: store.result(run_id))
    assert result["status"] in {"completed", "cancelled"}


def test_run_store_result_for_unfinished_run_has_standard_envelope(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path, max_workers=1)
    release = threading.Event()

    started = store.start(task=lambda: {"status": "completed"} if release.wait(1) else {"status": "completed"}, request={})
    result = store.result(started["run_id"])
    release.set()

    assert result["status"] in {"queued", "running"}
    assert result["error"] is None
    assert result["error_type"] is None
    assert result["summary"] == {}
    assert result["artifacts"] == {}


def test_service_artifact_lookup_returns_manifest(monkeypatch, tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path / "async", max_workers=1)
    workflow_dir = tmp_path / "workflow"
    workflow_dir.mkdir()
    manifest_path = workflow_dir / "manifest.json"
    manifest_path.write_text(json.dumps({"run_id": "workflow", "artifacts": []}), encoding="utf-8")

    def fake_design_circuit_quick(**kwargs):
        return {
            "status": "completed",
            "run_dir": str(workflow_dir),
            "summary": {"user_intent": kwargs["user_intent"]},
            "artifacts": {"manifest_json": str(manifest_path), "summary_json": str(workflow_dir / "summary.json")},
            "error": None,
            "error_type": None,
        }

    monkeypatch.setattr("mcp_server.service.design_circuit_quick", fake_design_circuit_quick)
    started = start_design_run(user_intent="A and not B", run_store=store)
    run_id = started["run_id"]
    _wait_for_completed(lambda: get_design_run_result(run_id, run_store=store))

    artifacts = get_design_run_artifacts(run_id, run_store=store)
    missing = get_design_run_artifacts("missing", run_store=store)

    assert artifacts["status"] == "completed"
    assert artifacts["manifest"]["run_id"] == "workflow"
    assert "manifest_json" in artifacts["artifacts"]
    assert missing["status"] == "not_found"
    assert missing["error_type"] == "not_found"


def test_service_async_design_run_uses_background_store(monkeypatch, tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path, max_workers=1)

    def fake_design_circuit_quick(**kwargs):
        return {
            "status": "completed",
            "run_dir": str(tmp_path / "workflow"),
            "summary": {
                "user_intent": kwargs["user_intent"],
                "host_organism": kwargs["host_organism"],
                "is_completed": True,
                "best_topology": {"score": 0.91, "mapping_status": "unmapped"},
            },
            "artifacts": {"summary_json": str(tmp_path / "summary.json")},
        }

    monkeypatch.setattr("mcp_server.service.design_circuit_quick", fake_design_circuit_quick)

    started = start_design_run(
        user_intent="A and not B",
        host_organism="E. coli",
        run_store=store,
    )
    run_id = started["run_id"]
    result = _wait_for_completed(lambda: get_design_run_result(run_id, run_store=store))

    status = get_design_run_status(run_id, run_store=store)
    assert status["status"] == "completed"
    assert status["workflow_run_dir"] == str(tmp_path / "workflow")
    assert result["summary"]["best_topology"]["score"] == 0.91


def test_service_list_and_cancel_validate_inputs(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path, max_workers=1)

    assert list_design_runs(limit="bad", run_store=store)["error_type"] == "validation_error"
    assert cancel_design_run("", run_store=store)["error_type"] == "validation_error"
    assert cancel_design_run("missing", run_store=store)["error_type"] == "not_found"
    assert get_design_run_status("missing", run_store=store)["error_type"] == "not_found"


def test_cancel_completed_run_does_not_overwrite_result(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path, max_workers=1)
    started = store.start(
        task=lambda: {"status": "completed", "summary": {"user_intent": "done"}},
        request={"user_intent": "done"},
    )
    run_id = started["run_id"]
    original_result = _wait_for_completed(lambda: store.result(run_id))

    cancelled = cancel_design_run(run_id, run_store=store)
    after_cancel_result = store.result(run_id)

    assert cancelled["status"] == "completed"
    assert "already terminal" in cancelled["message"]
    assert after_cancel_result == original_result


def test_compare_design_runs_ranks_completed_runs(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path, max_workers=1)
    low = store.start(
        task=lambda: {
            "status": "completed",
            "summary": {
                "best_topology": {
                    "score": 0.4,
                    "mapping_status": "unmapped",
                    "ode_status": "disabled",
                    "robustness_score": 0.3,
                }
            },
            "artifacts": {"summary_json": "low-summary.json"},
        },
        request={"user_intent": "low"},
    )
    high = store.start(
        task=lambda: {
            "status": "completed",
            "summary": {
                "best_topology": {
                    "score": 0.9,
                    "mapping_status": "mapped",
                    "ode_status": "completed",
                    "cello_buildable": True,
                    "robustness_score": 0.8,
                    "toxicity_score": 0.9,
                    "semantic_faithfulness_score": 0.95,
                }
            },
            "artifacts": {"summary_json": "high-summary.json", "manifest_json": "manifest.json"},
        },
        request={"user_intent": "high"},
    )
    low_result = _wait_for_completed_topology(lambda: store.result(low["run_id"]))
    high_result = _wait_for_completed_topology(lambda: store.result(high["run_id"]))

    assert low_result["summary"]["best_topology"]["score"] == 0.4
    assert high_result["summary"]["best_topology"]["score"] == 0.9

    compared = compare_design_runs([low["run_id"], high["run_id"]], run_store=store)

    assert compared["status"] == "completed"
    assert compared["summary"]["best_run_id"] == high["run_id"]
    assert compared["best_run"]["score"] == 0.9
    assert [item["rank"] for item in compared["ranked_runs"]] == [1, 2]
    assert compared["ranked_runs"][0]["run_id"] == high["run_id"]


def test_terminal_event_failure_does_not_rewrite_completed_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = RunStore(base_dir=tmp_path, max_workers=1)
    original_append_event = store.append_event

    def append_event(
        run_id,
        stage,
        status,
        progress,
        message,
        details=None,
    ):
        if status == "completed":
            raise OSError("simulated terminal event write failure")
        return original_append_event(
            run_id,
            stage,
            status,
            progress,
            message,
            details,
        )

    monkeypatch.setattr(store, "append_event", append_event)
    started = store.start(
        task=lambda: {
            "status": "completed",
            "summary": {"best_topology": {"score": 0.8}},
            "artifacts": {},
        },
        request={"user_intent": "terminal event resilience"},
    )
    store._futures[started["run_id"]].result(timeout=10)

    result = store.result(started["run_id"])

    assert result["status"] == "completed"
    assert result["summary"]["best_topology"]["score"] == 0.8


def test_compare_design_runs_reports_unavailable_runs(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path, max_workers=1)
    release = threading.Event()
    complete = store.start(
        task=lambda: {"status": "completed", "summary": {"best_topology": {"score": 0.7}}},
        request={"user_intent": "complete"},
    )
    unfinished = store.start(
        task=lambda: {"status": "completed"} if release.wait(1) else {"status": "completed"},
        request={"user_intent": "unfinished"},
    )
    _wait_for_completed(lambda: store.result(complete["run_id"]))

    compared = compare_design_runs([complete["run_id"], unfinished["run_id"], "missing"], run_store=store)
    release.set()

    assert compared["status"] == "completed"
    assert compared["summary"]["available_run_count"] == 1
    assert {item["run_id"] for item in compared["unavailable_runs"]} == {unfinished["run_id"], "missing"}


def test_compare_design_runs_validates_run_id_count(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path, max_workers=1)

    assert compare_design_runs(["one"], run_store=store)["error_type"] == "validation_error"
    assert compare_design_runs([str(index) for index in range(11)], run_store=store)["error_type"] == "validation_error"


def test_diagnose_design_run_reports_healthy_completed_run(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path, max_workers=1)
    started = store.start(
        task=lambda: {
            "status": "completed",
            "summary": {
                "requires_human_input": False,
                "best_topology": {
                    "score": 0.92,
                    "mapping_status": "mapped",
                    "ode_status": "completed",
                    "robustness_score": 0.8,
                    "toxicity_score": 0.9,
                    "semantic_faithfulness_score": 0.9,
                },
            },
            "artifacts": {"summary_json": "summary.json"},
        },
        request={"user_intent": "healthy"},
    )
    _wait_for_completed(lambda: store.result(started["run_id"]))

    diagnosis = diagnose_design_run(started["run_id"], run_store=store)

    assert diagnosis["status"] == "completed"
    assert diagnosis["diagnosis_status"] == "healthy"
    assert diagnosis["findings"] == []
    assert diagnosis["recommended_next_actions"] == ["No immediate action is required; keep the run as a viable candidate."]


def test_diagnose_design_run_flags_mapping_human_input_and_low_metrics(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path, max_workers=1)
    started = store.start(
        task=lambda: {
            "status": "completed",
            "summary": {
                "requires_human_input": True,
                "pause_reason": "compute_budget_exceeded",
                "human_feedback_prompt": "Need constraints",
                "latest_critic_feedback": "Mapping and robustness need work.",
                "failed_attempts": [{"error_type": "PART_ERROR"}],
                "best_topology": {
                    "score": 0.2,
                    "mapping_status": "failed",
                    "ode_status": "disabled",
                    "robustness_score": 0.3,
                    "toxicity_score": 0.2,
                    "semantic_faithfulness_score": 0.4,
                },
            },
            "artifacts": {"summary_json": "summary.json"},
        },
        request={"user_intent": "needs work"},
    )
    _wait_for_completed(lambda: store.result(started["run_id"]))

    diagnosis = diagnose_design_run(started["run_id"], run_store=store)
    categories = {finding["category"] for finding in diagnosis["findings"]}

    assert diagnosis["diagnosis_status"] == "needs_attention"
    assert {"human_input", "mapping", "ode", "score", "robustness", "toxicity", "semantics", "critic", "search"} <= categories
    assert diagnosis["summary"]["high_severity_count"] >= 1
    assert any("Cello" in action for action in diagnosis["recommended_next_actions"])


def test_diagnose_design_run_flags_missing_topology(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path, max_workers=1)
    started = store.start(
        task=lambda: {"status": "completed", "summary": {"current_node_id": "root"}, "artifacts": {}},
        request={"user_intent": "missing topology"},
    )
    _wait_for_completed(lambda: store.result(started["run_id"]))

    diagnosis = diagnose_design_run(started["run_id"], run_store=store)

    assert diagnosis["diagnosis_status"] == "needs_attention"
    assert any(finding["category"] == "topology" for finding in diagnosis["findings"])


def test_explain_design_run_returns_selected_review_sections_and_artifacts(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path / "async", max_workers=1)
    workflow_dir = tmp_path / "workflow"
    workflow_dir.mkdir()
    started = store.start(
        task=lambda: {
            "status": "completed",
            "run_dir": str(workflow_dir),
            "summary": {
                "user_intent": "A and not B",
                "host_organism": "Escherichia coli",
                "requires_human_input": False,
                "current_node_id": "root_repair",
                "tree_summary": [
                    {
                        "node_id": "root",
                        "parent_id": None,
                        "children_ids": ["root_repair"],
                        "search_mode": "Exploration",
                        "status": "Evaluated",
                        "score": 0.48,
                        "is_approved": False,
                        "error_type": "LOGIC_ERROR",
                        "critic_feedback": "Need clearer logic.",
                    },
                    {
                        "node_id": "root_repair",
                        "parent_id": "root",
                        "children_ids": [],
                        "search_mode": "Repair",
                        "status": "Pass",
                        "score": 0.74,
                        "is_approved": True,
                        "error_type": "NONE",
                        "critic_feedback": "Repair improved the candidate.",
                    },
                ],
                "best_topology": {
                    "source": "mock_cello_wrapper",
                    "cello_mode": "mock",
                    "cello_claim_level": "mock_only",
                    "cello_warning": "Mock output.",
                    "score": 0.74,
                    "mapping_status": "unmapped",
                    "ode_status": "simulated",
                    "ode_trace": {
                        "time": [0, 10, 20],
                        "output_protein": [1, 4, 8],
                        "total_mrna": [0, 1, 2],
                        "total_protein": [1, 5, 9],
                        "rnap_occupancy": [0.1, 0.2, 0.3],
                        "ribosome_occupancy": [0.2, 0.3, 0.4],
                    },
                    "functional_score": 0.91,
                    "metabolic_burden_score": 0.52,
                    "cello_assignment_score": 0.3,
                    "gate_count": 5,
                },
            },
            "artifacts": {"summary_json": str(workflow_dir / "summary.json")},
        },
        request={"user_intent": "A and not B"},
    )
    _wait_for_completed(lambda: store.result(started["run_id"]))

    explanation = explain_design_run(
        started["run_id"],
        profile="review",
        max_items_per_section=2,
        run_store=store,
    )

    assert explanation["status"] == "completed"
    assert explanation["summary"]["profile"] == "review"
    assert "score_explanation" in explanation["explanation"]
    assert "decision_trace" in explanation["explanation"]
    assert explanation["explanation"]["score_explanation"]["top_strengths"][0]["component"] == "functional"
    assert explanation["explanation"]["score_explanation"]["main_limitations"][0]["component"] == "cello_assignment"
    assert explanation["explanation"]["headline"]["cello_claim_level"] == "mock_only"
    assert any("Mock" in caveat for caveat in explanation["explanation"]["biological_caveats"])
    assert explanation["explanation"]["ode_explanation"]["key_readouts"]["peak_output_protein"] == 8
    assert "ode_explanation_json" in explanation["explanation_artifacts"]
    assert [item["node_id"] for item in explanation["explanation"]["decision_trace"]] == ["root", "root_repair"]
    assert Path(explanation["explanation_artifacts"]["score_explanation_json"]).exists()
    assert Path(explanation["explanation_artifacts"]["design_rationale_md"]).exists()


def test_explain_design_run_can_return_single_section_without_writing_artifacts(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path, max_workers=1)
    started = store.start(
        task=lambda: {
            "status": "completed",
            "summary": {
                "best_topology": {
                    "score": 0.88,
                    "mapping_status": "mapped",
                    "ode_status": "completed",
                    "robustness_score": 0.81,
                }
            },
            "artifacts": {},
        },
        request={"user_intent": "brief"},
    )
    _wait_for_completed(lambda: store.result(started["run_id"]))

    explanation = explain_design_run(
        started["run_id"],
        profile="brief",
        sections=["score"],
        write_artifacts=False,
        run_store=store,
    )

    assert explanation["status"] == "completed"
    assert explanation["explanation"]["sections"] == ["score"]
    assert "score_explanation" in explanation["explanation"]
    assert "decision_trace" not in explanation["explanation"]
    assert explanation["explanation_artifacts"] == {}


def test_explain_design_run_validates_options(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path, max_workers=1)

    assert explain_design_run("", run_store=store)["error_type"] == "validation_error"
    assert explain_design_run("missing", profile="bad", run_store=store)["error_type"] == "validation_error"
    assert explain_design_run("missing", sections=["bad"], run_store=store)["error_type"] == "validation_error"


def test_design_ir_replacement_diff_and_export_tools(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path / "async", max_workers=1)
    workflow_dir = tmp_path / "workflow"
    workflow_dir.mkdir()
    state_path = workflow_dir / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "user_intent": "Express GFP when A is present",
                "host_organism": "Escherichia coli",
                "best_topology": {
                    "verilog": "module c(input A, output GFP); assign GFP = A; endmodule",
                    "cello_mode": "mock",
                    "mapping_status": "unmapped",
                },
            }
        ),
        encoding="utf-8",
    )
    started = store.start(
        task=lambda: {
            "status": "completed",
            "run_dir": str(workflow_dir),
            "summary": {"best_topology": {"score": 0.5}},
            "artifacts": {"state_json": str(state_path)},
        },
        request={"user_intent": "design tools"},
    )
    run_id = started["run_id"]
    _wait_for_completed(lambda: store.result(run_id))

    initial = get_design_ir(run_id, run_store=store)
    candidates = list_compatible_replacements(
        run_id,
        "output_cds_GFP",
        run_store=store,
    )
    validation = validate_design_part_replacement(
        run_id,
        "output_cds_GFP",
        "DEMO_GFP_CDS",
        run_store=store,
    )
    replaced = replace_design_part(
        run_id,
        "output_cds_GFP",
        "DEMO_GFP_CDS",
        run_store=store,
    )
    compared = compare_design_revisions(
        run_id,
        initial["revision_id"],
        replaced["summary"]["revision_id"],
        run_store=store,
    )
    exported = export_design(
        run_id,
        replaced["summary"]["revision_id"],
        formats=["bom", "sbol3"],
        run_store=store,
    )

    assert initial["status"] == "completed"
    assert any(item["id"] == "DEMO_GFP_CDS" for item in candidates["replacements"])
    assert validation["validation"]["valid"] is True
    assert replaced["summary"]["replaced"] is True
    assert compared["diff"]["part_changes"]
    assert exported["summary"]["ready_count"] == 2
    assert Path(exported["artifacts"]["bom"]).exists()
    assert Path(exported["artifacts"]["sbol3"]).exists()


def test_submit_design_feedback_persists_guidance(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path / "async", max_workers=1)
    started = store.start(
        task=lambda: {
            "status": "needs_human_input",
            "summary": {"requires_human_input": True},
            "artifacts": {},
        },
        request={"user_intent": "paused"},
    )
    run_id = started["run_id"]
    for _ in range(100):
        result = store.result(run_id)
        if result["status"] == "needs_human_input":
            break
        time.sleep(0.01)

    feedback = submit_design_feedback(
        run_id,
        ["Prefer low burden parts"],
        action="repair",
        extra_budget=3,
        run_store=store,
    )

    assert feedback["status"] == "completed"
    assert feedback["summary"]["ready_to_resume"] is True
    assert Path(feedback["artifacts"]["human_feedback_json"]).exists()


def test_mcp_server_registers_expected_tools_without_real_mcp(monkeypatch) -> None:
    registered_tools = {}

    class FakeFastMCP:
        def __init__(self, name):
            self.name = name
            self.tools = registered_tools

        def tool(self):
            def decorator(func):
                self.tools[func.__name__] = func
                return func

            return decorator

        def run(self):
            return None

    fake_mcp = types.ModuleType("mcp")
    fake_server = types.ModuleType("mcp.server")
    fake_fastmcp = types.ModuleType("mcp.server.fastmcp")
    fake_fastmcp.FastMCP = FakeFastMCP
    fake_mcp.server = fake_server
    fake_server.fastmcp = fake_fastmcp

    monkeypatch.setitem(sys.modules, "mcp", fake_mcp)
    monkeypatch.setitem(sys.modules, "mcp.server", fake_server)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fake_fastmcp)

    import mcp_server.server as server_module

    importlib.reload(server_module)

    assert set(registered_tools) == {
        "design_genetic_circuit_quick",
        "evaluate_cello_verilog",
        "start_design_run",
        "get_design_run_status",
        "get_design_run_events",
        "get_design_run_progress",
        "get_design_run_result",
        "list_design_runs",
        "list_tool_capabilities",
        "cancel_design_run",
        "get_design_run_artifacts",
        "compare_design_runs",
        "diagnose_design_run",
        "explain_design_run",
        "submit_design_feedback",
        "resume_design_run",
        "get_design_ir",
        "list_compatible_replacements",
        "validate_design_part_replacement",
        "replace_design_part",
        "compare_design_revisions",
        "export_design",
        "summarize_mcp_design_state",
    }
    captured = {}

    def fake_design(**kwargs):
        captured.update(kwargs)
        return {"status": "error", "error_type": "cello21_program_blocked"}

    monkeypatch.setattr(server_module, "design_circuit_quick", fake_design)
    result = registered_tools["design_genetic_circuit_quick"](
        "AND gate", cello_artifact_format="cello21"
    )
    assert result["error_type"] == "cello21_program_blocked"
    assert captured["cello_artifact_format"] == "cello21"

    captured.clear()

    def fake_resume(*args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return {"status": "queued"}

    monkeypatch.setattr(server_module, "service_resume_design_run", fake_resume)
    result = registered_tools["resume_design_run"]("run_parent")
    assert result["status"] == "queued"
    assert captured["cello_artifact_format"] is None
