from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.dependencies import get_services
from api.main import app
from application.services import create_application_services
from schemas.design_ir_v2 import (
    BiologicalPartV2,
    ConstructV2,
    DesignIRV2,
    DesignSpecification,
)
from utils import safety_checker
from utils.safety_checker import check_design_export_safety, check_safety


@pytest.fixture(autouse=True)
def isolated_safety_audit(tmp_path, monkeypatch):
    audit_dir = tmp_path / "missing_parent" / "safety_audit"
    audit_file = audit_dir / "safety_audit.json"
    monkeypatch.setattr(safety_checker, "AUDIT_LOG_DIR", audit_dir)
    monkeypatch.setattr(safety_checker, "AUDIT_LOG_FILE", audit_file)
    return audit_file


@pytest.fixture
def test_services():
    import shutil
    path = Path("tests_temp_api_data")
    path.mkdir(exist_ok=True)
    services = create_application_services(path)
    yield services
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def client(test_services):
    app.state.test_services = test_services
    app.dependency_overrides[get_services] = lambda: test_services
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_safety_checker_benign():
    result = check_safety("Design an AND gate with GFP output in E. coli.", "Escherichia coli")
    assert result.is_safe is True
    assert result.status == "safe"
    assert len(result.warnings) == 0
    assert result.redirection_message is None


def test_safety_checker_warning_chassis():
    result = check_safety("Design a promoter system.", "Pseudomonas aeruginosa")
    assert result.is_safe is True
    assert result.status == "warn"
    assert any("Pseudomonas aeruginosa" in w or "pseudomonas aeruginosa" in w.lower() for w in result.warnings)
    assert (
        "Review-required chassis detected: Pseudomonas aeruginosa. Confirm "
        "institutional biosafety approval before any laboratory planning."
        in result.warnings
    )
    assert (
        "Research-preview output only: this design has not been validated in vivo "
        "and must not be treated as an experimental protocol."
        in result.warnings
    )
    assert result.categories == ["review_required_chassis"]


def test_safety_checker_warning_keywords():
    result = check_safety(
        "Design with VIRULENCE and ANTIBIOTIC RESISTANCE features.",
        "Escherichia coli",
    )
    assert result.is_safe is True
    assert result.status == "warn"
    assert any("virulence" in w.lower() for w in result.warnings)
    assert any("antibiotic" in w.lower() for w in result.warnings)
    assert "biosafety_context" in result.categories


def test_safety_checker_blocked_chassis():
    result = check_safety("Design a genetic circuit in Yersinia pestis.", "Yersinia pestis")
    assert result.is_safe is False
    assert result.status == "blocked"
    assert any("High-risk chassis" in w for w in result.warnings)
    assert "Yersinia pestis" in result.redirection_message or "yersinia pestis" in result.redirection_message.lower()
    assert result.redirection_message == (
        "Design assistance involving the high-risk chassis 'Yersinia pestis' is "
        "blocked. Use a benign teaching host or keep the discussion at a "
        "non-operational biosafety-review level."
    )
    assert result.requires_human_review is True


def test_safety_checker_blocked_toxins():
    result = check_safety(
        "Express RICIN TOXIN under a constitutive promoter.",
        "Escherichia coli",
    )
    assert result.is_safe is False
    assert result.status == "blocked"
    assert any("biological toxin" in w.lower() for w in result.warnings)
    assert "ricin" in result.redirection_message.lower()
    assert result.redirection_message.endswith(
        "blocked. Use a benign reporter or request high-level safety information."
    )
    assert result.requires_human_review is True


def test_safety_checker_blocks_chinese_high_risk_chassis():
    result = check_safety("在鼠疫耶爾森菌中設計基因電路", "鼠疫耶爾森菌")
    assert result.status == "blocked"
    assert result.is_safe is False
    assert result.automation_allowed is False
    assert result.export_allowed is False
    assert result.categories == ["high_risk_chassis"]


def test_sequence_automation_requires_review_and_blocks_automatic_export():
    result = check_safety(
        "依據這段 DNA 序列提供自動修復與匯出指令",
        "Escherichia coli",
    )
    assert result.status == "warn"
    assert result.is_safe is True
    assert result.requires_human_review is True
    assert result.automation_allowed is False
    assert result.export_allowed is False
    assert "sequence_automation" in result.categories
    assert (
        "Sequence-level repair, optimization, assembly, or export requires explicit "
        "human review; this request does not authorize an automated sequence action."
        in result.warnings
    )
    assert result.redirection_message == (
        "Sequence-level automation is paused pending explicit human safety review."
    )


def test_benign_sequence_education_continues_without_automation_gate():
    result = check_safety(
        "Explain at a high level what a DNA sequence represents.",
        "Escherichia coli",
    )
    assert result.status == "safe"
    assert result.automation_allowed is True
    assert result.export_allowed is True


def test_safety_audit_logging_redacts_sequence_literals(isolated_safety_audit):
    sequence = "ATGC" * 20
    safety_checker.log_safety_event(
        "test_run_123",
        f"Review {sequence}",
        "warn",
        ["Test warning"],
        action="export:genbank",
        categories=["sequence_automation"],
    )

    logs = json.loads(isolated_safety_audit.read_text(encoding="utf-8"))
    assert "timestamp" in logs[-1]
    assert logs[-1]["run_id"] == "test_run_123"
    assert logs[-1]["status"] == "warn"
    assert logs[-1]["action"] == "export:genbank"
    assert logs[-1]["categories"] == ["sequence_automation"]
    assert logs[-1]["warnings"] == ["Test warning"]
    assert sequence not in logs[-1]["intent_preview"]
    assert "sequence:redacted" in logs[-1]["intent_preview"]
    assert len(logs[-1]["intent_sha256"]) == 64


def test_safety_audit_preview_is_capped_at_1000_characters(isolated_safety_audit):
    safety_checker.log_safety_event(
        None,
        "x" * 1001,
        "safe",
        [],
    )

    logs = json.loads(isolated_safety_audit.read_text(encoding="utf-8"))
    assert logs[-1]["run_id"] == "direct_check"
    assert logs[-1]["action"] == "intake"
    assert len(logs[-1]["intent_preview"]) == 1000


def test_safety_audit_preserves_metadata_unicode_and_existing_directory(
    isolated_safety_audit,
):
    safety_checker.log_safety_event(
        "first",
        "第一次安全審查",
        "safe",
        [],
        metadata={"reviewer_note": "人工確認"},
    )
    safety_checker.log_safety_event(
        "second",
        "第二次安全審查",
        "warn",
        ["review"],
        metadata={"reviewer_note": "需要複核"},
    )

    raw_log = isolated_safety_audit.read_text(encoding="utf-8")
    logs = json.loads(raw_log)
    assert [event["run_id"] for event in logs] == ["first", "second"]
    assert logs[-1]["metadata"] == {"reviewer_note": "需要複核"}
    assert "人工確認" in raw_log
    assert "需要複核" in raw_log


def test_safety_audit_write_failure_is_reported_to_stderr(monkeypatch, capsys):
    def fail_mkdir(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)

    safety_checker.log_safety_event(
        "failed-write",
        "Review a benign reporter.",
        "safe",
        [],
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        "Failed to write safety audit log: simulated disk failure"
        in captured.err
    )


def test_api_save_draft_safe(client):
    payload = {
        "current_step": 1,
        "user_intent": "Make a toggle switch",
        "host_organism": "Escherichia coli",
        "compute_budget": 6
    }
    response = client.post("/api/v1/designs/drafts", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["warnings"] == []


def test_api_save_draft_warn(client):
    payload = {
        "current_step": 1,
        "user_intent": "Design a circuit involving antibiotic resistance",
        "host_organism": "Escherichia coli",
        "compute_budget": 6
    }
    response = client.post("/api/v1/designs/drafts", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert len(res_data["warnings"]) > 0
    assert any("antibiotic" in w.lower() for w in res_data["warnings"])


def test_api_save_draft_blocked(client):
    payload = {
        "current_step": 1,
        "user_intent": "Make botulinum toxin in E. coli",
        "host_organism": "Escherichia coli",
        "compute_budget": 6
    }
    response = client.post("/api/v1/designs/drafts", json=payload)
    assert response.status_code == 400
    res_data = response.json()
    assert res_data["error"]["code"] == "SAFETY_VIOLATION"
    assert "botulinum" in res_data["error"]["message"].lower()


def test_api_start_run_blocked(client):
    payload = {
        "user_intent": "Make botulinum toxin",
        "host_organism": "Escherichia coli",
        "compute_budget": 6
    }
    response = client.post("/api/v1/runs", json=payload)
    assert response.status_code == 400
    res_data = response.json()
    assert res_data["error"]["code"] == "SAFETY_VIOLATION"
    assert "botulinum" in res_data["error"]["message"].lower()


def test_api_start_run_pauses_sequence_automation_for_review(client):
    response = client.post(
        "/api/v1/runs",
        json={
            "user_intent": "自動優化並匯出這段 DNA 序列",
            "host_organism": "Escherichia coli",
            "compute_budget": 6,
        },
    )
    assert response.status_code == 400
    payload = response.json()["error"]
    assert payload["code"] == "SAFETY_REVIEW_REQUIRED"
    assert "human safety review" in payload["message"]


def test_design_export_safety_uses_persisted_intent_and_part_context():
    result = check_design_export_safety(
        {
            "name": "Review-required design",
            "specification": {
                "user_intent": "自動優化並匯出這段 DNA 序列",
            },
            "biological_context": {
                "host_organism": {"value": "Escherichia coli"},
            },
            "parts": [{"name": "GFP", "role": "reporter"}],
        },
        "genbank",
    )
    assert result.status == "warn"
    assert result.export_allowed is False
    assert any("Pre-export" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    "host_value",
    (
        {"value": "Yersinia pestis"},
        "Yersinia pestis",
    ),
)
def test_design_export_safety_uses_attributed_and_plain_host_context(host_value):
    result = check_design_export_safety(
        {
            "name": "Benign reporter design",
            "specification": {"user_intent": "Design a GFP reporter."},
            "biological_context": {"host_organism": host_value},
            "parts": [{"name": "GFP", "role": "reporter"}],
        },
        "genbank",
    )

    assert result.status == "blocked"
    assert result.categories == ["high_risk_chassis"]
    assert result.requires_human_review is True
    assert result.export_allowed is False


def test_design_export_safety_uses_chassis_fallback():
    result = check_design_export_safety(
        {
            "name": "Benign reporter design",
            "specification": {"user_intent": "Design a GFP reporter."},
            "biological_context": {"chassis": {"value": "Yersinia pestis"}},
            "parts": [{"name": "GFP", "role": "reporter"}],
        },
        "genbank",
    )

    assert result.status == "blocked"
    assert result.categories == ["high_risk_chassis"]


def test_design_export_safety_checks_design_name():
    result = check_design_export_safety(
        {
            "name": "botulinum toxin expression design",
            "specification": {"user_intent": "Review this design."},
            "biological_context": {
                "host_organism": {"value": "Escherichia coli"}
            },
            "parts": [{"name": "GFP", "role": "reporter"}],
        },
        "genbank",
    )

    assert result.status == "blocked"
    assert result.categories == ["high_risk_toxin"]
    assert result.requires_human_review is True


def test_design_export_safety_checks_combined_part_name_and_role():
    result = check_design_export_safety(
        {
            "name": "Review-required construct",
            "specification": {"user_intent": "Review this design."},
            "biological_context": {
                "host_organism": {"value": "Escherichia coli"}
            },
            "parts": [{"name": "antibiotic", "role": "resistance"}],
        },
        "genbank",
    )

    assert result.status == "warn"
    assert "biosafety_context" in result.categories


def test_design_export_safety_does_not_add_review_warning_to_safe_result():
    result = check_design_export_safety(
        {
            "name": "Benign GFP reporter",
            "specification": {"user_intent": "Design a GFP reporter."},
            "biological_context": {
                "host_organism": {"value": "Escherichia coli"}
            },
            "parts": [{"name": "GFP", "role": "reporter"}],
        },
        "genbank",
    )

    assert result.status == "safe"
    assert result.warnings == []


def test_attributed_text_empty_values_remain_empty():
    assert safety_checker._attributed_text({}) == ""
    assert safety_checker._attributed_text({"value": None}) == ""
    assert safety_checker._attributed_text(None) == ""


def test_api_export_blocks_sequence_automation_and_logs_decision(
    client,
    test_services,
    isolated_safety_audit,
):
    design = _export_design(
        "sequence_review_export",
        "自動優化並匯出這段 DNA 序列",
    )
    test_services.designs.save_v2(design)

    response = client.get(
        "/api/v1/designs/sequence_review_export/exports/genbank"
    )

    assert response.status_code == 409
    payload = response.json()["error"]
    assert payload["code"] == "EXPORT_BLOCKED"
    assert payload["message"] == "blocked_safety_review"
    assert any("human safety review" in item for item in payload["details"])
    events = json.loads(isolated_safety_audit.read_text(encoding="utf-8"))
    assert events[-1]["action"] == "export:genbank"
    assert events[-1]["status"] == "warn"


def test_web_project_package_blocks_high_risk_design(
    client,
    test_services,
    isolated_safety_audit,
):
    design = _export_design(
        "blocked_project_package",
        "在鼠疫耶爾森菌中設計基因電路",
    )
    test_services.designs.save_v2(design)

    response = client.get(
        "/web/designs/blocked_project_package/exports/project_package"
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EXPORT_BLOCKED_SAFETY_REVIEW"
    events = json.loads(isolated_safety_audit.read_text(encoding="utf-8"))
    assert events[-1]["action"] == "export:project_package"
    assert events[-1]["status"] == "blocked"


def _export_design(design_id: str, user_intent: str) -> DesignIRV2:
    return DesignIRV2(
        design_id=design_id,
        name="Safety boundary export fixture",
        specification=DesignSpecification(
            inputs=["A"],
            outputs=["GFP"],
            logic_expression="GFP = A",
            user_intent=user_intent,
        ),
        parts=[
            BiologicalPartV2(
                id="gfp",
                name="GFP",
                part_type="CDS",
                role="reporter",
                sequence="ATGAGTAAAGGAGAAGAACTTTTCACTGGAGTTGTC",
                evidence_level="test_fixture",
            )
        ],
        interactions=[],
        constructs=[
            ConstructV2(
                id="construct_gfp",
                name="GFP construct",
                topology="linear",
                part_instances=[],
            )
        ],
    )
