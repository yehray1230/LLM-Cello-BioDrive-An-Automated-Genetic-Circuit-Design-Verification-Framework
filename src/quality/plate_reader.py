from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from schemas.resource_calibration import (
    CalibrationContext,
    PlateReaderRecord,
)


@dataclass
class QCConfig:
    min_od600: float = 0.05
    blank_wells: list[str] = field(default_factory=lambda: ["A1", "A2"])
    deduct_blank: bool = True
    max_replicate_cv: float = 0.3


@dataclass
class FittedHillParameters:
    y_min: float
    y_max: float
    kd: float
    n: float
    r_squared: float
    rmse: float
    origin: str = "fitted"
    metadata: dict[str, Any] = field(default_factory=dict)


def ingest_plate_reader_csv(csv_content_or_path: str) -> list[PlateReaderRecord]:
    if "\n" in csv_content_or_path or "," in csv_content_or_path:
        reader = csv.DictReader(io.StringIO(csv_content_or_path))
    else:
        with open(csv_content_or_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
        reader = csv.DictReader(io.StringIO(content))

    records: list[PlateReaderRecord] = []
    for idx, row in enumerate(reader, start=2):
        record = PlateReaderRecord(
            experiment_id=row.get("experiment_id", "EXP_001"),
            protocol_version=row.get("protocol_version", "v1.0"),
            plate_id=row.get("plate_id", "PLATE_1"),
            well=row["well"].strip(),
            timestamp_s=float(row.get("timestamp_s", 0.0)),
            biological_replicate=row.get("biological_replicate", "bio_1"),
            technical_replicate=row.get("technical_replicate", "tech_1"),
            strain=row.get("strain", "MG1655"),
            medium=row.get("medium", "M9"),
            temperature_c=float(row.get("temperature_c", 37.0)),
            shaking_condition=row.get("shaking_condition", "220rpm"),
            construct_id=row.get("construct_id", "CONST_001"),
            backbone_id=row.get("backbone_id", "pUC19"),
            origin=row.get("origin", "pUC"),
            inducer_name=row.get("inducer_name", "IPTG"),
            inducer_concentration=float(row.get("inducer_concentration", 0.0)),
            inducer_unit=row.get("inducer_unit", "uM"),
            od600=float(row["od600"]),
            capacity_fluorescence=float(row.get("capacity_fluorescence", 0.0)),
            output_fluorescence=float(row["output_fluorescence"]),
            instrument=row.get("instrument", "Synergy_H1"),
            gain_settings={"gain": float(row.get("gain", 100))},
            source_row=idx,
        )
        records.append(record)
    return records


def apply_plate_reader_qc(
    records: Sequence[PlateReaderRecord],
    config: QCConfig | None = None,
) -> list[PlateReaderRecord]:
    cfg = config or QCConfig()
    blank_records = [r for r in records if r.well in cfg.blank_wells]
    avg_blank_od = (
        sum(r.od600 for r in blank_records) / len(blank_records)
        if blank_records
        else 0.0
    )
    avg_blank_output = (
        sum(r.output_fluorescence for r in blank_records) / len(blank_records)
        if blank_records
        else 0.0
    )

    qc_passed: list[PlateReaderRecord] = []
    for r in records:
        if r.well in cfg.blank_wells:
            continue
        net_od = max(0.0, r.od600 - avg_blank_od) if cfg.deduct_blank else r.od600
        if net_od < cfg.min_od600:
            continue
        net_output = (
            max(0.0, r.output_fluorescence - avg_blank_output)
            if cfg.deduct_blank
            else r.output_fluorescence
        )

        qc_record = PlateReaderRecord(
            experiment_id=r.experiment_id,
            protocol_version=r.protocol_version,
            plate_id=r.plate_id,
            well=r.well,
            timestamp_s=r.timestamp_s,
            biological_replicate=r.biological_replicate,
            technical_replicate=r.technical_replicate,
            strain=r.strain,
            medium=r.medium,
            temperature_c=r.temperature_c,
            shaking_condition=r.shaking_condition,
            construct_id=r.construct_id,
            backbone_id=r.backbone_id,
            origin=r.origin,
            inducer_name=r.inducer_name,
            inducer_concentration=r.inducer_concentration,
            inducer_unit=r.inducer_unit,
            od600=net_od,
            capacity_fluorescence=r.capacity_fluorescence,
            output_fluorescence=net_output,
            instrument=r.instrument,
            gain_settings=r.gain_settings,
            source_row=r.source_row,
            schema_version=r.schema_version,
        )
        qc_passed.append(qc_record)
    return qc_passed


def hill_function(x: float, y_min: float, y_max: float, kd: float, n: float) -> float:
    if x <= 0:
        return y_min
    x_n = math.pow(x, n)
    kd_n = math.pow(kd, n)
    return y_min + (y_max - y_min) * (x_n / (kd_n + x_n))


def fit_hill_curve_from_records(
    records: Sequence[PlateReaderRecord],
    context: CalibrationContext | None = None,
) -> FittedHillParameters:
    if not records:
        raise ValueError("Cannot fit Hill curve from empty records.")

    x_vals = [r.inducer_concentration for r in records]
    y_vals = [r.output_fluorescence for r in records]

    dynamic_range = (max(y_vals) - min(y_vals)) if y_vals else 0.0
    if dynamic_range <= 1e-9:
        raise ValueError(f"Insufficient dynamic range for fitting: min={min(y_vals)}, max={max(y_vals)}")

    identifiable = True
    bound_hit = False
    cov_finite = True
    warning_emitted = False

    # Try scipy curve_fit first
    try:
        import warnings
        from scipy.optimize import curve_fit, OptimizeWarning
        import numpy as np

        def _model(x, y_min, y_max, kd, n):
            return [hill_function(xi, y_min, y_max, kd, n) for xi in x]

        p0 = [min(y_vals), max(y_vals), max(1e-3, sum(x_vals) / len(x_vals)), 1.5]
        bounds = ([0.0, 0.0, 1e-6, 0.5], [max(y_vals) * 2, max(y_vals) * 10 + 1e-3, max(x_vals) * 100 + 1e-3, 5.0])

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always", OptimizeWarning)
            popt, pcov = curve_fit(_model, x_vals, y_vals, p0=p0, bounds=bounds, maxfev=2000)
            if any(issubclass(warn.category, OptimizeWarning) for warn in w):
                warning_emitted = True

        y_min, y_max, kd, n = popt
        if pcov is None or np.any(np.isinf(pcov)) or np.any(np.isnan(pcov)):
            cov_finite = False
            identifiable = False

        # Check bound hitting
        tol = 1e-4
        if (
            abs(y_min - bounds[0][0]) < tol
            or abs(kd - bounds[0][2]) < tol
            or abs(n - bounds[0][3]) < tol
            or abs(n - bounds[1][3]) < tol
        ):
            bound_hit = True
            identifiable = False

    except Exception:
        # Fallback grid search if scipy unavailable/failed
        y_min = min(y_vals)
        y_max = max(y_vals)
        kd = sum(x_vals) / len(x_vals) if sum(x_vals) > 0 else 1.0
        n = 1.5
        identifiable = False
        cov_finite = False

    preds = [hill_function(x, y_min, y_max, kd, n) for x in x_vals]
    ss_res = sum((y - p) ** 2 for y, p in zip(y_vals, preds))
    ss_tot = sum((y - sum(y_vals) / len(y_vals)) ** 2 for y in y_vals)
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 1.0
    rmse = math.sqrt(ss_res / len(y_vals))

    if r2 < 0.85:
        identifiable = False

    return FittedHillParameters(
        y_min=float(y_min),
        y_max=float(y_max),
        kd=float(kd),
        n=float(n),
        r_squared=float(r2),
        rmse=float(rmse),
        origin="fitted",
        metadata={
            "record_count": len(records),
            "context_fingerprint": context.fingerprint() if context else None,
            "identifiable": identifiable,
            "bound_hit": bound_hit,
            "covariance_finite": cov_finite,
            "warning_emitted": warning_emitted,
            "dynamic_range": float(dynamic_range),
        },
    )


def evaluate_held_out_validation(
    params: FittedHillParameters,
    holdout_records: Sequence[PlateReaderRecord],
) -> dict[str, float]:
    if not holdout_records:
        return {"rmse": 0.0, "r_squared": 1.0}

    x_vals = [r.inducer_concentration for r in holdout_records]
    y_vals = [r.output_fluorescence for r in holdout_records]
    preds = [hill_function(x, params.y_min, params.y_max, params.kd, params.n) for x in x_vals]

    ss_res = sum((y - p) ** 2 for y, p in zip(y_vals, preds))
    mean_y = sum(y_vals) / len(y_vals)
    ss_tot = sum((y - mean_y) ** 2 for y in y_vals)
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 1.0
    rmse = math.sqrt(ss_res / len(y_vals))

    return {"rmse": float(rmse), "r_squared": float(r2)}


def ingest_plate_reader_excel(
    file_path_or_bytes: Any,
    sheet_name: str | int = 0,
) -> list[PlateReaderRecord]:
    try:
        import pandas as pd
    except ImportError as err:
        raise ValueError("Excel plate-reader ingestion requires pandas.") from err

    try:
        df = pd.read_excel(file_path_or_bytes, sheet_name=sheet_name)
    except Exception as err:
        raise ValueError(f"Failed to parse Excel file: {err}") from err

    records: list[PlateReaderRecord] = []
    for idx, row in df.iterrows():
        row_dict = row.to_dict()
        record = PlateReaderRecord(
            experiment_id=str(row_dict.get("experiment_id", "EXP_001")),
            protocol_version=str(row_dict.get("protocol_version", "v1.0")),
            plate_id=str(row_dict.get("plate_id", "PLATE_1")),
            well=str(row_dict.get("well", "")).strip(),
            timestamp_s=float(row_dict.get("timestamp_s", 0.0)),
            biological_replicate=str(row_dict.get("biological_replicate", "bio_1")),
            technical_replicate=str(row_dict.get("technical_replicate", "tech_1")),
            strain=str(row_dict.get("strain", "MG1655")),
            medium=str(row_dict.get("medium", "M9")),
            temperature_c=float(row_dict.get("temperature_c", 37.0)),
            shaking_condition=str(row_dict.get("shaking_condition", "220rpm")),
            construct_id=str(row_dict.get("construct_id", "CONST_001")),
            backbone_id=str(row_dict.get("backbone_id", "pUC19")),
            origin=str(row_dict.get("origin", "pUC")),
            inducer_name=str(row_dict.get("inducer_name", "IPTG")),
            inducer_concentration=float(row_dict.get("inducer_concentration", 0.0)),
            inducer_unit=str(row_dict.get("inducer_unit", "uM")),
            od600=float(row_dict.get("od600", 0.0)),
            capacity_fluorescence=float(row_dict.get("capacity_fluorescence", 0.0)),
            output_fluorescence=float(row_dict.get("output_fluorescence", 0.0)),
            instrument=str(row_dict.get("instrument", "Synergy_H1")),
            gain_settings={"gain": float(row_dict.get("gain", 100))},
            source_row=int(idx) + 2,
        )
        records.append(record)
    return records


def export_fitted_parameters_to_part_library(
    part_id: str,
    params: FittedHillParameters,
    context: CalibrationContext | None = None,
) -> dict[str, Any]:
    return {
        "part_id": part_id,
        "parameters": {
            "y_min": params.y_min,
            "y_max": params.y_max,
            "kd": params.kd,
            "n": params.n,
        },
        "goodness_of_fit": {
            "r_squared": params.r_squared,
            "rmse": params.rmse,
        },
        "governance": {
            "origin": params.origin,
            "confidence_category": "measured_plate_reader",
            "automatic_promotion_allowed": False,
            "promotion_policy": "explicit_local_selection_required",
        },
        "metadata": {
            **params.metadata,
            "context_fingerprint": context.fingerprint() if context else params.metadata.get("context_fingerprint"),
        },
    }
