"""Typed contracts for workflow results and evaluator-consumable evidence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from typing import Any, Mapping


WORKFLOW_EVIDENCE_CONTRACT_VERSION = "workflow-evidence-v1"
ODE_TRACE_SERIES_KEYS = (
    "output_protein",
    "total_mrna",
    "total_protein",
    "rnap_occupancy",
    "ribosome_occupancy",
)


@dataclass(frozen=True)
class ODETraceEvidenceV1:
    """Validated view of an existing ODE trace; never synthesizes evidence."""

    present: bool
    time: tuple[float, ...]
    output_protein: tuple[float, ...]
    errors: tuple[str, ...]

    @classmethod
    def from_value(cls, value: Any) -> ODETraceEvidenceV1:
        if not isinstance(value, Mapping):
            return cls(False, (), (), ("missing_ode_trace",))

        errors: list[str] = []
        raw_times = value.get("time")
        raw_outputs = value.get("output_protein")
        times = list(raw_times) if isinstance(raw_times, list) else []
        outputs = list(raw_outputs) if isinstance(raw_outputs, list) else []
        if not times:
            errors.append("missing_ode_trace_time")
        if not outputs:
            errors.append("missing_ode_trace_output_protein")
        if times and outputs and len(times) != len(outputs):
            errors.append("ode_trace_length_mismatch")

        numeric_trace = True
        for item in [*times, *outputs]:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                numeric_trace = False
                break
            if not math.isfinite(float(item)):
                numeric_trace = False
                break
        if (times or outputs) and not numeric_trace:
            errors.append("ode_trace_requires_finite_numeric_values")

        if numeric_trace and len(times) > 1 and any(
            float(current) <= float(previous)
            for previous, current in zip(times, times[1:])
        ):
            errors.append("ode_trace_time_not_strictly_increasing")

        normalized_times = tuple(float(item) for item in times) if numeric_trace else ()
        normalized_outputs = (
            tuple(float(item) for item in outputs) if numeric_trace else ()
        )
        return cls(
            present=True,
            time=normalized_times,
            output_protein=normalized_outputs,
            errors=tuple(errors),
        )

    @property
    def is_valid(self) -> bool:
        return (
            self.present
            and not self.errors
            and bool(self.time)
            and len(self.time) == len(self.output_protein)
        )

    @property
    def sample_count(self) -> int:
        return len(self.time) if self.is_valid else 0


def is_valid_ode_trace(value: Any) -> bool:
    """Return whether an existing trace satisfies the canonical v1 contract."""
    return ODETraceEvidenceV1.from_value(value).is_valid


def project_ode_trace_rows(value: Any) -> list[dict[str, float]]:
    """Project aligned finite trace series into UI-safe rows without filling gaps."""
    trace = ODETraceEvidenceV1.from_value(value)
    if not trace.is_valid or not isinstance(value, Mapping):
        return []

    series: dict[str, tuple[float, ...]] = {
        "output_protein": trace.output_protein
    }
    for key in ODE_TRACE_SERIES_KEYS:
        if key == "output_protein":
            continue
        raw_values = value.get(key)
        if not isinstance(raw_values, list) or len(raw_values) != len(trace.time):
            continue
        if any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
            for item in raw_values
        ):
            continue
        series[key] = tuple(float(item) for item in raw_values)

    return [
        {
            "time": time_value,
            **{key: values[index] for key, values in series.items()},
        }
        for index, time_value in enumerate(trace.time)
    ]


@dataclass(frozen=True)
class SimulationEvidenceV1:
    """Typed projection of simulation status, scenarios, and ODE trace evidence."""

    status: str
    raw_result: dict[str, Any]
    scenario_count: int
    ode_trace: ODETraceEvidenceV1

    @classmethod
    def from_topology(cls, topology: Mapping[str, Any]) -> SimulationEvidenceV1:
        raw_simulation = topology.get("simulation_result")
        simulation = (
            deepcopy(dict(raw_simulation))
            if isinstance(raw_simulation, Mapping)
            else {}
        )
        scenario_results = simulation.get("scenario_results")
        return cls(
            status=str(simulation.get("status") or ""),
            raw_result=simulation,
            scenario_count=(
                len(scenario_results) if isinstance(scenario_results, list) else 0
            ),
            ode_trace=ODETraceEvidenceV1.from_value(topology.get("ode_trace")),
        )

    @property
    def combinational_complete(self) -> bool:
        return self.status == "simulated" and (
            self.scenario_count > 0 or self.ode_trace.is_valid
        )

    @property
    def temporal_complete(self) -> bool:
        return self.status == "simulated" and self.ode_trace.is_valid

    def evaluator_result(self, *, complete: bool, incomplete_reason: str) -> dict[str, Any]:
        result = deepcopy(self.raw_result)
        if not complete:
            result.update(
                {
                    "status": "incomplete_evidence",
                    "adapter_incomplete_reason": incomplete_reason,
                }
            )
        return result


@dataclass(frozen=True)
class WorkflowEvidenceEnvelopeV1:
    """Normalized typed view of legacy or standard workflow service payloads."""

    status: str
    service_status: str
    data: dict[str, Any]
    error: Any
    error_type: Any
    source_payload: dict[str, Any]
    already_standard: bool

    @classmethod
    def from_service_payload(
        cls, payload: Mapping[str, Any]
    ) -> WorkflowEvidenceEnvelopeV1:
        source = deepcopy(dict(payload))
        if isinstance(source.get("data"), Mapping):
            data = deepcopy(dict(source["data"]))
            return cls(
                status=str(source.get("status") or ""),
                service_status=str(source.get("service_status") or ""),
                data=data,
                error=source.get("error"),
                error_type=source.get("error_type"),
                source_payload=source,
                already_standard=True,
            )

        service_status = str(source.get("status") or "")
        summary = source.get("summary")
        data = deepcopy(dict(summary)) if isinstance(summary, Mapping) else {}
        data["status"] = service_status
        for key in ("run_dir", "artifacts", "warnings", "safety"):
            if key in source:
                data[key] = deepcopy(source[key])
        status = (
            "success"
            if service_status in {"completed", "needs_human_input"}
            else service_status
        )
        return cls(
            status=status,
            service_status=service_status,
            data=data,
            error=source.get("error"),
            error_type=source.get("error_type"),
            source_payload=source,
            already_standard=False,
        )

    @property
    def best_topology(self) -> dict[str, Any] | None:
        value = self.data.get("best_topology")
        return deepcopy(dict(value)) if isinstance(value, Mapping) else None

    def to_benchmark_payload(self) -> dict[str, Any]:
        if self.already_standard:
            return deepcopy(self.source_payload)
        return {
            "status": self.status,
            "service_status": self.service_status,
            "data": deepcopy(self.data),
            "error": self.error,
            "error_type": self.error_type,
        }
