from __future__ import annotations

import json
from pathlib import Path

from scripts.run_case_study_package import run_case_study_package


def test_phase10_case_study_package_runner(tmp_path: Path) -> None:
    output_dir = tmp_path / "case_studies_test_output"
    manifest = run_case_study_package(output_dir)

    assert manifest["package_id"] == "publication_case_study_package_v1"
    assert manifest["claim_boundary"] == "research_only"
    assert manifest["genbank_record_date"] == "2026-07-21"
    assert len(manifest["case_studies"]) == 3

    case_ids = [cs["case_id"] for cs in manifest["case_studies"]]
    assert "cs1_inducible_reporter" in case_ids
    assert "cs2_toggle_switch" in case_ids
    assert "cs3_polycistronic_operon" in case_ids

    # Verify generated artifact files exist
    assert (output_dir / "cs1_inducible_reporter.gb").exists()
    assert (output_dir / "cs1_inducible_reporter.ttl").exists()
    assert (output_dir / "cs1_inducible_reporter.csv").exists()

    assert (output_dir / "cs2_toggle_switch.gb").exists()
    assert (output_dir / "cs2_toggle_switch.ttl").exists()
    assert (output_dir / "cs2_toggle_switch.csv").exists()

    assert (output_dir / "cs3_polycistronic_operon.gb").exists()
    assert (output_dir / "cs3_polycistronic_operon.ttl").exists()
    assert (output_dir / "cs3_polycistronic_operon.csv").exists()

    assert (output_dir / "case_study_manifest.json").exists()

    # Read manifest file content
    saved_manifest = json.loads((output_dir / "case_study_manifest.json").read_text(encoding="utf-8"))
    assert saved_manifest["version"] == "1.0.0"


def test_phase10_case_study_package_is_byte_reproducible(tmp_path: Path) -> None:
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"

    run_case_study_package(first_output)
    run_case_study_package(second_output)

    first_files = sorted(path.name for path in first_output.iterdir())
    second_files = sorted(path.name for path in second_output.iterdir())
    assert first_files == second_files

    for filename in first_files:
        assert (first_output / filename).read_bytes() == (
            second_output / filename
        ).read_bytes()
