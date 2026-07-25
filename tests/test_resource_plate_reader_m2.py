from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark_suite.resource_plate_reader import (
    PlateReaderPreprocessingConfig,
    load_plate_map,
    preprocess_plate_reader_csv,
)
from schemas.resource_calibration import (
    ResourceCalibrationDataset,
    calibration_context_from_dict,
    construct_metadata_from_dict,
    resource_parameter_definition_from_dict,
    validation_split_from_dict,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "resource_calibration"


def _json(name: str):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _inputs():
    context = calibration_context_from_dict(_json("context.json"))
    constructs = tuple(
        construct_metadata_from_dict(item) for item in _json("constructs.json")
    )
    plate_map = load_plate_map(FIXTURE_DIR / "plate_map_m2.json")
    return context, constructs, plate_map


def _report(source: str | Path | None = None, *, config=None):
    context, constructs, plate_map = _inputs()
    return preprocess_plate_reader_csv(
        source or FIXTURE_DIR / "plate_reader_m2_raw.csv",
        context=context,
        constructs=constructs,
        plate_map=plate_map,
        config=config,
    )


def test_m2_preprocessing_ingests_plate_map_and_applies_timepoint_blanks() -> None:
    report = _report()

    assert report.status == "completed"
    assert len(report.records) == 16
    assert len(report.blank_summary) == 4
    assert len(report.derived_metrics) == 16
    assert report.excluded_wells == ()
    first = next(record for record in report.records if record.well == "B1")
    assert first.od600 == pytest.approx(0.01)
    assert first.capacity_fluorescence == pytest.approx(50.0)
    assert first.output_fluorescence == pytest.approx(5.0)
    assert first.construct_id == "empty_vector_capacity"
    assert first.source_row == 10
    provenance = report.trace_provenance[first.trace_id]
    assert provenance["raw"]["od600"] == pytest.approx(0.05)
    assert provenance["blank"]["od600"] == pytest.approx(0.04)


def test_m2_preprocessing_is_reproducible_and_metrics_are_traceable() -> None:
    first = _report()
    second = _report()

    assert first.to_dict() == second.to_dict()
    trace_ids = {record.trace_id for record in first.records}
    assert all(
        set(metric.source_trace_ids) <= trace_ids for metric in first.derived_metrics
    )
    assert all(
        trace_id in first.trace_provenance
        for metric in first.derived_metrics
        for trace_id in metric.source_trace_ids
    )
    losses = {
        (metric.construct_id, metric.biological_replicate): metric.value
        for metric in first.derived_metrics
        if metric.metric_name == "capacity_loss_fraction"
    }
    assert losses[("empty_vector_capacity", "bio_1")] == pytest.approx(0.0)
    assert losses[("load_reference", "bio_1")] > 0.30
    load_loss = next(
        metric
        for metric in first.derived_metrics
        if metric.metric_name == "capacity_loss_fraction"
        and metric.construct_id == "load_reference"
    )
    assert load_loss.metadata["baseline_source_trace_ids"]


def test_m2_report_composes_with_m0_dataset_contract() -> None:
    report = _report()
    context, constructs, _ = _inputs()
    parameters = tuple(
        resource_parameter_definition_from_dict(item)
        for item in _json("parameters.json")
    )
    split = validation_split_from_dict(_json("validation_split.json"))

    dataset = ResourceCalibrationDataset(
        dataset_id="synthetic_resource_calibration_m2",
        context=context,
        constructs=constructs,
        records=report.records,
        parameters=parameters,
        derived_metrics=report.derived_metrics,
        validation_split=split,
    )

    assert len(dataset.trace_index) == 16
    assert len(dataset.derived_metrics) == 16
    json.dumps(dataset.to_dict())


def test_m2_replicate_qc_excludes_inconsistent_construct_with_reason_codes() -> None:
    text = (FIXTURE_DIR / "plate_reader_m2_raw.csv").read_text(encoding="utf-8")
    replacements = {
        "plate_001,C2,900,0.0598": "plate_001,C2,900,0.0565",
        "plate_001,C2,1800,0.07564": "plate_001,C2,1800,0.06475",
        "plate_001,C2,2700,0.104152": "plate_001,C2,2700,0.077125",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    report = _report(
        text,
        config=PlateReaderPreprocessingConfig(max_replicate_cv=0.10),
    )

    assert report.status == "completed_with_exclusions"
    exclusions = {(item.well, item.reason_code) for item in report.excluded_wells}
    assert ("C1", "replicate_growth_cv_exceeds_threshold") in exclusions
    assert ("C2", "replicate_growth_cv_exceeds_threshold") in exclusions
    assert {record.construct_id for record in report.records} == {
        "empty_vector_capacity"
    }


def test_m2_rejects_missing_blank_and_unknown_plate_map_wells() -> None:
    context, constructs, plate_map = _inputs()
    text = (FIXTURE_DIR / "plate_reader_m2_raw.csv").read_text(encoding="utf-8")
    without_final_blanks = "\n".join(
        line
        for line in text.splitlines()
        if not (",A1,2700," in line or ",A2,2700," in line)
    )
    with pytest.raises(ValueError, match="Missing blank measurement"):
        preprocess_plate_reader_csv(
            without_final_blanks,
            context=context,
            constructs=constructs,
            plate_map=plate_map,
        )

    unknown = text.replace("plate_001,C1,0", "plate_001,H12,0", 1)
    with pytest.raises(ValueError, match="absent from plate map"):
        preprocess_plate_reader_csv(
            unknown,
            context=context,
            constructs=constructs,
            plate_map=plate_map,
        )


def test_m2_reports_failed_qc_when_no_sample_has_an_exponential_window() -> None:
    report = _report(
        config=PlateReaderPreprocessingConfig(
            min_exponential_od=0.20,
            max_exponential_od=0.30,
        )
    )

    assert report.status == "failed_qc"
    assert report.records == ()
    assert report.derived_metrics == ()
    assert len(report.excluded_wells) == 4
    assert {exclusion.reason_code for exclusion in report.excluded_wells} == {
        "insufficient_exponential_window"
    }
