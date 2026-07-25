from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any

from schemas.resource_calibration import (
    CalibrationContext,
    ConstructMetadata,
    DerivedMetric,
    PlateReaderRecord,
)


RESOURCE_PREPROCESSING_VERSION = "0.1.0"


@dataclass(frozen=True)
class PlateMapEntry:
    well: str
    role: str
    construct_id: str = ""
    biological_replicate: str = ""
    technical_replicate: str = ""
    inducer_name: str = ""
    inducer_concentration: float = 0.0
    inducer_unit: str = ""

    def __post_init__(self) -> None:
        if not self.well.strip():
            raise ValueError("Plate-map well must be non-empty.")
        if self.role not in {"blank", "sample"}:
            raise ValueError("Plate-map role must be 'blank' or 'sample'.")
        if self.role == "sample":
            for name in (
                "construct_id",
                "biological_replicate",
                "technical_replicate",
            ):
                if not str(getattr(self, name)).strip():
                    raise ValueError(f"Sample plate-map entry requires {name}.")
        if not math.isfinite(float(self.inducer_concentration)):
            raise ValueError("inducer_concentration must be finite.")
        if self.inducer_concentration < 0.0:
            raise ValueError("inducer_concentration must be non-negative.")
        if self.inducer_concentration > 0.0 and not (
            self.inducer_name.strip() and self.inducer_unit.strip()
        ):
            raise ValueError(
                "Positive inducer concentration requires inducer_name and inducer_unit."
            )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PlateMapEntry:
        try:
            concentration = float(payload.get("inducer_concentration", 0.0))
        except (TypeError, ValueError) as exc:
            raise ValueError("inducer_concentration must be numeric.") from exc
        return cls(
            well=str(payload.get("well") or "").upper(),
            role=str(payload.get("role") or ""),
            construct_id=str(payload.get("construct_id") or ""),
            biological_replicate=str(payload.get("biological_replicate") or ""),
            technical_replicate=str(payload.get("technical_replicate") or ""),
            inducer_name=str(payload.get("inducer_name") or ""),
            inducer_concentration=concentration,
            inducer_unit=str(payload.get("inducer_unit") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlateReaderPreprocessingConfig:
    min_exponential_od: float = 0.01
    max_exponential_od: float = 0.30
    min_window_points: int = 3
    min_growth_r_squared: float = 0.95
    min_biological_replicates: int = 2
    replicate_outlier_relative_deviation: float = 0.35
    max_replicate_cv: float = 0.25
    preprocessing_version: str = RESOURCE_PREPROCESSING_VERSION

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_exponential_od < self.max_exponential_od:
            raise ValueError("Exponential OD bounds must be ordered and non-negative.")
        if self.min_window_points < 3:
            raise ValueError("min_window_points must be at least 3.")
        for name in (
            "min_growth_r_squared",
            "replicate_outlier_relative_deviation",
            "max_replicate_cv",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative.")
        if self.min_growth_r_squared > 1.0:
            raise ValueError("min_growth_r_squared cannot exceed 1.0.")
        if self.min_biological_replicates < 1:
            raise ValueError("min_biological_replicates must be positive.")


@dataclass(frozen=True)
class WellExclusion:
    experiment_id: str
    plate_id: str
    well: str
    reason_code: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class PlateReaderPreprocessingReport:
    status: str
    records: tuple[PlateReaderRecord, ...]
    derived_metrics: tuple[DerivedMetric, ...]
    excluded_wells: tuple[WellExclusion, ...]
    blank_summary: tuple[dict[str, Any], ...]
    trace_provenance: dict[str, dict[str, Any]]
    plate_map_fingerprint: str
    config: PlateReaderPreprocessingConfig
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "records": [record.to_dict() for record in self.records],
            "derived_metrics": [metric.to_dict() for metric in self.derived_metrics],
            "excluded_wells": [item.to_dict() for item in self.excluded_wells],
            "blank_summary": list(self.blank_summary),
            "trace_provenance": dict(self.trace_provenance),
            "plate_map_fingerprint": self.plate_map_fingerprint,
            "config": asdict(self.config),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class _RawReading:
    experiment_id: str
    plate_id: str
    well: str
    timestamp_s: float
    od600: float
    capacity_fluorescence: float
    output_fluorescence: float
    source_row: int


@dataclass(frozen=True)
class _WellFit:
    experiment_id: str
    plate_id: str
    well: str
    entry: PlateMapEntry
    records: tuple[PlateReaderRecord, ...]
    window_records: tuple[PlateReaderRecord, ...]
    growth_rate_per_h: float
    r_squared: float


def load_plate_map(
    source: str | Path | list[dict[str, Any]],
) -> tuple[PlateMapEntry, ...]:
    if isinstance(source, list):
        payload = source
    else:
        text = _source_text(source)
        payload = json.loads(text)
    if not isinstance(payload, list) or not payload:
        raise ValueError("Plate map must be a non-empty JSON list.")
    entries = tuple(PlateMapEntry.from_dict(item) for item in payload)
    wells = [entry.well for entry in entries]
    duplicates = sorted({well for well in wells if wells.count(well) > 1})
    if duplicates:
        raise ValueError(f"Duplicate plate-map wells: {duplicates}.")
    if not any(entry.role == "blank" for entry in entries):
        raise ValueError("Plate map requires at least one blank well.")
    return entries


def preprocess_plate_reader_csv(
    source: str | Path,
    *,
    context: CalibrationContext,
    constructs: tuple[ConstructMetadata, ...] | list[ConstructMetadata],
    plate_map: tuple[PlateMapEntry, ...] | list[PlateMapEntry],
    config: PlateReaderPreprocessingConfig | None = None,
) -> PlateReaderPreprocessingReport:
    resolved_config = config or PlateReaderPreprocessingConfig()
    entries = tuple(plate_map)
    if not entries:
        raise ValueError("plate_map must contain at least one entry.")
    entry_by_well = {entry.well.upper(): entry for entry in entries}
    if len(entry_by_well) != len(entries):
        raise ValueError("plate_map contains duplicate wells.")
    construct_by_id = {item.construct_id: item for item in constructs}
    for entry in entries:
        if entry.role == "sample" and entry.construct_id not in construct_by_id:
            raise ValueError(
                f"Plate map references unknown construct {entry.construct_id!r}."
            )

    raw_readings = _load_raw_readings(source)
    unknown_wells = sorted(
        {reading.well for reading in raw_readings if reading.well not in entry_by_well}
    )
    if unknown_wells:
        raise ValueError(f"CSV contains wells absent from plate map: {unknown_wells}.")

    blank_by_time, blank_summary = _blank_correction(raw_readings, entry_by_well)
    records_by_well: dict[tuple[str, str], list[PlateReaderRecord]] = {}
    provenance: dict[str, dict[str, Any]] = {}
    clamped_values = 0
    for reading in raw_readings:
        entry = entry_by_well[reading.well]
        if entry.role == "blank":
            continue
        blank = blank_by_time[
            (reading.experiment_id, reading.plate_id, reading.timestamp_s)
        ]
        corrected = {
            "od600": max(0.0, reading.od600 - blank["od600"]),
            "capacity_fluorescence": max(
                0.0,
                reading.capacity_fluorescence - blank["capacity_fluorescence"],
            ),
            "output_fluorescence": max(
                0.0,
                reading.output_fluorescence - blank["output_fluorescence"],
            ),
        }
        clamped_values += sum(
            (
                reading.od600 - blank["od600"] < 0.0,
                reading.capacity_fluorescence - blank["capacity_fluorescence"] < 0.0,
                reading.output_fluorescence - blank["output_fluorescence"] < 0.0,
            )
        )
        record = PlateReaderRecord(
            experiment_id=reading.experiment_id,
            protocol_version=context.protocol_version,
            plate_id=reading.plate_id,
            well=reading.well,
            timestamp_s=reading.timestamp_s,
            biological_replicate=entry.biological_replicate,
            technical_replicate=entry.technical_replicate,
            strain=context.strain,
            medium=context.medium,
            temperature_c=context.temperature_c,
            shaking_condition=_shaking_condition(context),
            construct_id=entry.construct_id,
            backbone_id=construct_by_id[entry.construct_id].backbone_id,
            origin=construct_by_id[entry.construct_id].origin,
            inducer_name=entry.inducer_name,
            inducer_concentration=entry.inducer_concentration,
            inducer_unit=entry.inducer_unit,
            od600=corrected["od600"],
            capacity_fluorescence=corrected["capacity_fluorescence"],
            output_fluorescence=corrected["output_fluorescence"],
            instrument=context.instrument,
            gain_settings=dict(context.gain_settings),
            source_row=reading.source_row,
        )
        records_by_well.setdefault(
            (reading.experiment_id, reading.plate_id, reading.well), []
        ).append(record)
        provenance[record.trace_id] = {
            "source_row": reading.source_row,
            "raw": {
                "od600": reading.od600,
                "capacity_fluorescence": reading.capacity_fluorescence,
                "output_fluorescence": reading.output_fluorescence,
            },
            "blank": dict(blank),
            "blank_corrected": corrected,
        }

    exclusions: list[WellExclusion] = []
    preliminary: list[_WellFit] = []
    for (experiment_id, plate_id, well), records in sorted(records_by_well.items()):
        records.sort(key=lambda item: item.timestamp_s)
        duplicate_times = _duplicates([item.timestamp_s for item in records])
        if duplicate_times:
            exclusions.append(
                WellExclusion(
                    experiment_id,
                    plate_id,
                    well,
                    "duplicate_timepoint",
                    f"Duplicate timestamps: {duplicate_times}.",
                )
            )
            continue
        window = tuple(
            item
            for item in records
            if resolved_config.min_exponential_od
            <= item.od600
            <= resolved_config.max_exponential_od
        )
        if len(window) < resolved_config.min_window_points:
            exclusions.append(
                WellExclusion(
                    experiment_id,
                    plate_id,
                    well,
                    "insufficient_exponential_window",
                    f"Found {len(window)} eligible points; "
                    f"required {resolved_config.min_window_points}.",
                )
            )
            continue
        x = [item.timestamp_s / 3600.0 for item in window]
        y = [math.log(item.od600) for item in window]
        slope, _, r_squared = _linear_fit(x, y)
        if slope <= 0.0:
            exclusions.append(
                WellExclusion(
                    experiment_id,
                    plate_id,
                    well,
                    "non_positive_growth",
                    f"Estimated growth rate was {slope:.6g} 1/h.",
                )
            )
            continue
        if r_squared < resolved_config.min_growth_r_squared:
            exclusions.append(
                WellExclusion(
                    experiment_id,
                    plate_id,
                    well,
                    "poor_exponential_fit",
                    f"Growth R^2 {r_squared:.4f} was below "
                    f"{resolved_config.min_growth_r_squared:.4f}.",
                )
            )
            continue
        preliminary.append(
            _WellFit(
                experiment_id=experiment_id,
                plate_id=plate_id,
                well=well,
                entry=entry_by_well[well],
                records=tuple(records),
                window_records=window,
                growth_rate_per_h=slope,
                r_squared=r_squared,
            )
        )

    valid_fits = _replicate_qc(preliminary, resolved_config, exclusions)
    fingerprint = _plate_map_fingerprint(entries)
    metrics = _derived_metrics(
        valid_fits,
        constructs=tuple(constructs),
        config=resolved_config,
        plate_map_fingerprint=fingerprint,
    )
    records = tuple(
        record
        for fit in sorted(
            valid_fits,
            key=lambda item: (item.experiment_id, item.plate_id, item.well),
        )
        for record in fit.records
    )
    warnings = []
    if clamped_values:
        warnings.append(
            f"Blank correction clamped {clamped_values} negative channel values to zero."
        )
    status = (
        "failed_qc"
        if not records
        else "completed_with_exclusions"
        if exclusions
        else "completed"
    )
    return PlateReaderPreprocessingReport(
        status=status,
        records=records,
        derived_metrics=metrics,
        excluded_wells=tuple(exclusions),
        blank_summary=blank_summary,
        trace_provenance=provenance,
        plate_map_fingerprint=fingerprint,
        config=resolved_config,
        warnings=tuple(warnings),
    )


def _load_raw_readings(source: str | Path) -> tuple[_RawReading, ...]:
    reader = csv.DictReader(StringIO(_source_text(source)))
    required = {
        "experiment_id",
        "plate_id",
        "well",
        "timestamp_s",
        "od600",
        "capacity_fluorescence",
        "output_fluorescence",
    }
    fields = set(reader.fieldnames or ())
    missing = sorted(required - fields)
    if missing:
        raise ValueError(f"Plate-reader CSV is missing columns: {missing}.")
    readings = []
    identities: set[tuple[str, str, float]] = set()
    for source_row, row in enumerate(reader, start=2):
        try:
            timestamp = float(row["timestamp_s"])
            od600 = float(row["od600"])
            capacity = float(row["capacity_fluorescence"])
            output = float(row["output_fluorescence"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid numeric value on CSV row {source_row}.") from exc
        values = (timestamp, od600, capacity, output)
        if not all(math.isfinite(value) and value >= 0.0 for value in values):
            raise ValueError(
                f"CSV row {source_row} contains non-finite or negative measurements."
            )
        experiment_id = str(row["experiment_id"] or "").strip()
        plate_id = str(row["plate_id"] or "").strip()
        well = str(row["well"] or "").strip().upper()
        if not experiment_id or not plate_id or not well:
            raise ValueError(f"CSV row {source_row} has an empty identifier.")
        identity = (plate_id, well, timestamp)
        if identity in identities:
            raise ValueError(f"Duplicate plate/well/timepoint on CSV row {source_row}.")
        identities.add(identity)
        readings.append(
            _RawReading(
                experiment_id,
                plate_id,
                well,
                timestamp,
                od600,
                capacity,
                output,
                source_row,
            )
        )
    if not readings:
        raise ValueError("Plate-reader CSV contains no data rows.")
    return tuple(readings)


def _blank_correction(
    readings: tuple[_RawReading, ...],
    entry_by_well: dict[str, PlateMapEntry],
) -> tuple[
    dict[tuple[str, str, float], dict[str, float]],
    tuple[dict[str, Any], ...],
]:
    grouped: dict[tuple[str, str, float], list[_RawReading]] = {}
    sample_keys: set[tuple[str, str, float]] = set()
    for reading in readings:
        key = (reading.experiment_id, reading.plate_id, reading.timestamp_s)
        if entry_by_well[reading.well].role == "blank":
            grouped.setdefault(key, []).append(reading)
        else:
            sample_keys.add(key)
    missing = sorted(sample_keys - set(grouped))
    if missing:
        raise ValueError(f"Missing blank measurement for plate/timepoints: {missing}.")
    corrections = {}
    summary = []
    for key in sorted(sample_keys):
        blanks = grouped[key]
        correction = {
            "od600": statistics.median(item.od600 for item in blanks),
            "capacity_fluorescence": statistics.median(
                item.capacity_fluorescence for item in blanks
            ),
            "output_fluorescence": statistics.median(
                item.output_fluorescence for item in blanks
            ),
        }
        corrections[key] = correction
        summary.append(
            {
                "experiment_id": key[0],
                "plate_id": key[1],
                "timestamp_s": key[2],
                "blank_wells": sorted(item.well for item in blanks),
                **correction,
            }
        )
    return corrections, tuple(summary)


def _replicate_qc(
    fits: list[_WellFit],
    config: PlateReaderPreprocessingConfig,
    exclusions: list[WellExclusion],
) -> tuple[_WellFit, ...]:
    grouped: dict[tuple[str, str, float, str], list[_WellFit]] = {}
    for fit in fits:
        condition = (
            fit.entry.construct_id,
            fit.entry.inducer_name,
            fit.entry.inducer_concentration,
            fit.entry.inducer_unit,
        )
        grouped.setdefault(condition, []).append(fit)
    accepted = []
    for condition, group in sorted(grouped.items()):
        construct_id = condition[0]
        bio_replicates = {item.entry.biological_replicate for item in group}
        if len(bio_replicates) < config.min_biological_replicates:
            for item in group:
                exclusions.append(
                    WellExclusion(
                        item.experiment_id,
                        item.plate_id,
                        item.well,
                        "insufficient_biological_replicates",
                        f"Construct {construct_id!r} had {len(bio_replicates)}; "
                        f"required {config.min_biological_replicates}.",
                    )
                )
            continue
        median_rate = statistics.median(item.growth_rate_per_h for item in group)
        retained = []
        for item in group:
            deviation = abs(item.growth_rate_per_h - median_rate) / max(
                abs(median_rate), 1e-12
            )
            if deviation > config.replicate_outlier_relative_deviation:
                exclusions.append(
                    WellExclusion(
                        item.experiment_id,
                        item.plate_id,
                        item.well,
                        "replicate_growth_outlier",
                        f"Relative deviation {deviation:.4f} exceeded "
                        f"{config.replicate_outlier_relative_deviation:.4f}.",
                    )
                )
            else:
                retained.append(item)
        if len({item.entry.biological_replicate for item in retained}) < (
            config.min_biological_replicates
        ):
            for item in retained:
                exclusions.append(
                    WellExclusion(
                        item.experiment_id,
                        item.plate_id,
                        item.well,
                        "replicate_count_after_outlier_qc",
                        "Too few biological replicates remained after outlier QC.",
                    )
                )
            continue
        rates = [item.growth_rate_per_h for item in retained]
        cv = statistics.stdev(rates) / statistics.mean(rates) if len(rates) > 1 else 0.0
        if cv > config.max_replicate_cv:
            for item in retained:
                exclusions.append(
                    WellExclusion(
                        item.experiment_id,
                        item.plate_id,
                        item.well,
                        "replicate_growth_cv_exceeds_threshold",
                        f"Growth-rate CV {cv:.4f} exceeded {config.max_replicate_cv:.4f}.",
                    )
                )
            continue
        accepted.extend(retained)
    return tuple(accepted)


def _derived_metrics(
    fits: tuple[_WellFit, ...],
    *,
    constructs: tuple[ConstructMetadata, ...],
    config: PlateReaderPreprocessingConfig,
    plate_map_fingerprint: str,
) -> tuple[DerivedMetric, ...]:
    capacity_by_well = {
        (fit.plate_id, fit.well): statistics.median(
            record.capacity_fluorescence / record.od600
            for record in fit.window_records
            if record.od600 > 0.0
        )
        for fit in fits
    }
    output_by_well = {
        (fit.plate_id, fit.well): statistics.median(
            record.output_fluorescence / record.od600
            for record in fit.window_records
            if record.od600 > 0.0
        )
        for fit in fits
    }
    baseline_ids = {
        item.construct_id
        for item in constructs
        if item.metadata.get("control_role") == "empty_vector_baseline"
    }
    baseline_fits_by_plate: dict[tuple[str, str], list[_WellFit]] = {}
    for fit in fits:
        if fit.entry.construct_id in baseline_ids:
            baseline_fits_by_plate.setdefault(
                (fit.experiment_id, fit.plate_id), []
            ).append(fit)
    metrics = []
    for fit in sorted(
        fits,
        key=lambda item: (item.experiment_id, item.plate_id, item.well),
    ):
        trace_ids = tuple(record.trace_id for record in fit.window_records)
        metadata = {
            "plate_id": fit.plate_id,
            "experiment_id": fit.experiment_id,
            "well": fit.well,
            "inducer_name": fit.entry.inducer_name,
            "inducer_concentration": fit.entry.inducer_concentration,
            "inducer_unit": fit.entry.inducer_unit,
            "growth_r_squared": fit.r_squared,
            "window_start_s": fit.window_records[0].timestamp_s,
            "window_end_s": fit.window_records[-1].timestamp_s,
            "plate_map_fingerprint": plate_map_fingerprint,
            "blank_correction": "per_plate_timepoint_blank_median",
        }
        values = (
            ("growth_rate_per_h", fit.growth_rate_per_h, "1/h", "log_linear_od600"),
            (
                "capacity_signal_per_od",
                capacity_by_well[(fit.plate_id, fit.well)],
                "a.u./OD600",
                "median_blank_corrected_ratio",
            ),
            (
                "output_signal_per_od",
                output_by_well[(fit.plate_id, fit.well)],
                "a.u./OD600",
                "median_blank_corrected_ratio",
            ),
        )
        for metric_name, value, unit, method in values:
            metrics.append(
                DerivedMetric(
                    metric_id=_metric_id(fit, metric_name),
                    metric_name=metric_name,
                    value=value,
                    unit=unit,
                    construct_id=fit.entry.construct_id,
                    source_trace_ids=trace_ids,
                    preprocessing_version=config.preprocessing_version,
                    method=method,
                    biological_replicate=fit.entry.biological_replicate,
                    metadata=dict(metadata),
                )
            )
        baseline_fits = baseline_fits_by_plate.get(
            (fit.experiment_id, fit.plate_id), []
        )
        baseline_capacity = (
            statistics.median(
                capacity_by_well[(item.plate_id, item.well)] for item in baseline_fits
            )
            if baseline_fits
            else None
        )
        baseline_trace_ids = sorted(
            record.trace_id
            for baseline_fit in baseline_fits
            for record in baseline_fit.window_records
        )
        if baseline_capacity is not None and baseline_capacity > 0.0:
            metrics.append(
                DerivedMetric(
                    metric_id=_metric_id(fit, "capacity_loss_fraction"),
                    metric_name="capacity_loss_fraction",
                    value=max(
                        0.0,
                        1.0
                        - capacity_by_well[(fit.plate_id, fit.well)]
                        / baseline_capacity,
                    ),
                    unit="fraction",
                    construct_id=fit.entry.construct_id,
                    source_trace_ids=trace_ids,
                    preprocessing_version=config.preprocessing_version,
                    method="relative_to_empty_vector_capacity_median",
                    biological_replicate=fit.entry.biological_replicate,
                    metadata={
                        **metadata,
                        "baseline_capacity_signal_per_od": baseline_capacity,
                        "baseline_source_trace_ids": baseline_trace_ids,
                    },
                )
            )
    return tuple(metrics)


def _linear_fit(x: list[float], y: list[float]) -> tuple[float, float, float]:
    x_mean = statistics.mean(x)
    y_mean = statistics.mean(y)
    denominator = sum((value - x_mean) ** 2 for value in x)
    if denominator <= 0.0:
        raise ValueError("Cannot fit a window with identical timestamps.")
    slope = (
        sum((x_value - x_mean) * (y_value - y_mean) for x_value, y_value in zip(x, y))
        / denominator
    )
    intercept = y_mean - slope * x_mean
    predictions = [intercept + slope * value for value in x]
    residual = sum(
        (actual - predicted) ** 2 for actual, predicted in zip(y, predictions)
    )
    total = sum((actual - y_mean) ** 2 for actual in y)
    r_squared = 1.0 - residual / total if total > 0.0 else 1.0
    return slope, intercept, max(0.0, min(1.0, r_squared))


def _source_text(source: str | Path) -> str:
    if isinstance(source, Path):
        return source.read_text(encoding="utf-8")
    candidate = str(source)
    if "\n" not in candidate and "\r" not in candidate:
        path = Path(candidate)
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return candidate


def _plate_map_fingerprint(entries: tuple[PlateMapEntry, ...]) -> str:
    payload = [entry.to_dict() for entry in sorted(entries, key=lambda item: item.well)]
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _shaking_condition(context: CalibrationContext) -> str:
    if context.shaking_rpm is None:
        return context.aeration
    return f"{context.shaking_rpm:g} rpm {context.aeration}"


def _metric_id(fit: _WellFit, metric_name: str) -> str:
    return ":".join(
        (
            "m2",
            fit.entry.construct_id,
            fit.entry.biological_replicate,
            fit.experiment_id,
            fit.plate_id,
            fit.well,
            metric_name,
        )
    )


def _duplicates(values: list[float]) -> list[float]:
    return sorted({value for value in values if values.count(value) > 1})
