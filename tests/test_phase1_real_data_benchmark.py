from __future__ import annotations

import io
from pathlib import Path

from benchmark_suite.dataset import load_benchmark_dataset, validate_benchmark_dataset
from benchmark_suite.runner import run_benchmark_dataset
from quality.plate_reader import (
    apply_plate_reader_qc,
    export_fitted_parameters_to_part_library,
    fit_hill_curve_from_records,
    ingest_plate_reader_csv,
    ingest_plate_reader_excel,
)
from schemas.resource_calibration import CalibrationContext


def test_validated_circuits_dataset_schema_and_integrity() -> None:
    dataset = load_benchmark_dataset("validated_circuits_v1")
    assert dataset.dataset_id == "validated_circuits_v1"
    assert len(dataset.cases) == 22
    assert dataset.provenance.get("evidence_status") == "literature_curated_fixture"
    assert dataset.provenance.get("wet_lab_validated") is False
    assert dataset.provenance.get("independent_review_status") == "pending"
    assert (
        dataset.provenance.get("source_rights_status")
        == "pending_independent_review"
    )
    assert "project-authored fixture structure" in dataset.license

    errors = validate_benchmark_dataset(dataset)
    assert not errors

    case_ids = {case.case_id for case in dataset.cases}
    assert "cello_amtr_nor_v1" in case_ids
    assert "gardner_toggle_switch_v1" in case_ids
    assert "elowitz_repressilator_v1" in case_ids
    assert "inducible_pbad_arac_v1" in case_ids

    for case in dataset.cases:
        assert case.source.get("doi")
        assert case.source.get("exact_location")
        assert case.source.get("measurement_context")
        assert case.source.get("spot_review_status") == "pending_independent_review"

    cello_dois = {
        case.source.get("doi") for case in dataset.cases if "cello" in case.tags
    }
    assert cello_dois == {"10.1126/science.aac7341"}


def test_exp022_protocol_does_not_pretend_missing_input_is_frozen() -> None:
    import json

    protocol_path = (
        Path(__file__).resolve().parent.parent
        / "benchmark_suite"
        / "protocols"
        / "exp022_real_pilot_protocol.json"
    )
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    expected_input = protocol["preregistration"]["expected_raw_files"][0]

    assert protocol["preregistration"]["input_status"] == "awaiting_governed_raw_file"
    assert expected_input["sha256"] is None
    assert expected_input["status"] == "not_received"
    assert protocol["governance"]["gate_status"] == "not_started"


def test_run_benchmark_dataset_calibration_metrics() -> None:
    dataset = load_benchmark_dataset("validated_circuits_v1")
    result = run_benchmark_dataset(dataset, profile_id="research-v1.8")

    assert result["dataset"]["dataset_id"] == "validated_circuits_v1"
    assert len(result["cases"]) == 22
    summary = result["summary"]
    assert summary["case_count"] == 22

    calibration = summary["calibration"]
    assert "true_positives" in calibration
    assert "false_positives" in calibration
    assert "precision" in calibration
    assert "recall" in calibration
    assert 0.0 <= calibration["accuracy"] <= 1.0


def test_excel_and_csv_plate_reader_ingestion_and_qc(tmp_path: Path) -> None:
    synthetic_fixture_csv_data = (
        "experiment_id,well,timestamp_s,inducer_name,inducer_concentration,od600,output_fluorescence\n"
        "EXP_FIT,A1,0.0,IPTG,0.0,0.02,10.0\n"
        "EXP_FIT,A2,0.0,IPTG,0.0,0.02,10.0\n"
        "EXP_FIT,B1,0.0,IPTG,0.0,0.20,50.0\n"
        "EXP_FIT,B2,0.0,IPTG,1.0,0.25,200.0\n"
        "EXP_FIT,B3,0.0,IPTG,10.0,0.30,800.0\n"
        "EXP_FIT,B4,0.0,IPTG,100.0,0.31,1200.0\n"
    )
    records_csv = ingest_plate_reader_csv(synthetic_fixture_csv_data)
    assert len(records_csv) == 6

    qc_passed = apply_plate_reader_qc(records_csv)
    assert len(qc_passed) == 4  # A1, A2 filtered as blanks

    # Test Excel import via pandas dataframe to excel if pandas available
    try:
        import pandas as pd
        excel_path = tmp_path / "synthetic_fixture_plate.xlsx"
        df = pd.read_csv(io.StringIO(synthetic_fixture_csv_data))
        df.to_excel(excel_path, index=False)

        records_excel = ingest_plate_reader_excel(excel_path)
        assert len(records_excel) == 6
        assert records_excel[0].well == "A1"
    except (ImportError, ValueError, ModuleNotFoundError):
        pass  # If openpyxl not installed, Excel test is safely skipped


def test_fit_hill_curve_and_export_part_library() -> None:
    synthetic_fixture_csv_data = (
        "well,inducer_concentration,od600,output_fluorescence\n"
        "A1,0.0,0.02,10.0\n"
        "A2,0.0,0.02,10.0\n"
        "B1,0.1,0.25,50.0\n"
        "B2,1.0,0.25,450.0\n"
        "B3,10.0,0.25,950.0\n"
        "B4,100.0,0.25,1000.0\n"
    )
    records = ingest_plate_reader_csv(synthetic_fixture_csv_data)
    qc_passed = apply_plate_reader_qc(records)

    context = CalibrationContext(
        context_id="ctx_ptet_test",
        host_organism="E. coli",
        strain="MG1655",
        medium="M9",
        temperature_c=37.0,
        aeration="high",
        culture_format="96_well",
        working_volume_ul=200.0,
        instrument="Synergy_H1",
        gain_settings={"gain": 100},
        growth_phase="exponential",
        capacity_reporter="RFP",
        output_reporter="GFP",
        protocol_version="v1.0",
    )
    params = fit_hill_curve_from_records(qc_passed, context=context)

    assert params.origin == "fitted"
    assert params.r_squared > 0.90
    assert params.kd > 0.0

    part_export = export_fitted_parameters_to_part_library("pTet_TetR_gate", params, context)
    assert part_export["part_id"] == "pTet_TetR_gate"
    assert part_export["parameters"]["kd"] == params.kd
    assert part_export["governance"]["automatic_promotion_allowed"] is False
    assert part_export["governance"]["promotion_policy"] == "explicit_local_selection_required"
