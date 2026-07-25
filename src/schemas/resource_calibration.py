from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any


RESOURCE_CALIBRATION_SCHEMA_VERSION = "0.1.0"

PARAMETER_ROLES = {
    "observed",
    "calibrated",
    "literature_prior",
    "fixed_assumption",
    "inferred",
}

PARAMETER_ROLE_GOVERNANCE_ORIGINS = {
    "observed": "measured",
    "calibrated": "inferred",
    "literature_prior": "literature",
    "fixed_assumption": "default",
    "inferred": "inferred",
}

VALIDATION_SPLIT_STRATEGIES = {
    "construct_holdout",
    "backbone_holdout",
    "rbs_holdout",
    "module_composition_holdout",
    "manual",
}

_WELL_PATTERN = re.compile(r"^[A-Za-z]+[1-9][0-9]*$")


@dataclass(frozen=True)
class CalibrationContext:
    context_id: str
    host_organism: str
    strain: str
    medium: str
    temperature_c: float
    aeration: str
    culture_format: str
    working_volume_ul: float
    instrument: str
    gain_settings: dict[str, Any]
    growth_phase: str
    capacity_reporter: str
    output_reporter: str
    protocol_version: str
    plasmid_backbone: str = ""
    origin: str = ""
    selectable_marker: str = ""
    antibiotic_name: str = ""
    antibiotic_concentration: float | None = None
    antibiotic_unit: str = ""
    shaking_rpm: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = RESOURCE_CALIBRATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "context_id",
            "host_organism",
            "strain",
            "medium",
            "aeration",
            "culture_format",
            "instrument",
            "growth_phase",
            "capacity_reporter",
            "output_reporter",
            "protocol_version",
        ):
            _require_text(getattr(self, name), name)
        _require_finite(self.temperature_c, "temperature_c")
        if self.temperature_c <= 0.0:
            raise ValueError("temperature_c must be positive.")
        _require_positive(self.working_volume_ul, "working_volume_ul")
        _validate_optional_non_negative(
            self.antibiotic_concentration,
            "antibiotic_concentration",
        )
        _validate_optional_non_negative(self.shaking_rpm, "shaking_rpm")
        if self.antibiotic_concentration is not None:
            _require_text(self.antibiotic_name, "antibiotic_name")
            _require_text(self.antibiotic_unit, "antibiotic_unit")
        _require_mapping(self.gain_settings, "gain_settings")
        _require_mapping(self.metadata, "metadata")
        _require_schema_version(self.schema_version)

    def fingerprint(self) -> str:
        payload = self.to_dict()
        payload.pop("context_id", None)
        normalized = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConstructMetadata:
    construct_id: str
    backbone_id: str
    origin: str
    promoter_id: str
    rbs_id: str
    cds_id: str
    terminator_id: str
    copy_number_source: str
    sequence_available: bool
    cds_length_bp: int | None = None
    protein_length_aa: int | None = None
    declared_copy_number: float | None = None
    reporter_maturation_prior_s: float | None = None
    sequence_sha256: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = RESOURCE_CALIBRATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "construct_id",
            "backbone_id",
            "origin",
            "promoter_id",
            "rbs_id",
            "cds_id",
            "terminator_id",
            "copy_number_source",
        ):
            _require_text(getattr(self, name), name)
        _validate_optional_positive_int(self.cds_length_bp, "cds_length_bp")
        _validate_optional_positive_int(
            self.protein_length_aa,
            "protein_length_aa",
        )
        _validate_optional_positive(
            self.declared_copy_number,
            "declared_copy_number",
        )
        _validate_optional_positive(
            self.reporter_maturation_prior_s,
            "reporter_maturation_prior_s",
        )
        if self.sequence_available:
            _require_text(self.sequence_sha256, "sequence_sha256")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", self.sequence_sha256):
                raise ValueError("sequence_sha256 must be a 64-character SHA-256.")
        elif self.sequence_sha256:
            raise ValueError(
                "sequence_sha256 cannot be set when sequence_available is false."
            )
        _require_mapping(self.metadata, "metadata")
        _require_schema_version(self.schema_version)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlateReaderRecord:
    experiment_id: str
    protocol_version: str
    plate_id: str
    well: str
    timestamp_s: float
    biological_replicate: str
    technical_replicate: str
    strain: str
    medium: str
    temperature_c: float
    shaking_condition: str
    construct_id: str
    backbone_id: str
    origin: str
    inducer_name: str
    inducer_concentration: float
    inducer_unit: str
    od600: float
    capacity_fluorescence: float
    output_fluorescence: float
    instrument: str
    gain_settings: dict[str, Any]
    source_row: int
    schema_version: str = RESOURCE_CALIBRATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "experiment_id",
            "protocol_version",
            "plate_id",
            "well",
            "biological_replicate",
            "technical_replicate",
            "strain",
            "medium",
            "shaking_condition",
            "construct_id",
            "backbone_id",
            "origin",
            "instrument",
        ):
            _require_text(getattr(self, name), name)
        if not _WELL_PATTERN.fullmatch(self.well.strip()):
            raise ValueError(f"Invalid well identifier: {self.well!r}.")
        for name in (
            "timestamp_s",
            "inducer_concentration",
            "od600",
            "capacity_fluorescence",
            "output_fluorescence",
        ):
            _require_non_negative(getattr(self, name), name)
        _require_finite(self.temperature_c, "temperature_c")
        if self.temperature_c <= 0.0:
            raise ValueError("temperature_c must be positive.")
        if self.inducer_concentration > 0.0:
            _require_text(self.inducer_name, "inducer_name")
            _require_text(self.inducer_unit, "inducer_unit")
        if not isinstance(self.source_row, int) or self.source_row < 2:
            raise ValueError("source_row must identify a CSV data row (>= 2).")
        _require_mapping(self.gain_settings, "gain_settings")
        _require_schema_version(self.schema_version)

    @property
    def trace_id(self) -> str:
        return ":".join(
            (
                self.experiment_id,
                self.plate_id,
                self.well.upper(),
                _stable_number(self.timestamp_s),
            )
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["trace_id"] = self.trace_id
        return payload


@dataclass(frozen=True)
class ResourceParameterDefinition:
    parameter_name: str
    role: str
    unit: str
    source: str
    measurement_context_id: str
    value: float | None = None
    lower_bound: float | None = None
    upper_bound: float | None = None
    confidence: float | None = None
    is_fittable: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = RESOURCE_CALIBRATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_text(self.parameter_name, "parameter_name")
        _require_text(self.unit, "unit")
        _require_text(self.source, "source")
        _require_text(self.measurement_context_id, "measurement_context_id")
        if self.role not in PARAMETER_ROLES:
            raise ValueError(
                f"role must be one of {sorted(PARAMETER_ROLES)}; got {self.role!r}."
            )
        for name in ("value", "lower_bound", "upper_bound", "confidence"):
            candidate = getattr(self, name)
            if candidate is not None:
                _require_finite(candidate, name)
        if self.lower_bound is not None and self.upper_bound is not None:
            if self.lower_bound > self.upper_bound:
                raise ValueError("lower_bound cannot exceed upper_bound.")
        if self.value is not None:
            if self.lower_bound is not None and self.value < self.lower_bound:
                raise ValueError("value cannot be below lower_bound.")
            if self.upper_bound is not None and self.value > self.upper_bound:
                raise ValueError("value cannot exceed upper_bound.")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1.")
        if self.role == "observed" and self.value is None:
            raise ValueError("Observed parameters require a value.")
        if self.role == "fixed_assumption" and self.is_fittable:
            raise ValueError("Fixed assumptions cannot be marked fittable.")
        if self.role == "inferred" and self.is_fittable:
            raise ValueError(
                "Inferred outputs cannot be marked fittable; use role='calibrated'."
            )
        _require_mapping(self.metadata, "metadata")
        _require_schema_version(self.schema_version)

    @property
    def governance_origin(self) -> str:
        return PARAMETER_ROLE_GOVERNANCE_ORIGINS[self.role]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["governance_origin"] = self.governance_origin
        return payload


@dataclass(frozen=True)
class DerivedMetric:
    metric_id: str
    metric_name: str
    value: float
    unit: str
    construct_id: str
    source_trace_ids: tuple[str, ...]
    preprocessing_version: str
    method: str
    biological_replicate: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = RESOURCE_CALIBRATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "metric_id",
            "metric_name",
            "unit",
            "construct_id",
            "preprocessing_version",
            "method",
        ):
            _require_text(getattr(self, name), name)
        _require_finite(self.value, "value")
        _validate_identifier_tuple(self.source_trace_ids, "source_trace_ids")
        _require_mapping(self.metadata, "metadata")
        _require_schema_version(self.schema_version)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "metric_name": self.metric_name,
            "value": self.value,
            "unit": self.unit,
            "construct_id": self.construct_id,
            "source_trace_ids": list(self.source_trace_ids),
            "preprocessing_version": self.preprocessing_version,
            "method": self.method,
            "biological_replicate": self.biological_replicate,
            "metadata": dict(self.metadata),
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class ValidationSplit:
    split_id: str
    strategy: str
    training_construct_ids: tuple[str, ...]
    validation_construct_ids: tuple[str, ...]
    grouping_key: str
    rationale: str
    random_seed: int | None = None
    frozen: bool = True
    schema_version: str = RESOURCE_CALIBRATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_text(self.split_id, "split_id")
        _require_text(self.grouping_key, "grouping_key")
        _require_text(self.rationale, "rationale")
        if self.strategy not in VALIDATION_SPLIT_STRATEGIES:
            raise ValueError(
                "strategy must be one of "
                f"{sorted(VALIDATION_SPLIT_STRATEGIES)}; got {self.strategy!r}."
            )
        _validate_identifier_tuple(
            self.training_construct_ids,
            "training_construct_ids",
        )
        _validate_identifier_tuple(
            self.validation_construct_ids,
            "validation_construct_ids",
        )
        overlap = set(self.training_construct_ids) & set(self.validation_construct_ids)
        if overlap:
            raise ValueError(
                "Training and validation constructs must be disjoint; overlap: "
                f"{sorted(overlap)}."
            )
        if self.random_seed is not None and not isinstance(self.random_seed, int):
            raise ValueError("random_seed must be an integer or null.")
        if not self.frozen:
            raise ValueError("Validation splits must be frozen before use.")
        _require_schema_version(self.schema_version)

    def validate_constructs(self, known_construct_ids: set[str]) -> None:
        selected = set(self.training_construct_ids) | set(self.validation_construct_ids)
        unknown = selected - known_construct_ids
        if unknown:
            raise ValueError(
                f"Validation split references unknown constructs: {sorted(unknown)}."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "split_id": self.split_id,
            "strategy": self.strategy,
            "training_construct_ids": list(self.training_construct_ids),
            "validation_construct_ids": list(self.validation_construct_ids),
            "grouping_key": self.grouping_key,
            "rationale": self.rationale,
            "random_seed": self.random_seed,
            "frozen": self.frozen,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class ResourceCalibrationDataset:
    dataset_id: str
    context: CalibrationContext
    constructs: tuple[ConstructMetadata, ...]
    records: tuple[PlateReaderRecord, ...]
    parameters: tuple[ResourceParameterDefinition, ...]
    derived_metrics: tuple[DerivedMetric, ...]
    validation_split: ValidationSplit
    schema_version: str = RESOURCE_CALIBRATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_text(self.dataset_id, "dataset_id")
        _require_schema_version(self.schema_version)
        if not self.constructs:
            raise ValueError("At least one construct is required.")
        if not self.records:
            raise ValueError("At least one plate-reader record is required.")

        construct_ids = [item.construct_id for item in self.constructs]
        _require_unique(construct_ids, "construct_id")
        construct_by_id = {item.construct_id: item for item in self.constructs}

        trace_ids = [record.trace_id for record in self.records]
        _require_unique(trace_ids, "plate-reader trace_id")
        trace_index = {record.trace_id: record for record in self.records}
        parameter_names = [item.parameter_name for item in self.parameters]
        _require_unique(parameter_names, "parameter_name")
        metric_ids = [item.metric_id for item in self.derived_metrics]
        _require_unique(metric_ids, "metric_id")

        for record in self.records:
            construct = construct_by_id.get(record.construct_id)
            if construct is None:
                raise ValueError(
                    "Plate-reader record references unknown construct "
                    f"{record.construct_id!r}."
                )
            if record.protocol_version != self.context.protocol_version:
                raise ValueError(
                    f"Protocol mismatch for trace {record.trace_id}: "
                    f"{record.protocol_version!r} != "
                    f"{self.context.protocol_version!r}."
                )
            if record.strain != self.context.strain:
                raise ValueError(
                    f"Strain mismatch for trace {record.trace_id}: "
                    f"{record.strain!r} != {self.context.strain!r}."
                )
            if record.medium != self.context.medium:
                raise ValueError(
                    f"Medium mismatch for trace {record.trace_id}: "
                    f"{record.medium!r} != {self.context.medium!r}."
                )
            if not math.isclose(
                record.temperature_c,
                self.context.temperature_c,
                rel_tol=0.0,
                abs_tol=1e-9,
            ):
                raise ValueError(f"Temperature mismatch for trace {record.trace_id}.")
            if record.instrument != self.context.instrument:
                raise ValueError(
                    f"Instrument mismatch for trace {record.trace_id}: "
                    f"{record.instrument!r} != {self.context.instrument!r}."
                )
            if record.gain_settings != self.context.gain_settings:
                raise ValueError(f"Gain-setting mismatch for trace {record.trace_id}.")
            if record.backbone_id != construct.backbone_id:
                raise ValueError(
                    f"Backbone mismatch for construct {record.construct_id!r}."
                )
            if record.origin != construct.origin:
                raise ValueError(
                    f"Origin mismatch for construct {record.construct_id!r}."
                )
            if (
                self.context.plasmid_backbone
                and record.backbone_id != self.context.plasmid_backbone
            ):
                raise ValueError(
                    f"Context backbone mismatch for trace {record.trace_id}."
                )
            if self.context.origin and record.origin != self.context.origin:
                raise ValueError(
                    f"Context origin mismatch for trace {record.trace_id}."
                )

        for parameter in self.parameters:
            if parameter.measurement_context_id != self.context.context_id:
                raise ValueError(
                    f"Parameter {parameter.parameter_name!r} references context "
                    f"{parameter.measurement_context_id!r}, expected "
                    f"{self.context.context_id!r}."
                )

        for metric in self.derived_metrics:
            if metric.construct_id not in construct_by_id:
                raise ValueError(
                    f"Derived metric {metric.metric_id!r} references unknown "
                    f"construct {metric.construct_id!r}."
                )
            unknown_traces = set(metric.source_trace_ids) - set(trace_index)
            if unknown_traces:
                raise ValueError(
                    f"Derived metric {metric.metric_id!r} references unknown "
                    f"trace IDs: {sorted(unknown_traces)}."
                )
            wrong_construct = [
                trace_id
                for trace_id in metric.source_trace_ids
                if trace_index[trace_id].construct_id != metric.construct_id
            ]
            if wrong_construct:
                raise ValueError(
                    f"Derived metric {metric.metric_id!r} uses traces from a "
                    f"different construct: {sorted(wrong_construct)}."
                )

        self.validation_split.validate_constructs(set(construct_ids))

    @property
    def trace_index(self) -> dict[str, PlateReaderRecord]:
        return {record.trace_id: record for record in self.records}

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "context": self.context.to_dict(),
            "constructs": [item.to_dict() for item in self.constructs],
            "records": [item.to_dict() for item in self.records],
            "parameters": [item.to_dict() for item in self.parameters],
            "derived_metrics": [item.to_dict() for item in self.derived_metrics],
            "validation_split": self.validation_split.to_dict(),
            "schema_version": self.schema_version,
        }


def calibration_context_from_dict(payload: dict[str, Any]) -> CalibrationContext:
    return CalibrationContext(
        context_id=_text(payload, "context_id"),
        host_organism=_text(payload, "host_organism"),
        strain=_text(payload, "strain"),
        medium=_text(payload, "medium"),
        temperature_c=_float(payload, "temperature_c"),
        aeration=_text(payload, "aeration"),
        culture_format=_text(payload, "culture_format"),
        working_volume_ul=_float(payload, "working_volume_ul"),
        instrument=_text(payload, "instrument"),
        gain_settings=_mapping_value(payload.get("gain_settings"), "gain_settings"),
        growth_phase=_text(payload, "growth_phase"),
        capacity_reporter=_text(payload, "capacity_reporter"),
        output_reporter=_text(payload, "output_reporter"),
        protocol_version=_text(payload, "protocol_version"),
        plasmid_backbone=str(payload.get("plasmid_backbone") or ""),
        origin=str(payload.get("origin") or ""),
        selectable_marker=str(payload.get("selectable_marker") or ""),
        antibiotic_name=str(payload.get("antibiotic_name") or ""),
        antibiotic_concentration=_optional_float(
            payload.get("antibiotic_concentration"),
            "antibiotic_concentration",
        ),
        antibiotic_unit=str(payload.get("antibiotic_unit") or ""),
        shaking_rpm=_optional_float(payload.get("shaking_rpm"), "shaking_rpm"),
        metadata=_mapping_value(payload.get("metadata", {}), "metadata"),
        schema_version=str(
            payload.get("schema_version") or RESOURCE_CALIBRATION_SCHEMA_VERSION
        ),
    )


def construct_metadata_from_dict(payload: dict[str, Any]) -> ConstructMetadata:
    return ConstructMetadata(
        construct_id=_text(payload, "construct_id"),
        backbone_id=_text(payload, "backbone_id"),
        origin=_text(payload, "origin"),
        promoter_id=_text(payload, "promoter_id"),
        rbs_id=_text(payload, "rbs_id"),
        cds_id=_text(payload, "cds_id"),
        terminator_id=_text(payload, "terminator_id"),
        copy_number_source=_text(payload, "copy_number_source"),
        sequence_available=_bool(payload.get("sequence_available")),
        cds_length_bp=_optional_int(payload.get("cds_length_bp"), "cds_length_bp"),
        protein_length_aa=_optional_int(
            payload.get("protein_length_aa"),
            "protein_length_aa",
        ),
        declared_copy_number=_optional_float(
            payload.get("declared_copy_number"),
            "declared_copy_number",
        ),
        reporter_maturation_prior_s=_optional_float(
            payload.get("reporter_maturation_prior_s"),
            "reporter_maturation_prior_s",
        ),
        sequence_sha256=str(payload.get("sequence_sha256") or ""),
        metadata=_mapping_value(payload.get("metadata", {}), "metadata"),
        schema_version=str(
            payload.get("schema_version") or RESOURCE_CALIBRATION_SCHEMA_VERSION
        ),
    )


def plate_reader_record_from_dict(
    payload: dict[str, Any],
    *,
    source_row: int | None = None,
) -> PlateReaderRecord:
    return PlateReaderRecord(
        experiment_id=_text(payload, "experiment_id"),
        protocol_version=_text(payload, "protocol_version"),
        plate_id=_text(payload, "plate_id"),
        well=_text(payload, "well"),
        timestamp_s=_float(payload, "timestamp_s"),
        biological_replicate=_text(payload, "biological_replicate"),
        technical_replicate=_text(payload, "technical_replicate"),
        strain=_text(payload, "strain"),
        medium=_text(payload, "medium"),
        temperature_c=_float(payload, "temperature_c"),
        shaking_condition=_text(payload, "shaking_condition"),
        construct_id=_text(payload, "construct_id"),
        backbone_id=_text(payload, "backbone_id"),
        origin=_text(payload, "origin"),
        inducer_name=str(payload.get("inducer_name") or ""),
        inducer_concentration=_float(payload, "inducer_concentration"),
        inducer_unit=str(payload.get("inducer_unit") or ""),
        od600=_float(payload, "od600"),
        capacity_fluorescence=_float(payload, "capacity_fluorescence"),
        output_fluorescence=_float(payload, "output_fluorescence"),
        instrument=_text(payload, "instrument"),
        gain_settings=_mapping_value(payload.get("gain_settings"), "gain_settings"),
        source_row=(
            source_row if source_row is not None else _int(payload, "source_row")
        ),
        schema_version=str(
            payload.get("schema_version") or RESOURCE_CALIBRATION_SCHEMA_VERSION
        ),
    )


def resource_parameter_definition_from_dict(
    payload: dict[str, Any],
) -> ResourceParameterDefinition:
    return ResourceParameterDefinition(
        parameter_name=_text(payload, "parameter_name"),
        role=_text(payload, "role"),
        unit=_text(payload, "unit"),
        source=_text(payload, "source"),
        measurement_context_id=_text(payload, "measurement_context_id"),
        value=_optional_float(payload.get("value"), "value"),
        lower_bound=_optional_float(payload.get("lower_bound"), "lower_bound"),
        upper_bound=_optional_float(payload.get("upper_bound"), "upper_bound"),
        confidence=_optional_float(payload.get("confidence"), "confidence"),
        is_fittable=_bool(payload.get("is_fittable", False)),
        metadata=_mapping_value(payload.get("metadata", {}), "metadata"),
        schema_version=str(
            payload.get("schema_version") or RESOURCE_CALIBRATION_SCHEMA_VERSION
        ),
    )


def derived_metric_from_dict(payload: dict[str, Any]) -> DerivedMetric:
    return DerivedMetric(
        metric_id=_text(payload, "metric_id"),
        metric_name=_text(payload, "metric_name"),
        value=_float(payload, "value"),
        unit=_text(payload, "unit"),
        construct_id=_text(payload, "construct_id"),
        source_trace_ids=_string_tuple(
            payload.get("source_trace_ids"),
            "source_trace_ids",
        ),
        preprocessing_version=_text(payload, "preprocessing_version"),
        method=_text(payload, "method"),
        biological_replicate=str(payload.get("biological_replicate") or ""),
        metadata=_mapping_value(payload.get("metadata", {}), "metadata"),
        schema_version=str(
            payload.get("schema_version") or RESOURCE_CALIBRATION_SCHEMA_VERSION
        ),
    )


def validation_split_from_dict(payload: dict[str, Any]) -> ValidationSplit:
    return ValidationSplit(
        split_id=_text(payload, "split_id"),
        strategy=_text(payload, "strategy"),
        training_construct_ids=_string_tuple(
            payload.get("training_construct_ids"),
            "training_construct_ids",
        ),
        validation_construct_ids=_string_tuple(
            payload.get("validation_construct_ids"),
            "validation_construct_ids",
        ),
        grouping_key=_text(payload, "grouping_key"),
        rationale=_text(payload, "rationale"),
        random_seed=_optional_int(payload.get("random_seed"), "random_seed"),
        frozen=_bool(payload.get("frozen", True)),
        schema_version=str(
            payload.get("schema_version") or RESOURCE_CALIBRATION_SCHEMA_VERSION
        ),
    )


def _require_text(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")


def _require_mapping(value: Any, name: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping.")


def _require_finite(value: Any, name: str) -> None:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric.")
    try:
        candidate = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric.") from exc
    if not math.isfinite(candidate):
        raise ValueError(f"{name} must be finite.")


def _require_positive(value: Any, name: str) -> None:
    _require_finite(value, name)
    if float(value) <= 0.0:
        raise ValueError(f"{name} must be positive.")


def _require_non_negative(value: Any, name: str) -> None:
    _require_finite(value, name)
    if float(value) < 0.0:
        raise ValueError(f"{name} must be non-negative.")


def _validate_optional_positive(value: Any, name: str) -> None:
    if value is not None:
        _require_positive(value, name)


def _validate_optional_non_negative(value: Any, name: str) -> None:
    if value is not None:
        _require_non_negative(value, name)


def _validate_optional_positive_int(value: Any, name: str) -> None:
    if value is not None and (not isinstance(value, int) or value <= 0):
        raise ValueError(f"{name} must be a positive integer or null.")


def _require_schema_version(value: str) -> None:
    if value != RESOURCE_CALIBRATION_SCHEMA_VERSION:
        raise ValueError(
            "Unsupported resource calibration schema version: "
            f"{value!r}; expected {RESOURCE_CALIBRATION_SCHEMA_VERSION!r}."
        )


def _validate_identifier_tuple(values: tuple[str, ...], name: str) -> None:
    if not values:
        raise ValueError(f"{name} must contain at least one identifier.")
    for value in values:
        _require_text(value, name)
    _require_unique(list(values), name)


def _require_unique(values: list[str], name: str) -> None:
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise ValueError(f"Duplicate {name} values: {duplicates}.")


def _text(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "")
    _require_text(value, key)
    return value


def _float(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be numeric.") from exc
    _require_finite(result, key)
    return result


def _optional_float(value: Any, name: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric or null.") from exc
    _require_finite(result, name)
    return result


def _int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer.") from exc


def _optional_int(value: Any, name: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer or null.") from exc
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{name} must be an integer or null.")
    return result


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n", ""}:
        return False
    raise ValueError(f"Cannot interpret {value!r} as a boolean.")


def _mapping_value(value: Any, name: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{name} must contain a JSON object.") from exc
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"{name} must be a mapping or JSON object.")


def _string_tuple(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a list of identifiers.")
    return tuple(str(item) for item in value)


def _stable_number(value: float) -> str:
    return format(float(value), ".12g")
