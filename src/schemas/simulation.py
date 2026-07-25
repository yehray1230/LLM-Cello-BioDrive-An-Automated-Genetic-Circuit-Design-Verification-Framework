from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any

from schemas.design_ir_v2 import DesignIRV2


SIMULATION_SPEC_SCHEMA_VERSION = "1.0"
SIMULATION_RESULT_SCHEMA_VERSION = "1.0"
SIMULATION_MODEL_ID = "resource-aware-regulatory-ode"
SIMULATION_MODEL_VERSION = "1.10.0"


def canonical_payload_hash(payload: Any) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def stable_seed(payload: Any) -> int:
    return int(canonical_payload_hash(payload)[:8], 16)


def parse_logic_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "high", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "low", "off", ""}:
        return False
    return default


@dataclass
class SimulationScenario:
    scenario_id: str
    inputs: dict[str, bool] = field(default_factory=dict)
    expected_outputs: dict[str, bool] = field(default_factory=dict)


@dataclass
class SimulationSpec:
    verilog: str
    scenarios: list[SimulationScenario]
    target_output: str | None = None
    chassis: str = "Escherichia coli"
    copy_number: float = 1.0
    parameters: dict[str, Any] = field(default_factory=dict)
    simulation_time: float = 600.0
    sample_count: int = 80
    monte_carlo_samples: int = 1
    noise_fraction: float = 0.15
    random_seed: int | None = None
    temporal_inputs: dict[str, Any] = field(default_factory=dict)
    solver_methods: list[str] = field(default_factory=lambda: ["BDF", "Radau", "RK4"])
    relative_tolerance: float = 1e-5
    absolute_tolerance: float = 1e-8
    assumptions: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    model_id: str = SIMULATION_MODEL_ID
    model_version: str = SIMULATION_MODEL_VERSION
    schema_version: str = SIMULATION_SPEC_SCHEMA_VERSION

    @property
    def parameter_set_hash(self) -> str:
        return canonical_payload_hash(self.parameters)

    @property
    def scenario_set_hash(self) -> str:
        return canonical_payload_hash([asdict(item) for item in self.scenarios])

    @property
    def configuration_hash(self) -> str:
        return canonical_payload_hash(asdict(self))

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.model_version != SIMULATION_MODEL_VERSION:
            errors.append(
                f"Unsupported simulation model version: {self.model_version}."
            )
        if self.simulation_time <= 0:
            errors.append("simulation_time must be positive.")
        if self.sample_count < 2:
            errors.append("sample_count must be at least 2.")
        if self.monte_carlo_samples < 1:
            errors.append("monte_carlo_samples must be at least 1.")
        if self.copy_number <= 0:
            errors.append("copy_number must be positive.")
        if not self.scenarios:
            errors.append("At least one simulation scenario is required.")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "configuration_hash": self.configuration_hash,
            "parameter_set_hash": self.parameter_set_hash,
            "scenario_set_hash": self.scenario_set_hash,
        }


@dataclass
class SimulationScenarioResult:
    scenario_id: str
    success: bool
    expected_outputs: dict[str, bool] = field(default_factory=dict)
    terminal_outputs: dict[str, float] = field(default_factory=dict)
    error: str | None = None


@dataclass
class SimulationResult:
    status: str
    configuration_hash: str
    parameter_set_hash: str
    scenario_set_hash: str
    scenario_results: list[SimulationScenarioResult] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    solver: dict[str, Any] = field(default_factory=dict)
    model_id: str = SIMULATION_MODEL_ID
    model_version: str = SIMULATION_MODEL_VERSION
    schema_version: str = SIMULATION_RESULT_SCHEMA_VERSION

    @property
    def result_hash(self) -> str:
        return canonical_payload_hash(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "result_hash": self.result_hash}


def simulation_spec_from_topology(
    topology: dict[str, Any],
    *,
    simulation_time: float = 600.0,
    sample_count: int = 80,
    monte_carlo_samples: int = 1,
    noise_fraction: float = 0.15,
    input_signals: list[str] | None = None,
    target_output: str | None = None,
    random_seed: int | None = None,
    temporal_inputs: dict[str, Any] | None = None,
) -> SimulationSpec:
    inputs = input_signals or []
    truth_table = (
        topology.get("truth_table")
        or topology.get("truth_table_or_logic_matrix")
        or topology.get("logic_matrix")
        or []
    )
    if not isinstance(truth_table, list) or not truth_table:
        truth_table = [{**{name: True for name in inputs}, target_output or "Y": True}]

    scenarios: list[SimulationScenario] = []
    for index, row in enumerate(truth_table):
        selected = row if isinstance(row, dict) else {}
        output_key = _output_key(selected, inputs, target_output)
        scenarios.append(
            SimulationScenario(
                scenario_id=f"scenario_{index + 1}",
                inputs={
                    name: parse_logic_value(
                        selected.get(
                            name,
                            selected.get(
                                name.upper(), selected.get(name.lower(), True)
                            ),
                        ),
                        True,
                    )
                    for name in inputs
                },
                expected_outputs=(
                    {str(output_key): parse_logic_value(selected.get(output_key))}
                    if output_key
                    else {}
                ),
            )
        )

    raw_parameters = topology.get("biokinetic_parameters")
    if not isinstance(raw_parameters, dict):
        raw_parameters = {}
    chassis = (
        topology.get("chassis") or raw_parameters.get("host") or "Escherichia coli"
    )
    resource_model_mode = str(topology.get("resource_model_mode") or "legacy_preview")
    resource_model_inputs = {
        "mode": resource_model_mode,
        "protein_lengths_aa": topology.get("protein_lengths_aa", {}),
        "cds_lengths_bp": topology.get("cds_lengths_bp", {}),
        "construct_metadata": topology.get("construct_metadata", {}),
    }
    return SimulationSpec(
        verilog=str(topology.get("verilog") or topology.get("verilog_code") or ""),
        scenarios=scenarios,
        target_output=target_output,
        chassis=str(chassis),
        copy_number=_positive_float(topology.get("copy_number"), 1.0),
        parameters=raw_parameters,
        simulation_time=float(simulation_time),
        sample_count=int(sample_count),
        monte_carlo_samples=max(1, int(monte_carlo_samples)),
        noise_fraction=max(0.0, float(noise_fraction)),
        random_seed=random_seed,
        temporal_inputs=dict(temporal_inputs or {}),
        assumptions=[f"resource_model_mode={resource_model_mode}"],
        provenance={
            "source": "topology",
            "parameter_provenance": topology.get("parameter_provenance", {}),
            "resource_model_inputs": resource_model_inputs,
        },
    )


def simulation_spec_from_design_ir_v2(
    design: DesignIRV2,
    **overrides: Any,
) -> SimulationSpec:
    from tools.sequence_analyzer import detect_degradation_tags

    chassis_value = (
        design.biological_context.chassis.value
        or design.biological_context.host_organism.value
        or "Escherichia coli"
    )
    copy_number = 1.0
    assumptions: list[str] = []
    for plasmid in design.plasmids:
        if plasmid.copy_number.value is not None:
            copy_number = _positive_float(plasmid.copy_number.value, 1.0)
            break
    else:
        assumptions.append("Plasmid copy number was not defined; 1.0 was used.")

    # Detect degradation tags on CDS parts
    biokinetic_params = dict(design.extensions.get("biokinetic_parameters", {}))

    # Check for UCF path and parse gate parameters
    ucf_path = design.extensions.get("ucf_path") or biokinetic_params.get("ucf_path")
    gate_params = {}
    if ucf_path:
        from tools.cello_artifact_parser import parse_ucf_gate_parameters

        gate_params = parse_ucf_gate_parameters(ucf_path)
        if gate_params:
            assumptions.append(
                f"Loaded characterized gate parameters from UCF: {ucf_path}"
            )

    degradation_multipliers = {
        "ssrA_LVA": 8.0,
        "ssrA_LAV": 6.0,
        "ssrA_ASV": 4.0,
    }

    # Map part IDs to logic node IDs (Verilog signal names)
    assignments = getattr(design, "assignments", []) or design.extensions.get(
        "assignments", []
    )
    part_to_logic_node = {}
    for asn in assignments:
        part_id = getattr(asn, "part_id", None) or (
            asn.get("part_id") if isinstance(asn, dict) else None
        )
        node_id = getattr(asn, "logic_node_id", None) or (
            asn.get("logic_node_id") if isinstance(asn, dict) else None
        )
        part_name = getattr(asn, "part_name", None) or (
            asn.get("part_name") if isinstance(asn, dict) else None
        )
        if part_id and node_id:
            part_to_logic_node[part_id] = node_id

            # Map UCF parameters to this node
            match_entry = None
            for key in (part_id, part_name, node_id):
                if key and key in gate_params:
                    match_entry = gate_params[key]
                    break
            if not match_entry and part_id:
                clean_part_id = (
                    part_id.replace("DEMO_", "")
                    .replace("_CDS", "")
                    .replace("_PROM", "")
                )
                if clean_part_id in gate_params:
                    match_entry = gate_params[clean_part_id]

            if match_entry:
                if match_entry.get("K") is not None:
                    biokinetic_params[f"kd_{node_id}"] = match_entry["K"]
                if match_entry.get("n") is not None:
                    biokinetic_params[f"hill_coefficient_{node_id}"] = match_entry["n"]
                ymin = match_entry.get("ymin")
                ymax = match_entry.get("ymax")
                if ymin is not None and ymax is not None and ymax > 0:
                    biokinetic_params[f"leak_fraction_{node_id}"] = ymin / ymax
                assumptions.append(
                    f"Mapped characterized Hill parameters for logic node {node_id} (from part {part_id})."
                )

    # Retrieve default rate
    default_rate = 0.0007
    if "protein_degradation_rate" in biokinetic_params:
        try:
            val = biokinetic_params["protein_degradation_rate"]
            if isinstance(val, dict) and "value" in val:
                default_rate = float(val["value"])
            else:
                default_rate = float(val)
        except (TypeError, ValueError):
            pass

    for part in design.parts:
        if part.part_type.lower() == "cds" and part.sequence:
            tag = detect_degradation_tags(part.sequence)
            if tag in degradation_multipliers:
                multiplier = degradation_multipliers[tag]
                custom_rate = default_rate * multiplier
                biokinetic_params[f"protein_degradation_rate_{part.id}"] = custom_rate
                if part.id in part_to_logic_node:
                    logic_node = part_to_logic_node[part.id]
                    biokinetic_params[f"protein_degradation_rate_{logic_node}"] = (
                        custom_rate
                    )
                assumptions.append(
                    f"Degradation tag {tag} detected on part {part.id}. "
                    f"Protein degradation rate adjusted to {custom_rate:.5f} 1/s."
                )

    spec = simulation_spec_from_topology(
        {
            "verilog": design.extensions.get("verilog", ""),
            "truth_table": design.specification.truth_table,
            "chassis": chassis_value,
            "copy_number": copy_number,
            "biokinetic_parameters": biokinetic_params,
        },
        input_signals=design.specification.inputs,
        target_output=design.specification.outputs[0]
        if design.specification.outputs
        else None,
        **overrides,
    )
    spec.assumptions.extend(assumptions)
    spec.provenance.update(
        {
            "source": "DesignIR v2",
            "design_id": design.design_id,
            "revision_id": design.revision.revision_id,
        }
    )
    return spec


def _output_key(
    row: dict[str, Any],
    inputs: list[str],
    target_output: str | None,
) -> str | None:
    if target_output and target_output in row:
        return target_output
    for key in ("Y", "OUT", "OUTPUT", "Z", "output", "out"):
        if key in row:
            return key
    return next((str(key) for key in row if key not in inputs), target_output)


def _positive_float(value: Any, default: float) -> float:
    try:
        selected = float(value)
    except (TypeError, ValueError):
        return default
    return selected if selected > 0 else default
