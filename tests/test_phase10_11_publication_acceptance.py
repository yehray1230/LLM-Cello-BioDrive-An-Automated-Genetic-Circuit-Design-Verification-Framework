from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from exporters.pdf_exporter import _inject_auto_print_script, export_report_to_pdf  # noqa: E402
from scripts.run_case_study_package import run_case_study_package  # noqa: E402


def test_phase10_clean_room_case_study_package(tmp_path: Path) -> None:
    target_dir = tmp_path / "clean_room_case_studies"
    manifest = run_case_study_package(target_dir)

    assert manifest["package_id"] == "publication_case_study_package_v1"
    assert manifest["version"] == "1.0.0"
    assert manifest["claim_boundary"] == "research_only"
    assert "computational screening heuristics" in manifest["disclaimer"]
    assert len(manifest["case_studies"]) == 3

    expected_files = [
        "cs1_inducible_reporter.gb",
        "cs1_inducible_reporter.ttl",
        "cs1_inducible_reporter.csv",
        "cs2_toggle_switch.gb",
        "cs2_toggle_switch.ttl",
        "cs2_toggle_switch.csv",
        "cs3_polycistronic_operon.gb",
        "cs3_polycistronic_operon.ttl",
        "cs3_polycistronic_operon.csv",
        "case_study_manifest.json",
    ]

    for fname in expected_files:
        filepath = target_dir / fname
        assert filepath.exists(), f"Missing file: {fname}"
        assert filepath.stat().st_size > 0, f"Empty file: {fname}"

    # Verify GenBank header structure
    gb_content = (target_dir / "cs1_inducible_reporter.gb").read_text(encoding="utf-8")
    assert "LOCUS" in gb_content
    assert "FEATURES" in gb_content
    assert "//" in gb_content

    # Verify SBOL3 Turtle content
    ttl_content = (target_dir / "cs1_inducible_reporter.ttl").read_text(encoding="utf-8")
    assert "@prefix" in ttl_content or "http://" in ttl_content

    # Verify BOM CSV header
    csv_content = (target_dir / "cs1_inducible_reporter.csv").read_text(encoding="utf-8")
    assert "part_id" in csv_content or "part_type" in csv_content


def test_phase11_pdf_exporter_fallback_truthfulness(tmp_path: Path) -> None:
    html_content = (
        "<!DOCTYPE html><html><head><title>Test Report</title></head>"
        "<body><h1>Genetic Circuit Analysis</h1><p>Test content.</p></body></html>"
    )
    pdf_path = tmp_path / "report.pdf"
    fallback_path = tmp_path / "report.print.html"

    result = export_report_to_pdf(html_content, pdf_path)

    # In environment without weasyprint binary/library, export_report_to_pdf returns False
    # and generates report.print.html
    if result is False:
        assert fallback_path.exists()
        fallback_content = fallback_path.read_text(encoding="utf-8")
        assert "<style media=\"print\">" in fallback_content
        assert "@page { size: A4;" in fallback_content
    else:
        assert pdf_path.exists()
        assert pdf_path.stat().st_size > 0


def test_phase11_auto_print_script_injection() -> None:
    raw_html = "<html><head><title>Title</title></head><body>Content</body></html>"
    injected = _inject_auto_print_script(raw_html)
    assert "<style media=\"print\">" in injected
    assert "</head>" in injected

    raw_no_head = "<div>Direct Body Content</div>"
    injected_no_head = _inject_auto_print_script(raw_no_head)
    assert "<style media=\"print\">" in injected_no_head
    assert "Direct Body Content" in injected_no_head
