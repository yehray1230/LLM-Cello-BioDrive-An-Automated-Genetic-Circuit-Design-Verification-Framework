from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from utils.scalar_values import optional_float as _optional_float
from utils.scalar_values import optional_trimmed_text as _optional_string


HOST_OPTIMIZATION_SCHEMA_VERSION = "1.0.0"


@dataclass
class HostOptimizationCandidate:
    candidate_id: str
    strategy: str
    status: str
    objective_scores: dict[str, float]
    aggregate_score: float
    sequence_overrides: dict[str, str] = field(default_factory=dict)
    recommended_settings: dict[str, Any] = field(default_factory=dict)
    tradeoffs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HostOptimizationResult:
    status: str
    design_id: str
    host_profile_id: str
    objective_weights: dict[str, float]
    candidates: list[HostOptimizationCandidate]
    selected_candidate_id: str | None = None
    limitations: list[str] = field(default_factory=list)
    schema_version: str = HOST_OPTIMIZATION_SCHEMA_VERSION
    optimizer: str = "host-optimization-candidate-ranker"
    optimizer_version: str = "1.0.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExperimentalMeasurement:
    measurement_id: str
    design_id: str
    candidate_id: str | None = None
    host_profile_id: str | None = None
    expression_value: float | None = None
    growth_rate: float | None = None
    burden_value: float | None = None
    on_off_ratio: float | None = None
    units: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HostCalibrationResult:
    calibration_id: str
    status: str
    design_id: str
    host_profile_id: str | None
    measurement_count: int
    summary: dict[str, Any]
    recommendations: list[str] = field(default_factory=list)
    measurements: list[ExperimentalMeasurement] = field(default_factory=list)
    schema_version: str = HOST_OPTIMIZATION_SCHEMA_VERSION
    calibrator: str = "experimental-host-calibration-summary"
    calibrator_version: str = "1.0.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def measurement_from_dict(payload: dict[str, Any]) -> ExperimentalMeasurement:
    return ExperimentalMeasurement(
        measurement_id=str(payload.get("measurement_id") or ""),
        design_id=str(payload.get("design_id") or ""),
        candidate_id=_optional_string(payload.get("candidate_id")),
        host_profile_id=_optional_string(payload.get("host_profile_id")),
        expression_value=_optional_float(payload.get("expression_value")),
        growth_rate=_optional_float(payload.get("growth_rate")),
        burden_value=_optional_float(payload.get("burden_value")),
        on_off_ratio=_optional_float(payload.get("on_off_ratio")),
        units=dict(payload.get("units") or {}),
        metadata=dict(payload.get("metadata") or {}),
    )
def calibration_from_dict(payload: dict[str, Any]) -> HostCalibrationResult:
    return HostCalibrationResult(
        calibration_id=str(payload.get("calibration_id") or ""),
        status=str(payload.get("status") or "needs_more_data"),
        design_id=str(payload.get("design_id") or ""),
        host_profile_id=_optional_string(payload.get("host_profile_id")),
        measurement_count=int(payload.get("measurement_count") or 0),
        summary=dict(payload.get("summary") or {}),
        recommendations=list(payload.get("recommendations") or []),
        measurements=[
            measurement_from_dict(item)
            for item in list(payload.get("measurements") or [])
            if isinstance(item, dict)
        ],
        schema_version=str(
            payload.get("schema_version") or HOST_OPTIMIZATION_SCHEMA_VERSION
        ),
    )
