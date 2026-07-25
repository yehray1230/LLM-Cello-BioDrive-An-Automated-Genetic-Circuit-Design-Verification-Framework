from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from schemas.resource_calibration import (
    PARAMETER_ROLE_GOVERNANCE_ORIGINS,
    CalibrationContext,
    ResourceCalibrationDataset,
    ResourceParameterDefinition,
    ValidationSplit,
    calibration_context_from_dict,
    construct_metadata_from_dict,
    derived_metric_from_dict,
    plate_reader_record_from_dict,
    resource_parameter_definition_from_dict,
    validation_split_from_dict,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "resource_calibration"


def _read_json(name: str):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _synthetic_dataset() -> ResourceCalibrationDataset:
    context = calibration_context_from_dict(_read_json("context.json"))
    constructs = tuple(
        construct_metadata_from_dict(item) for item in _read_json("constructs.json")
    )
    parameters = tuple(
        resource_parameter_definition_from_dict(item)
        for item in _read_json("parameters.json")
    )
    derived_metrics = tuple(
        derived_metric_from_dict(item) for item in _read_json("derived_metrics.json")
    )
    validation_split = validation_split_from_dict(_read_json("validation_split.json"))
    with (FIXTURE_DIR / "plate_reader_long.csv").open(
        encoding="utf-8",
        newline="",
    ) as handle:
        records = tuple(
            plate_reader_record_from_dict(row, source_row=index)
            for index, row in enumerate(csv.DictReader(handle), start=2)
        )
    return ResourceCalibrationDataset(
        dataset_id="synthetic_resource_calibration_m0",
        context=context,
        constructs=constructs,
        records=records,
        parameters=parameters,
        derived_metrics=derived_metrics,
        validation_split=validation_split,
    )


def test_synthetic_resource_calibration_fixture_validates_and_traces_rows() -> None:
    dataset = _synthetic_dataset()

    assert len(dataset.records) == 12
    assert len(dataset.trace_index) == 12
    assert dataset.records[0].source_row == 2
    assert dataset.records[0].trace_id == "exp_m0_synthetic:plate_001:A1:0"
    assert dataset.trace_index["exp_m0_synthetic:plate_001:C2:600"].construct_id == (
        "load_heldout_rbs"
    )
    assert dataset.validation_split.validation_construct_ids == ("load_heldout_rbs",)
    assert dataset.derived_metrics[0].source_trace_ids == (
        "exp_m0_synthetic:plate_001:A1:0",
        "exp_m0_synthetic:plate_001:A1:600",
    )
    assert all(
        trace_id in dataset.trace_index
        for metric in dataset.derived_metrics
        for trace_id in metric.source_trace_ids
    )
    json.dumps(dataset.to_dict())


def test_calibration_context_round_trip_has_stable_fingerprint() -> None:
    context = calibration_context_from_dict(_read_json("context.json"))
    round_trip = calibration_context_from_dict(context.to_dict())

    assert isinstance(round_trip, CalibrationContext)
    assert round_trip == context
    assert round_trip.fingerprint() == context.fingerprint()


def test_dataset_rejects_context_mismatch_without_silent_coercion() -> None:
    dataset = _synthetic_dataset()
    mismatched = replace(dataset.records[0], medium="LB")

    with pytest.raises(ValueError, match="Medium mismatch"):
        ResourceCalibrationDataset(
            dataset_id=dataset.dataset_id,
            context=dataset.context,
            constructs=dataset.constructs,
            records=(mismatched, *dataset.records[1:]),
            parameters=dataset.parameters,
            derived_metrics=dataset.derived_metrics,
            validation_split=dataset.validation_split,
        )


def test_parameter_taxonomy_maps_to_existing_governance_origins() -> None:
    dataset = _synthetic_dataset()
    origins = {
        parameter.role: parameter.governance_origin for parameter in dataset.parameters
    }

    assert origins == PARAMETER_ROLE_GOVERNANCE_ORIGINS
    with pytest.raises(ValueError, match="Fixed assumptions cannot"):
        ResourceParameterDefinition(
            parameter_name="maturation_state_count",
            role="fixed_assumption",
            unit="count",
            source="test",
            measurement_context_id=dataset.context.context_id,
            value=2.0,
            is_fittable=True,
        )


def test_validation_split_is_frozen_and_disjoint() -> None:
    with pytest.raises(ValueError, match="must be disjoint"):
        ValidationSplit(
            split_id="invalid_overlap",
            strategy="construct_holdout",
            training_construct_ids=("construct_a",),
            validation_construct_ids=("construct_a",),
            grouping_key="construct_id",
            rationale="Intentional invalid test fixture.",
        )

    with pytest.raises(ValueError, match="must be frozen"):
        ValidationSplit(
            split_id="invalid_mutable",
            strategy="construct_holdout",
            training_construct_ids=("construct_a",),
            validation_construct_ids=("construct_b",),
            grouping_key="construct_id",
            rationale="Intentional invalid test fixture.",
            frozen=False,
        )
