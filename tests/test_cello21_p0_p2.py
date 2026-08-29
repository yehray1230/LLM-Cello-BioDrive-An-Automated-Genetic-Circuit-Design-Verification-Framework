from __future__ import annotations

import json
import inspect
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import application.services as application_services
import mcp_server.service as workflow_service
from application.secret_store import ProcessSecretStore
from application.settings import SettingsService
from api.schemas import RunResumeRequest, RunStartRequest, SettingsUpdateRequest
from mcp_server.cello21_program import (
    Cello21ProgramBlocked,
    Cello21ProgramPolicy,
    Cello21ProgramRunner,
    ProviderCallResult,
)
from web.routes import run_resume as web_run_resume


def _policy(**overrides: object) -> Cello21ProgramPolicy:
    values: dict[str, object] = {
        "program_id": "CELLO21-P0-P2-R3",
        "experiment_id": "C21-INTEGRATED-AND2-R3",
        "active_experiment_id": None,
        "execution_authorized": False,
        "network_authorized": False,
        "paid_api_authorized": False,
        "cello_mapping_authorized": False,
        "retry_authorized": False,
        "maximum_paid_calls": 3,
        "maximum_mapping_runs": 1,
        "paid_cost_cap_usd": 0.0,
        "paid_cost_cap_twd": 0.0,
        "budget_exchange_rate_twd_per_usd": 35.0,
        "per_call_reservation_usd": 0.030432,
    }
    values.update(overrides)
    return Cello21ProgramPolicy(**values)  # type: ignore[arg-type]


def test_inactive_runner_never_invokes_provider_or_mapping(tmp_path: Path) -> None:
    runner = Cello21ProgramRunner(_policy(), tmp_path / "ledger.jsonl")
    called = []
    with pytest.raises(Cello21ProgramBlocked, match="active experiment"):
        runner.run_provider_step("builder", lambda: called.append("provider"))
    with pytest.raises(Cello21ProgramBlocked, match="active experiment"):
        runner.run_mapping_step("mapping", lambda: called.append("mapping"))
    assert called == []
    assert not runner.ledger_path.exists()


def test_provider_reservation_hard_stops_before_callback(tmp_path: Path) -> None:
    runner = Cello21ProgramRunner(
        _policy(
            active_experiment_id="C21-INTEGRATED-AND2-R3",
            execution_authorized=True,
            network_authorized=True,
            paid_api_authorized=True,
            paid_cost_cap_usd=0.05,
            paid_cost_cap_twd=2.0,
        ),
        tmp_path / "ledger.jsonl",
    )
    result = ProviderCallResult(
        value="ok",
        request_id="req_fixture",
        model="gpt-fixture",
        input_tokens=10,
        cached_input_tokens=0,
        output_tokens=5,
        actual_cost_usd=0.02,
    )
    assert runner.run_provider_step("builder", lambda: result) == "ok"
    called = []
    with pytest.raises(Cello21ProgramBlocked, match="USD cost cap"):
        runner.run_provider_step("translator", lambda: called.append("called"))
    assert called == []
    events = [json.loads(line) for line in runner.ledger_path.read_text().splitlines()]
    assert [event["event"] for event in events] == [
        "provider_reserved",
        "provider_finished",
    ]


def test_mapping_is_single_attempt_and_failure_is_not_retried(tmp_path: Path) -> None:
    runner = Cello21ProgramRunner(
        _policy(
            active_experiment_id="C21-INTEGRATED-AND2-R3",
            execution_authorized=True,
            cello_mapping_authorized=True,
        ),
        tmp_path / "ledger.jsonl",
    )
    calls = 0

    def fail() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("fixture failure")

    with pytest.raises(RuntimeError, match="fixture failure"):
        runner.run_mapping_step("mapping", fail)
    with pytest.raises(Cello21ProgramBlocked, match="run cap"):
        runner.run_mapping_step("mapping", fail)
    assert calls == 1


def test_retry_authority_blocks_before_any_external_callback(tmp_path: Path) -> None:
    runner = Cello21ProgramRunner(
        _policy(
            active_experiment_id="C21-INTEGRATED-AND2-R3",
            execution_authorized=True,
            network_authorized=True,
            paid_api_authorized=True,
            retry_authorized=True,
            paid_cost_cap_usd=1.0,
            paid_cost_cap_twd=35.0,
        ),
        tmp_path / "ledger.jsonl",
    )
    called = []
    with pytest.raises(Cello21ProgramBlocked, match="retry"):
        runner.run_provider_step("builder", lambda: called.append("called"))  # type: ignore[arg-type]
    assert called == []


def test_observed_cost_over_reservation_is_recorded_then_stops(
    tmp_path: Path,
) -> None:
    runner = Cello21ProgramRunner(
        _policy(
            active_experiment_id="C21-INTEGRATED-AND2-R3",
            execution_authorized=True,
            network_authorized=True,
            paid_api_authorized=True,
            paid_cost_cap_usd=1.0,
            paid_cost_cap_twd=35.0,
        ),
        tmp_path / "ledger.jsonl",
    )
    result = ProviderCallResult(
        value="not-returned",
        request_id="req_overrun",
        model="gpt-fixture",
        input_tokens=10,
        cached_input_tokens=0,
        output_tokens=5,
        actual_cost_usd=0.04,
    )
    with pytest.raises(Cello21ProgramBlocked, match="exceeded"):
        runner.run_provider_step("builder", lambda: result)
    events = [json.loads(line) for line in runner.ledger_path.read_text().splitlines()]
    assert events[-1]["reservation_exceeded"] is True
    assert events[-1]["actual_cost_usd"] == 0.04


def test_ledger_for_another_experiment_fails_closed(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "schema_version": "cello21.program-ledger.v1",
                "program_id": "CELLO21-P0-P2-R3",
                "experiment_id": "DIFFERENT",
                "event": "provider_reserved",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(Cello21ProgramBlocked, match="different program or experiment"):
        Cello21ProgramRunner(_policy(), ledger).preflight()


@pytest.mark.parametrize(
    "payload, message",
    [
        (
            {
                "schema_version": "wrong",
                "program_id": "CELLO21-P0-P2-R3",
                "experiment_id": "C21-INTEGRATED-AND2-R3",
                "timestamp_utc": "2026-08-27T00:00:00+00:00",
                "event": "mapping_reserved",
                "attempt_id": "a1",
                "stage": "mapping",
            },
            "schema version",
        ),
        (
            {
                "schema_version": "cello21.program-ledger.v1",
                "program_id": "CELLO21-P0-P2-R3",
                "experiment_id": "C21-INTEGRATED-AND2-R3",
                "timestamp_utc": "2026-08-27T00:00:00+00:00",
                "event": "provider_finished",
                "attempt_id": "a1",
                "stage": "builder",
                "status": "failed_no_retry",
                "error_type": "RuntimeError",
            },
            "unreserved attempt",
        ),
    ],
)
def test_corrupted_ledger_fails_closed(
    tmp_path: Path, payload: dict[str, object], message: str
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(Cello21ProgramBlocked, match=message):
        Cello21ProgramRunner(_policy(), ledger).preflight()


def test_incomplete_attempt_blocks_a_second_runner_before_callback(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    policy = _policy(
        active_experiment_id="C21-INTEGRATED-AND2-R3",
        execution_authorized=True,
        cello_mapping_authorized=True,
    )
    entered = threading.Event()
    release = threading.Event()
    first = Cello21ProgramRunner(policy, ledger)
    second = Cello21ProgramRunner(policy, ledger)
    errors: list[BaseException] = []

    def slow_mapping() -> str:
        entered.set()
        assert release.wait(timeout=5)
        return "mapped"

    def run_first() -> None:
        try:
            assert first.run_mapping_step("mapping", slow_mapping) == "mapped"
        except BaseException as exc:  # pragma: no cover - diagnostic capture
            errors.append(exc)

    thread = threading.Thread(target=run_first)
    thread.start()
    assert entered.wait(timeout=5)
    called: list[str] = []
    with pytest.raises(Cello21ProgramBlocked, match="incomplete attempt"):
        second.run_mapping_step("mapping", lambda: called.append("called"))
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert errors == []
    assert called == []


def test_existing_cross_runner_lock_fails_closed(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    ledger.with_name("ledger.jsonl.lock").write_text("held", encoding="utf-8")
    runner = Cello21ProgramRunner(
        _policy(
            active_experiment_id="C21-INTEGRATED-AND2-R3",
            execution_authorized=True,
            cello_mapping_authorized=True,
        ),
        ledger,
    )
    with pytest.raises(Cello21ProgramBlocked, match="locked by another runner"):
        runner.run_mapping_step("mapping", lambda: "forbidden")


def test_preflight_does_not_expose_local_ledger_path(tmp_path: Path) -> None:
    runner = Cello21ProgramRunner(_policy(), tmp_path / "secret" / "ledger.jsonl")
    payload = runner.preflight()
    assert "ledger_path" not in payload
    assert str(tmp_path) not in json.dumps(payload)


def test_policy_from_payloads_requires_exact_active_identity() -> None:
    policy = Cello21ProgramPolicy.from_payloads(
        state={
            "program_id": "CELLO21-P0-P2-R3",
            "active_experiment_id": "DIFFERENT",
            "execution_authorized": True,
            "cello_mapping_authorized": True,
            "paid_cost_cap_usd": 1.0,
            "paid_cost_cap_twd": 35.0,
        },
        budget={
            "maximum_paid_calls": 3,
            "budget_exchange_rate_twd_per_usd": 35.0,
            "per_call_reservation": {"reserved_total_cost_usd": 0.03},
        },
        experiment_id="C21-INTEGRATED-AND2-R3",
        maximum_mapping_runs=1,
    )
    with pytest.raises(Cello21ProgramBlocked, match="active experiment"):
        policy.require("mapping")


def test_settings_and_api_preserve_explicit_cello21_mode(tmp_path: Path) -> None:
    service = SettingsService(
        tmp_path / "settings.json",
        secret_store=ProcessSecretStore(str(tmp_path / "settings-secret")),
    )
    service.save_settings({"cello_artifact_format": "cello21"})
    assert service.get_settings_raw()["cello_artifact_format"] == "cello21"
    assert (
        SettingsUpdateRequest(cello_artifact_format="cello21").cello_artifact_format
        == "cello21"
    )
    assert RunStartRequest(
        user_intent="AND gate", cello_artifact_format="cello21"
    ).cello_artifact_format == "cello21"
    assert RunResumeRequest(
        cello_artifact_format="cello21"
    ).cello_artifact_format == "cello21"
    with pytest.raises(ValidationError):
        RunStartRequest(user_intent="AND gate", cello_artifact_format="auto")
    with pytest.raises(ValueError, match="cello_artifact_format"):
        service.save_settings({"cello_artifact_format": "auto"})


def test_run_service_injects_cello21_from_saved_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = SettingsService(
        tmp_path / "settings.json",
        secret_store=ProcessSecretStore(str(tmp_path / "run-secret")),
    )
    settings.save_settings({"cello_artifact_format": "cello21"})
    captured: dict[str, object] = {}

    def fake_start(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"status": "queued"}

    monkeypatch.setattr(application_services, "start_design_run", fake_start)
    service = application_services.RunService(object(), settings)  # type: ignore[arg-type]
    assert service.start({"user_intent": "AND gate"})["status"] == "queued"
    assert captured["cello_artifact_format"] == "cello21"


def test_run_service_resume_preserves_omitted_format_for_parent_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = SettingsService(
        tmp_path / "settings.json",
        secret_store=ProcessSecretStore(str(tmp_path / "resume-secret")),
    )
    settings.save_settings({"cello_artifact_format": "cello21"})
    captured: dict[str, object] = {}

    def fake_resume(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"status": "queued"}

    monkeypatch.setattr(application_services, "resume_design_run", fake_resume)
    service = application_services.RunService(object(), settings)  # type: ignore[arg-type]
    assert service.resume("run_fixture") == {"status": "queued"}
    assert captured["cello_artifact_format"] is None


def test_run_service_resume_preserves_explicit_format_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_resume(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"status": "queued"}

    monkeypatch.setattr(application_services, "resume_design_run", fake_resume)
    service = application_services.RunService(object())  # type: ignore[arg-type]
    assert service.resume("run_fixture", cello_artifact_format="cello21") == {
        "status": "queued"
    }
    assert captured["cello_artifact_format"] == "cello21"


@pytest.mark.parametrize(
    ("parent_request", "explicit_format", "expected_format"),
    [
        ({"cello_artifact_format": "cello21"}, None, "cello21"),
        ({"cello_artifact_format": "cello21"}, "cello_v2", "cello_v2"),
        ({}, None, "cello_v2"),
    ],
)
def test_resume_service_resolves_parent_format_and_propagates_to_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    parent_request: dict[str, object],
    explicit_format: str | None,
    expected_format: str,
) -> None:
    run_dir = tmp_path / "parent"
    run_dir.mkdir()
    state_path = run_dir / "state.json"
    state_path.write_text("{}", encoding="utf-8")
    (run_dir / "human_feedback.json").write_text(
        json.dumps({"action": "repair", "constraints": [], "extra_budget": 0}),
        encoding="utf-8",
    )
    manifest_path = run_dir / "run_manifest.json"
    manifest_path.write_text(
        json.dumps({"request": parent_request}), encoding="utf-8"
    )
    captured: dict[str, object] = {}

    class FakeStore:
        def result(self, run_id: str) -> dict[str, object]:
            return {
                "status": "needs_human_input",
                "artifacts": {"state_json": str(state_path)},
            }

        def status(self, run_id: str) -> dict[str, object]:
            return {
                "run_dir": str(run_dir),
                "run_manifest_path": str(manifest_path),
            }

        def start(self, *, task, request, run_id):
            captured["child_request"] = request
            captured["task_result"] = task()
            return {"status": "queued", "run_id": run_id}

        def append_event(self, *args, **kwargs) -> None:
            return None

    state = SimpleNamespace(
        user_intent="AND gate",
        host_organism="Escherichia coli",
        compute_budget=2,
        human_constraints=[],
        requires_human_input=True,
        pause_reason="review",
        human_feedback_prompt="review",
        last_error="paused",
        is_completed=False,
        best_topology=None,
    )
    monkeypatch.setattr(workflow_service, "design_state_from_dict", lambda _: state)
    monkeypatch.setattr(workflow_service, "_add_guided_child", lambda *_: None)

    def fake_design(**kwargs: object) -> dict[str, object]:
        captured["workflow_format"] = kwargs["cello_artifact_format"]
        return {"status": "completed"}

    monkeypatch.setattr(workflow_service, "design_circuit_quick", fake_design)
    result = workflow_service.resume_design_run(
        "run_parent",
        run_store=FakeStore(),  # type: ignore[arg-type]
        cello_artifact_format=explicit_format,
    )

    assert result["status"] == "queued"
    assert captured["child_request"]["cello_artifact_format"] == expected_format  # type: ignore[index]
    assert captured["workflow_format"] == expected_format


def test_resume_service_fails_closed_for_invalid_parent_format(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "run_manifest.json"
    manifest_path.write_text(
        json.dumps({"request": {"cello_artifact_format": "invalid"}}),
        encoding="utf-8",
    )
    resolved, error = workflow_service._resolve_resume_cello_artifact_format(
        {"run_manifest_path": str(manifest_path)}, None
    )
    assert resolved is None
    assert error is not None
    assert error["error_type"] == "validation_error"


def test_web_resume_omits_format_for_parent_resolution() -> None:
    captured: dict[str, object] = {}

    class FakeRuns:
        def resume(self, run_id: str, **kwargs: object) -> dict[str, object]:
            captured["run_id"] = run_id
            captured.update(kwargs)
            return {"status": "queued", "run_id": "run_child"}

    services = SimpleNamespace(runs=FakeRuns())
    response = web_run_resume("run_parent", "", services)  # type: ignore[arg-type]
    assert response.status_code == 303
    assert captured["run_id"] == "run_parent"
    assert "cello_artifact_format" not in captured


def test_quick_workflow_blocks_cello21_before_provider_or_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        workflow_service,
        "CelloWrapper",
        lambda **_: (_ for _ in ()).throw(AssertionError("mapping must not start")),
    )
    monkeypatch.setattr(
        workflow_service,
        "run_reflexion_workflow",
        lambda **_: (_ for _ in ()).throw(AssertionError("provider must not start")),
    )
    result = workflow_service.design_circuit_quick(
        "Design an AND gate",
        model_name="fixture/model",
        api_key="fixture-key",
        enable_rag=False,
        enable_ode=False,
        enable_skill_extraction=False,
        output_dir=str(tmp_path),
        cello_artifact_format="cello21",
    )
    assert result["status"] == "error"
    assert result["error_type"] == "cello21_program_blocked"


def test_direct_service_rejects_unknown_artifact_format_before_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflow_service,
        "run_reflexion_workflow",
        lambda **_: (_ for _ in ()).throw(AssertionError("workflow must not start")),
    )
    result = workflow_service.design_circuit_quick(
        "Design an AND gate",
        model_name="fixture/model",
        cello_artifact_format="auto",
    )
    assert result["status"] == "error"
    assert result["error_type"] == "validation_error"


def test_evaluate_verilog_routes_cello21_mapping_through_program_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    class WrapperSpy:
        def __init__(self, **_: object):
            pass

        def run(self, state: object) -> object:
            calls.append("mapping")
            state.last_error = "fixture stop after mapping boundary"
            return state

    runner = Cello21ProgramRunner(
        _policy(
            active_experiment_id="C21-INTEGRATED-AND2-R3",
            execution_authorized=True,
            cello_mapping_authorized=True,
        ),
        tmp_path / "ledger.jsonl",
    )
    monkeypatch.setattr(workflow_service, "CelloWrapper", WrapperSpy)
    result = workflow_service.evaluate_verilog(
        "module g(input A, output Y); assign Y = A; endmodule",
        enable_ode=False,
        cello_artifact_format="cello21",
        cello21_program_runner=runner,
    )
    assert result["status"] == "error"
    assert calls == ["mapping"]
    events = [json.loads(line) for line in runner.ledger_path.read_text().splitlines()]
    assert [event["event"] for event in events] == [
        "mapping_reserved",
        "mapping_finished",
    ]


def test_cello_artifact_format_keeps_legacy_positional_parameter_order() -> None:
    for function in (
        workflow_service.design_circuit_quick,
        workflow_service.start_design_run,
        workflow_service.evaluate_verilog,
    ):
        names = list(inspect.signature(function).parameters)
        assert names.index("cello_artifact_format") > names.index("device_path")
