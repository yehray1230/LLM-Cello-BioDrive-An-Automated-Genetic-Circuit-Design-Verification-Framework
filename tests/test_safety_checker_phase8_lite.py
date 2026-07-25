from __future__ import annotations

import json

from utils import safety_checker
from utils.safety_checker import check_design_export_safety, check_safety


def test_benign_educational_request_remains_available():
    result = check_safety(
        "Explain at a high level what a DNA sequence represents.",
        "Escherichia coli",
    )
    assert result.status == "safe"
    assert result.automation_allowed is True
    assert result.export_allowed is True
    assert result.claim_strength == "research_only"


def test_chinese_high_risk_chassis_is_blocked():
    result = check_safety("在鼠疫耶爾森菌中設計基因電路", "鼠疫耶爾森菌")
    assert result.status == "blocked"
    assert result.is_safe is False
    assert result.automation_allowed is False
    assert result.export_allowed is False
    assert result.categories == ["high_risk_chassis"]


def test_chinese_sequence_automation_requires_review():
    result = check_safety(
        "依據這段 DNA 序列提供自動修復與匯出指令",
        "Escherichia coli",
    )
    assert result.status == "warn"
    assert result.requires_human_review is True
    assert result.automation_allowed is False
    assert result.export_allowed is False
    assert result.redirection_message is not None
    assert "sequence_automation" in result.categories


def test_export_gate_uses_persisted_design_context():
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


def test_audit_redacts_sequence_literals(tmp_path, monkeypatch):
    audit_dir = tmp_path / "audit"
    audit_file = audit_dir / "safety_audit.json"
    monkeypatch.setattr(safety_checker, "AUDIT_LOG_DIR", audit_dir)
    monkeypatch.setattr(safety_checker, "AUDIT_LOG_FILE", audit_file)
    sequence = "ATGC" * 20

    safety_checker.log_safety_event(
        "test_run",
        f"Review {sequence}",
        "warn",
        ["Review required"],
        action="export:genbank",
        categories=["sequence_automation"],
    )

    event = json.loads(audit_file.read_text(encoding="utf-8"))[-1]
    assert sequence not in event["intent_preview"]
    assert "sequence:redacted" in event["intent_preview"]
    assert len(event["intent_sha256"]) == 64
