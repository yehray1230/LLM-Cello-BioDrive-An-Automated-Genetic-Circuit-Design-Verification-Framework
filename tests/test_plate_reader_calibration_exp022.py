from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from quality.plate_reader import (
    QCConfig,
    apply_plate_reader_qc,
    evaluate_held_out_validation,
    fit_hill_curve_from_records,
    ingest_plate_reader_csv,
)
from schemas.resource_calibration import CalibrationContext


SYNTHETIC_TRAINING_FIXTURE_CSV = """experiment_id,protocol_version,plate_id,well,timestamp_s,biological_replicate,technical_replicate,strain,medium,temperature_c,shaking_condition,construct_id,backbone_id,origin,inducer_name,inducer_concentration,inducer_unit,od600,capacity_fluorescence,output_fluorescence,instrument,gain
EXP_001,v1.0,PLATE_1,A1,3600,bio_1,tech_1,MG1655,M9,37.0,220rpm,CONST_BLANK,pUC19,pUC,IPTG,0.0,uM,0.05,10.0,50.0,Synergy_H1,100
EXP_001,v1.0,PLATE_1,A2,3600,bio_1,tech_2,MG1655,M9,37.0,220rpm,CONST_BLANK,pUC19,pUC,IPTG,0.0,uM,0.05,10.0,50.0,Synergy_H1,100
EXP_001,v1.0,PLATE_1,B1,3600,bio_1,tech_1,MG1655,M9,37.0,220rpm,CONST_001,pUC19,pUC,IPTG,0.0,uM,0.85,100.0,150.0,Synergy_H1,100
EXP_001,v1.0,PLATE_1,B2,3600,bio_1,tech_1,MG1655,M9,37.0,220rpm,CONST_001,pUC19,pUC,IPTG,1.0,uM,0.90,100.0,300.0,Synergy_H1,100
EXP_001,v1.0,PLATE_1,B3,3600,bio_1,tech_1,MG1655,M9,37.0,220rpm,CONST_001,pUC19,pUC,IPTG,10.0,uM,0.92,100.0,850.0,Synergy_H1,100
EXP_001,v1.0,PLATE_1,B4,3600,bio_1,tech_1,MG1655,M9,37.0,220rpm,CONST_001,pUC19,pUC,IPTG,100.0,uM,0.95,100.0,1050.0,Synergy_H1,100
EXP_001,v1.0,PLATE_1,B5,3600,bio_1,tech_1,MG1655,M9,37.0,220rpm,CONST_001,pUC19,pUC,IPTG,1000.0,uM,0.94,100.0,1100.0,Synergy_H1,100
"""

SYNTHETIC_HOLDOUT_FIXTURE_CSV = """experiment_id,protocol_version,plate_id,well,timestamp_s,biological_replicate,technical_replicate,strain,medium,temperature_c,shaking_condition,construct_id,backbone_id,origin,inducer_name,inducer_concentration,inducer_unit,od600,capacity_fluorescence,output_fluorescence,instrument,gain
EXP_001,v1.0,PLATE_1,C1,3600,bio_2,tech_1,MG1655,M9,37.0,220rpm,CONST_001,pUC19,pUC,IPTG,5.0,uM,0.91,100.0,600.0,Synergy_H1,100
EXP_001,v1.0,PLATE_1,C2,3600,bio_2,tech_1,MG1655,M9,37.0,220rpm,CONST_001,pUC19,pUC,IPTG,50.0,uM,0.93,100.0,1000.0,Synergy_H1,100
"""


def test_synthetic_plate_reader_fixture_ingestion():
    records = ingest_plate_reader_csv(SYNTHETIC_TRAINING_FIXTURE_CSV)
    assert len(records) == 7
    assert records[0].well == "A1"
    assert records[2].inducer_concentration == 0.0
    assert records[6].inducer_concentration == 1000.0


def test_synthetic_plate_reader_fixture_qc_blank_subtraction():
    records = ingest_plate_reader_csv(SYNTHETIC_TRAINING_FIXTURE_CSV)
    qc_passed = apply_plate_reader_qc(records, QCConfig(blank_wells=["A1", "A2"]))
    # 7 records minus 2 blank wells = 5
    assert len(qc_passed) == 5
    # B1 raw output was 150.0, blank was 50.0 -> net 100.0
    assert qc_passed[0].output_fluorescence == 100.0
    # B1 raw OD was 0.85, blank was 0.05 -> net 0.80
    assert pytest.approx(qc_passed[0].od600, 1e-3) == 0.80


def test_synthetic_hill_fitting_and_holdout_contract():
    records = ingest_plate_reader_csv(SYNTHETIC_TRAINING_FIXTURE_CSV)
    qc_passed = apply_plate_reader_qc(records, QCConfig(blank_wells=["A1", "A2"]))

    ctx = CalibrationContext(
        context_id="CTX_M9_IPTG",
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

    fitted = fit_hill_curve_from_records(qc_passed, context=ctx)
    assert fitted.origin == "fitted"
    assert fitted.r_squared > 0.90
    assert fitted.y_min < fitted.y_max
    assert fitted.metadata.get("identifiable") is True

    holdout_records = ingest_plate_reader_csv(SYNTHETIC_HOLDOUT_FIXTURE_CSV)
    eval_metrics = evaluate_held_out_validation(fitted, holdout_records)
    assert "rmse" in eval_metrics
    assert eval_metrics["r_squared"] > 0.80


def test_agc3_negative_paths_insufficient_dynamic_range():
    flat_csv = """experiment_id,well,inducer_concentration,od600,output_fluorescence
EXP_FLAT,A1,0.0,0.5,100.0
EXP_FLAT,A2,1.0,0.5,100.0
EXP_FLAT,A3,10.0,0.5,100.0
"""
    records = ingest_plate_reader_csv(flat_csv)
    with pytest.raises(ValueError, match="Insufficient dynamic range"):
        fit_hill_curve_from_records(records)


def test_agc3_negative_paths_qc_low_od_filtering():
    low_od_csv = """experiment_id,well,inducer_concentration,od600,output_fluorescence
EXP_LOW,A1,0.0,0.01,50.0
EXP_LOW,A2,1.0,0.02,60.0
EXP_LOW,A3,10.0,0.03,70.0
"""
    records = ingest_plate_reader_csv(low_od_csv)
    qc_passed = apply_plate_reader_qc(records, QCConfig(min_od600=0.05, deduct_blank=False))
    assert len(qc_passed) == 0
