from __future__ import annotations
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FieldEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_path: str = Field(min_length=1, max_length=256)
    status: str = Field(default="unknown", max_length=64)
    source_uri: str | None = Field(default=None, max_length=2048)
    locator: str | None = Field(default=None, max_length=512)
    note: str = Field(default="", max_length=4000)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class DraftPartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    part_type: str = Field(min_length=1, max_length=64)
    role: str = Field(default="", max_length=2000)
    sequence: str | None = Field(default=None, max_length=2_000_000)
    host_compatibility: list[str] = Field(default_factory=list, max_length=50)
    evidence: FieldEvidenceRequest | None = None


class DraftInteractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=128)
    interaction_type: str = Field(min_length=1, max_length=64)
    label: str = Field(default="", max_length=512)


class ImportDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="", max_length=512)
    source_type: str = Field(default="literature", max_length=64)
    source_uri: str | None = Field(default=None, max_length=2048)
    citation: str = Field(default="", max_length=8000)
    host_organism: str = Field(default="unknown", max_length=512)
    inputs: list[str] = Field(default_factory=list, max_length=100)
    outputs: list[str] = Field(default_factory=list, max_length=100)
    logic_expression: str = Field(default="", max_length=20_000)
    validation_status: str = Field(default="unknown", max_length=64)
    validation_notes: str = Field(default="", max_length=20_000)
    parts: list[DraftPartRequest] = Field(default_factory=list, max_length=2000)
    interactions: list[DraftInteractionRequest] = Field(
        default_factory=list,
        max_length=5000,
    )
    evidence: list[FieldEvidenceRequest] = Field(default_factory=list, max_length=5000)
    notes: str = Field(default="", max_length=20_000)
    created_at: str = Field(default="", max_length=128)
    schema_version: str = Field(default="1.0", max_length=32)


class JsonImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft: ImportDraftRequest


class GenBankImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(default="external_design.gb", max_length=255)
    content: str = Field(min_length=1, max_length=5_000_000)


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left_design_id: str = Field(min_length=1, max_length=128)
    right_design_id: str = Field(min_length=1, max_length=128)
    left_metrics: dict[str, Any] = Field(default_factory=dict)
    right_metrics: dict[str, Any] = Field(default_factory=dict)


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: dict[str, Any]
    profile_id: str = Field(default="research-v1.8", max_length=128)


class BenchmarkRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(min_length=1, max_length=128)
    profile_id: str = Field(default="research-v1.8", max_length=128)


class BenchmarkComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    benchmark_run_ids: list[str] = Field(min_length=2, max_length=20)


class ParameterFitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_id: str = Field(min_length=1, max_length=128)
    csv_content: str = Field(min_length=1, max_length=2_000_000)
    snapshot_id: str | None = Field(default=None, max_length=128)
    concentration_column: str = Field(default="concentration", max_length=128)
    response_column: str = Field(default="response", max_length=128)
    source: str = Field(default="local_plate_reader_fit", max_length=512)
    measurement_context: dict[str, Any] = Field(default_factory=dict)


class ResourceCalibrationWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str | None = Field(default=None, max_length=128)
    input_mode: Literal["raw_plate_reader", "derived_observations"]
    dataset_id: str = Field(min_length=1, max_length=128)
    validation_dataset_id: str = Field(min_length=1, max_length=128)
    context: dict[str, Any]
    constructs: list[dict[str, Any]] = Field(min_length=1, max_length=512)
    validation_split: dict[str, Any]
    raw_csv: str | None = Field(default=None, max_length=2_000_000)
    plate_map: list[dict[str, Any]] = Field(default_factory=list, max_length=384)
    preprocessing_config: dict[str, Any] = Field(default_factory=dict)
    baseline_construct_id: str | None = Field(default=None, max_length=128)
    demand_index_by_construct: dict[str, float] = Field(default_factory=dict)
    demand_index_by_condition: dict[str, float] = Field(default_factory=dict)
    observations: list[dict[str, Any]] = Field(default_factory=list, max_length=20_000)
    defaults: dict[str, float] = Field(default_factory=dict)
    bounds: dict[str, list[float]] = Field(default_factory=dict)
    bootstrap_samples: int = Field(default=100, ge=0, le=10_000)
    bootstrap_seed: int = 1729
    observed_output_fold_changes: dict[str, float] = Field(default_factory=dict)
    predicted_output_fold_changes: dict[str, float] = Field(default_factory=dict)
    output_prediction_model_id: str = Field(default="", max_length=256)

    @model_validator(mode="after")
    def validate_input_mode(self) -> "ResourceCalibrationWorkflowRequest":
        if self.input_mode == "raw_plate_reader":
            if not (self.raw_csv or "").strip():
                raise ValueError("raw_csv is required for raw_plate_reader input.")
            if not self.plate_map:
                raise ValueError("plate_map is required for raw_plate_reader input.")
            if not (self.baseline_construct_id or "").strip():
                raise ValueError(
                    "baseline_construct_id is required for raw_plate_reader input."
                )
        elif not self.observations:
            raise ValueError(
                "observations are required for derived_observations input."
            )
        if bool(self.observed_output_fold_changes) != bool(
            self.predicted_output_fold_changes
        ):
            raise ValueError(
                "Observed and predicted output fold-change mappings must be supplied together."
            )
        return self


class ResourceModelAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameter_ranges: dict[str, list[float]] = Field(default_factory=dict)
    morris_trajectories: int = Field(default=24, ge=4, le=10_000)
    morris_levels: int = Field(default=6, ge=4, le=100)
    sobol_sample_count: int = Field(default=512, ge=128, le=100_000)
    fit_bootstrap_samples: int = Field(default=20, ge=10, le=10_000)
    random_seed: int = 2606

    @model_validator(mode="after")
    def validate_morris_levels(self) -> "ResourceModelAnalysisRequest":
        if self.morris_levels % 2:
            raise ValueError("morris_levels must be even.")
        for name, bounds in self.parameter_ranges.items():
            if len(bounds) != 2 or bounds[0] <= 0 or bounds[1] <= bounds[0]:
                raise ValueError(f"Invalid positive ordered range for {name!r}.")
        return self


class TemporalStageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: float = Field(default=0.0, ge=0.0)
    end: float = Field(default=float("inf"), ge=0.0)
    value: float

    @model_validator(mode="after")
    def validate_window(self) -> "TemporalStageInput":
        if self.end <= self.start:
            raise ValueError("Temporal stage end must be greater than start.")
        return self


class TemporalPatternInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["step", "pulse", "sine"]
    time: float | None = Field(default=None, ge=0.0)
    start_value: float | None = None
    end_value: float | None = None
    start_time: float | None = Field(default=None, ge=0.0)
    end_time: float | None = Field(default=None, ge=0.0)
    active_value: float | None = None
    basal_value: float | None = None
    amplitude: float | None = None
    frequency: float | None = Field(default=None, ge=0.0)
    bias: float | None = None

    @model_validator(mode="after")
    def validate_pattern_fields(self) -> "TemporalPatternInput":
        if self.type == "step":
            missing = [
                name
                for name in ("time", "start_value", "end_value")
                if getattr(self, name) is None
            ]
        elif self.type == "pulse":
            missing = [
                name
                for name in ("start_time", "end_time", "active_value", "basal_value")
                if getattr(self, name) is None
            ]
            if self.start_time is not None and self.end_time is not None:
                if self.end_time <= self.start_time:
                    raise ValueError("Pulse end_time must be greater than start_time.")
        else:
            missing = [
                name
                for name in ("amplitude", "frequency", "bias")
                if getattr(self, name) is None
            ]
        if missing:
            raise ValueError(
                f"Temporal {self.type} input is missing required fields: {', '.join(missing)}."
            )
        return self


TemporalInputValue = TemporalPatternInput | list[TemporalStageInput]


def temporal_inputs_to_dict(
    temporal_inputs: dict[str, TemporalInputValue] | None,
) -> dict[str, Any] | None:
    if temporal_inputs is None:
        return None
    result: dict[str, Any] = {}
    for signal_name, spec in temporal_inputs.items():
        if isinstance(spec, list):
            result[signal_name] = [item.model_dump() for item in spec]
        else:
            result[signal_name] = {
                key: value
                for key, value in spec.model_dump().items()
                if value is not None
            }
    return result


class SimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topology: dict[str, Any]
    parameter_fit_snapshot_id: str | None = Field(default=None, max_length=128)
    host_profile_id: str | None = Field(default=None, max_length=128)
    simulation_time: float = Field(default=600.0, gt=0.0, le=604800.0)
    sample_count: int = Field(default=80, ge=2, le=10000)
    monte_carlo_samples: int = Field(default=1, ge=1, le=1000)
    noise_fraction: float = Field(default=0.15, ge=0.0, le=10.0)
    random_seed: int | None = Field(default=None, ge=0, le=2**32 - 1)
    temporal_inputs: dict[str, TemporalInputValue] | None = Field(default=None)


class RunStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_intent: str = Field(min_length=1, max_length=20_000)
    host_organism: str = Field(
        default="Escherichia coli",
        min_length=1,
        max_length=512,
    )
    compute_budget: int = Field(default=6, ge=1, le=100)
    enable_rag: bool = True
    enable_ode: bool = True
    enable_skill_extraction: bool = True
    monte_carlo_samples: int = Field(default=1, ge=1, le=1000)
    model_name: str | None = Field(default=None, max_length=256)
    api_base: str | None = Field(default=None, max_length=2048)
    cello_command: str | None = Field(default=None, max_length=4000)
    cello_artifact_format: str | None = Field(
        default=None, pattern="^(cello_v2|cello21)$"
    )
    ucf_path: str | None = Field(default=None, max_length=2048)
    sensor_path: str | None = Field(default=None, max_length=2048)
    device_path: str | None = Field(default=None, max_length=2048)


class RunFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    constraints: list[str] = Field(min_length=1, max_length=100)
    action: str = Field(default="repair", pattern="^(repair|exploitation|fallback)$")
    extra_budget: int = Field(default=2, ge=1, le=100)


class RunResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_name: str | None = Field(default=None, max_length=256)
    api_base: str | None = Field(default=None, max_length=2048)
    cello_artifact_format: str | None = Field(
        default=None, pattern="^(cello_v2|cello21)$"
    )


class SimulationComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topology: dict[str, Any]
    parameter_fit_snapshot_id: str = Field(min_length=1, max_length=128)
    host_profile_id: str | None = Field(default=None, max_length=128)
    simulation_time: float = Field(default=600.0, gt=0.0, le=604800.0)
    sample_count: int = Field(default=80, ge=2, le=10000)
    monte_carlo_samples: int = Field(default=1, ge=1, le=1000)
    noise_fraction: float = Field(default=0.15, ge=0.0, le=10.0)
    random_seed: int | None = Field(default=None, ge=0, le=2**32 - 1)
    temporal_inputs: dict[str, TemporalInputValue] | None = Field(default=None)


class ParameterFitSnapshotComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topology: dict[str, Any]
    host_profile_id: str | None = Field(default=None, max_length=128)
    simulation_time: float = Field(default=600.0, gt=0.0, le=604800.0)
    sample_count: int = Field(default=80, ge=2, le=10000)
    monte_carlo_samples: int = Field(default=1, ge=1, le=1000)
    noise_fraction: float = Field(default=0.15, ge=0.0, le=10.0)
    random_seed: int | None = Field(default=None, ge=0, le=2**32 - 1)
    temporal_inputs: dict[str, TemporalInputValue] | None = Field(default=None)


class ParameterSweepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topology: dict[str, Any]
    parameter_name: str = Field(min_length=1, max_length=128)
    sweep_values: list[float] = Field(min_length=1, max_length=100)
    host_profile_id: str | None = Field(default=None, max_length=128)


class BifurcationSweepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topology: dict[str, Any]
    input_name: str = Field(min_length=1, max_length=128)
    input_values: list[float] = Field(min_length=1, max_length=100)
    host_profile_id: str | None = Field(default=None, max_length=128)


class SettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(default="OpenAI", max_length=256)
    model_name: str = Field(default="gpt-5.4-mini", max_length=256)
    api_key: str = Field(default="", max_length=2048)
    api_base: str = Field(default="", max_length=2048)
    cello_command: str = Field(default="", max_length=4000)
    cello_artifact_format: str = Field(
        default="cello_v2", pattern="^(cello_v2|cello21)$"
    )
    ucf_path: str = Field(default="", max_length=2048)
    sensor_path: str = Field(default="", max_length=2048)
    device_path: str = Field(default="", max_length=2048)
    default_host: str = Field(default="Escherichia coli", max_length=512)
    default_compute_budget: int = Field(default=6, ge=1, le=100)


class SettingsTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(default="OpenAI", max_length=256)
    model_name: str = Field(default="gpt-5.4-mini", max_length=256)
    api_key: str = Field(default="", max_length=2048)
    api_base: str = Field(default="", max_length=2048)


class DesignDraftUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_step: int = Field(default=1, ge=1, le=4)
    user_intent: str = Field(default="", max_length=20_000)
    host_organism: str = Field(default="Escherichia coli", max_length=512)
    compute_budget: int = Field(default=6, ge=1, le=100)
    enable_rag: bool = True
    enable_ode: bool = True
    enable_skill_extraction: bool = True
    model_name: str = Field(default="", max_length=256)
    api_base: str = Field(default="", max_length=2048)
    structured_spec: dict[str, Any] = Field(default_factory=dict)
    pm_chat_history: list[dict[str, str]] = Field(default_factory=list)
    pending_proposal: dict[str, Any] = Field(default_factory=dict)
    pm_stage: str = Field(default="elicitation", max_length=128)


class ElicitationProposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    choice: Literal["agree", "override"]
    value: Any = None


class OdeSimulationFormRequest(BaseModel):
    simulation_time: float = Field(default=600.0, ge=10.0, le=86400.0)
    sample_count: int = Field(default=80, ge=5, le=2000)
    noise_fraction: float = Field(default=0.15, ge=0.0, le=2.0)
    random_seed: int | None = Field(default=None, ge=0)


class SsaSimulationFormRequest(BaseModel):
    runs: int = Field(default=50, ge=1, le=1000)
    scale_factor: float = Field(default=10.0, ge=0.01, le=1000.0)
    max_steps: int = Field(default=15000, ge=100, le=1000000)


class ParameterSweepFormRequest(BaseModel):
    parameter_name: str = Field(min_length=1, max_length=128)
    sweep_values: list[float] = Field(min_length=1, max_length=100)


class BifurcationSweepFormRequest(BaseModel):
    input_name: str = Field(min_length=1, max_length=128)
    input_values: list[float] = Field(min_length=1, max_length=100)
