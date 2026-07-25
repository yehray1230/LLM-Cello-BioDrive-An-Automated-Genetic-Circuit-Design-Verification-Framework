from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from exporters.assembly_deliverables import write_assembly_deliverables
from exporters.pdf_exporter import export_report_artifact, export_report_to_pdf
from mcp_server.explainer import (
    _wet_lab_qc_checklist,
    build_design_explanation,
    calculate_evidence_completeness,
)


def test_evidence_completeness_calculation():
    topology = {
        "verilog": "module circuit(input a, output y); assign y = ~a; endmodule",
        "gates": [{"name": "Gate1"}],
        "ode_simulation": {"dynamic_margin": 2.5},
        "cello_provenance": {"claim_level": "externally_mapped"}
    }
    result = calculate_evidence_completeness(topology)
    assert result["evidence_completeness_index"] > 50.0
    assert len(result["verified_items"]) >= 2
    assert len(result["citations"]) >= 1


def test_wet_lab_qc_checklist():
    topology = {}
    checklist = _wet_lab_qc_checklist(topology)
    assert len(checklist) == 4
    categories = [item["category"] for item in checklist]
    assert "Positive Control" in categories
    assert "Negative Control" in categories


def test_build_design_explanation_includes_evidence():
    result_data = {
        "status": "completed",
        "summary": {"best_topology": {"verilog": "module test(); endmodule", "gates": [{"name": "G1"}]}}
    }
    exp = build_design_explanation("run-123", result_data, profile="full", write_artifacts=False)
    explanation = exp["explanation"]
    assert "evidence_lineage" in explanation
    assert "wet_lab_qc_checklist" in explanation


def test_pdf_exporter_reports_renderer_unavailable_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import exporters.pdf_exporter as pdf_exporter

    html_content = (
        "<html><head><title>Test Report</title></head>"
        "<body><h1>Evidence Report</h1></body></html>"
    )
    output_pdf = tmp_path / "test_report.pdf"
    monkeypatch.setitem(__import__("sys").modules, "weasyprint", None)

    result = pdf_exporter.export_report_artifact(html_content, output_pdf)

    assert result.generation_status == "print_html_fallback"
    assert result.media_type == "text/html"
    assert result.renderer == "browser_print"
    assert result.fallback_reason == "renderer_unavailable"
    assert result.error_type == "ModuleNotFoundError"
    assert result.artifact_path == output_pdf.with_suffix(".print.html")
    assert result.artifact_path.exists()
    assert result.to_manifest_entry()["sha256"]


def test_pdf_exporter_reports_native_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_pdf = tmp_path / "test_report.pdf"

    class FakeHTML:
        def __init__(self, *, string: str) -> None:
            assert "Evidence Report" in string

        def write_pdf(self, output_path: str) -> None:
            Path(output_path).write_bytes(b"%PDF-1.7\nfixture")

    fake_weasyprint = SimpleNamespace(HTML=FakeHTML, __version__="test-version")
    monkeypatch.setitem(__import__("sys").modules, "weasyprint", fake_weasyprint)

    result = export_report_artifact("<h1>Evidence Report</h1>", output_pdf)

    assert result.generation_status == "native_pdf"
    assert result.generated_pdf is True
    assert result.media_type == "application/pdf"
    assert result.renderer == "weasyprint"
    assert result.renderer_version == "test-version"
    assert result.artifact_path == output_pdf
    assert result.to_manifest_entry()["filename"] == "test_report.pdf"
    assert export_report_to_pdf("<h1>Evidence Report</h1>", output_pdf) is True


def test_pdf_exporter_reports_renderer_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenHTML:
        def __init__(self, *, string: str) -> None:
            pass

        def write_pdf(self, output_path: str) -> None:
            raise ValueError("renderer crashed")

    monkeypatch.setitem(
        __import__("sys").modules,
        "weasyprint",
        SimpleNamespace(HTML=BrokenHTML, __version__="broken-version"),
    )

    result = export_report_artifact("<html></html>", tmp_path / "report.pdf")

    assert result.generation_status == "print_html_fallback"
    assert result.fallback_reason == "renderer_failed"
    assert result.error_type == "ValueError"
    assert result.artifact_path == tmp_path / "report.print.html"


def test_pdf_exporter_raises_when_fallback_cannot_be_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import exporters.pdf_exporter as pdf_exporter

    monkeypatch.setitem(__import__("sys").modules, "weasyprint", None)
    monkeypatch.setattr(
        Path,
        "write_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("write denied")),
    )

    result = pdf_exporter.export_report_artifact(
        "<html></html>", tmp_path / "report.pdf"
    )
    assert result.generation_status == "failed"
    assert result.artifact_path is None
    assert result.fallback_reason == "fallback_write_failed"
    assert result.error_type == "OSError"

    with pytest.raises(RuntimeError, match="could not write print-HTML fallback"):
        pdf_exporter.export_report_to_pdf("<html></html>", tmp_path / "report.pdf")


def test_assembly_deliverables_generates_pdf_artifact(tmp_path: Path):
    payload = {
        "deliverable_id": "deliv-123",
        "plan": {
            "design_id": "des-1",
            "plasmid_id": "p-1",
            "backbone_id": "pUC19",
            "backbone_version": "v1",
            "method": "Golden Gate",
            "status": "ready",
            "target_length": 1000,
            "target_checksum": "abc1234",
            "strategy": "Golden Gate",
            "vector": {"name": "pUC19"},
            "junctions": [],
            "assembly_steps": []
        },
        "readiness": {"readiness_status": "ready", "blockers": [], "warnings": []},
        "assembly": {
            "genbank": "LOCUS       Test_Plasmid             100 bp    DNA     circular SYN 21-JUL-2026\nFEATURES             Location/Qualifiers\nORIGIN\n        1 atcg\n//",
            "fragments": [],
            "primers": [],
        },
        "primers": {
            "status": "ready",
            "fragment_primer_sets": []
        }
    }
    artifacts = write_assembly_deliverables(tmp_path, payload)
    assert "pdf_report" in artifacts
    assert artifacts["pdf_report"]["filename"].startswith("assembly_report")
    assert artifacts["pdf_report"]["generation_status"] in {
        "native_pdf",
        "print_html_fallback",
    }
    assert artifacts["pdf_report"]["renderer"]
    assert len(artifacts["pdf_report"]["sha256"]) == 64
