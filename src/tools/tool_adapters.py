from __future__ import annotations

import importlib.util
import math
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from schemas.state import DesignState
from tools.cello_wrapper import CelloWrapper
from tools.ode_simulator import BatchODESimulator


CAPABILITY_LOGIC_SYNTHESIS = "logic_synthesis"
CAPABILITY_ODE_SIMULATION = "ode_simulation"
CAPABILITY_STOCHASTIC_SIMULATION = "stochastic_simulation"
CAPABILITY_CRN_COMPILATION = "crn_compilation"
CAPABILITY_RNA_FOLDING = "rna_folding"
CAPABILITY_SEQUENCE_OPTIMIZATION = "sequence_optimization"
CAPABILITY_ASSEMBLY_SIMULATION = "assembly_simulation"
CAPABILITY_PRIMER_DESIGN = "primer_design"
CAPABILITY_OFF_TARGET_SCREENING = "off_target_screening"
CAPABILITY_HOMOLOGY_SCREENING = "homology_screening"
CAPABILITY_FORMAT_VALIDATION = "format_validation"
CAPABILITY_SEQUENCE_ANNOTATION = "sequence_annotation"


CAPABILITY_CATALOG: dict[str, dict[str, Any]] = {
    CAPABILITY_LOGIC_SYNTHESIS: {
        "description": "Map logic designs to genetic circuit assignments.",
        "preferred_tools": ["cello"],
        "fallback": "mock_cello_wrapper",
    },
    CAPABILITY_ODE_SIMULATION: {
        "description": "Run deterministic ODE simulation over candidate topologies.",
        "preferred_tools": ["scipy"],
        "fallback": "internal_rk4",
    },
    CAPABILITY_STOCHASTIC_SIMULATION: {
        "description": "Run stochastic verification for sequential or noisy designs.",
        "preferred_tools": ["bioscrape"],
        "fallback": None,
    },
    CAPABILITY_CRN_COMPILATION: {
        "description": "Compile biological designs into CRN/SBML-like models.",
        "preferred_tools": ["biocrnpyler"],
        "fallback": "local_ode_abstractions",
    },
    CAPABILITY_RNA_FOLDING: {
        "description": "Estimate RNA structure or accessibility.",
        "preferred_tools": ["viennarna", "nupack"],
        "fallback": "heuristic_warning",
    },
    CAPABILITY_SEQUENCE_OPTIMIZATION: {
        "description": "Repair sequence constraints and optimize host compatibility.",
        "preferred_tools": ["dna_chisel"],
        "fallback": "local_sequence_optimization",
    },
    CAPABILITY_ASSEMBLY_SIMULATION: {
        "description": "Simulate construct assembly and cloning plans.",
        "preferred_tools": ["dna_cauldron"],
        "fallback": "local_assembly_planner",
    },
    CAPABILITY_PRIMER_DESIGN: {
        "description": "Design assembly or PCR primers.",
        "preferred_tools": ["primer3"],
        "fallback": "local_primer_designer",
    },
    CAPABILITY_OFF_TARGET_SCREENING: {
        "description": "Screen guide-like sequences for potential off-targets.",
        "preferred_tools": ["cas-offinder"],
        "fallback": "deterministic_precheck",
        "license_sensitive": True,
    },
    CAPABILITY_HOMOLOGY_SCREENING: {
        "description": "Screen sequences against local homology databases.",
        "preferred_tools": ["blastn", "bowtie"],
        "fallback": "exact_match_scan",
    },
    CAPABILITY_FORMAT_VALIDATION: {
        "description": "Validate biological exchange formats such as SBOL.",
        "preferred_tools": ["sbol3"],
        "fallback": "export_with_warning",
    },
    CAPABILITY_SEQUENCE_ANNOTATION: {
        "description": "Parse and annotate GenBank or sequence features.",
        "preferred_tools": ["biopython"],
        "fallback": "local_importers_exporters",
    },
    "sequence_record_lookup": {
        "description": "Resolve explicit accession and version to structured sequence record.",
        "preferred_tools": ["ncbi_entrez"],
        "fallback": "fixture_cache",
    },
    "motif_matrix_lookup": {
        "description": "Retrieve versioned PFM/PWM matrix models for transcription factors.",
        "preferred_tools": ["jaspar"],
        "fallback": "fixture_cache",
    },
    "binding_evidence_lookup": {
        "description": "Retrieve experimental binding evidence records.",
        "preferred_tools": ["unibind"],
        "fallback": "fixture_cache",
    },
    "literature_search": {
        "description": "Retrieve traceable publication records and parameter evidence passages.",
        "preferred_tools": ["pubmed", "europepmc"],
        "fallback": "fixture_cache",
    },
}


@dataclass
class ToolWarning:
    code: str
    message: str
    severity: str = "warning"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolAvailability:
    tool_name: str
    adapter_name: str
    capability: str
    status: str
    version: str | None = None
    fallback_available: bool = False
    fallback_used: bool = False
    license_sensitive: bool = False
    warnings: list[ToolWarning] = field(default_factory=list)
    checked_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["warnings"] = [warning.to_dict() for warning in self.warnings]
        return payload


@dataclass
class ToolAdapterResult:
    availability: ToolAvailability
    status: str
    output: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    warnings: list[ToolWarning] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "availability": self.availability.to_dict(),
            "status": self.status,
            "output": self.output,
            "metrics": self.metrics,
            "artifacts": self.artifacts,
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


class ToolAdapter(Protocol):
    tool_name: str
    adapter_name: str
    capability: str

    def available(self) -> ToolAvailability: ...

    def validate_input(self, payload: dict[str, Any]) -> list[ToolWarning]: ...

    def run(self, payload: dict[str, Any]) -> ToolAdapterResult: ...


def normalize_tool_warning(
    code: str,
    message: str,
    severity: str = "warning",
) -> ToolWarning:
    return ToolWarning(
        code=str(code).strip().upper().replace(" ", "_") or "TOOL_WARNING",
        message=str(message).strip() or "Tool warning.",
        severity=str(severity or "warning"),
    )


def detect_python_module(
    module_name: str,
    *,
    tool_name: str,
    adapter_name: str,
    capability: str,
    fallback_available: bool = False,
    license_sensitive: bool = False,
) -> ToolAvailability:
    if importlib.util.find_spec(module_name) is None:
        warnings = [
            normalize_tool_warning(
                "TOOL_UNAVAILABLE",
                f"Optional Python module '{module_name}' is not installed.",
            )
        ]
        return ToolAvailability(
            tool_name=tool_name,
            adapter_name=adapter_name,
            capability=capability,
            status="unavailable",
            fallback_available=fallback_available,
            license_sensitive=license_sensitive,
            warnings=warnings,
        )
    return ToolAvailability(
        tool_name=tool_name,
        adapter_name=adapter_name,
        capability=capability,
        status="available",
        fallback_available=fallback_available,
        license_sensitive=license_sensitive,
    )


def detect_cli_tool(
    executable: str,
    *,
    tool_name: str,
    adapter_name: str,
    capability: str,
    fallback_available: bool = False,
    license_sensitive: bool = False,
) -> ToolAvailability:
    if shutil.which(executable) is None:
        return ToolAvailability(
            tool_name=tool_name,
            adapter_name=adapter_name,
            capability=capability,
            status="unavailable",
            fallback_available=fallback_available,
            license_sensitive=license_sensitive,
            warnings=[
                normalize_tool_warning(
                    "TOOL_UNAVAILABLE",
                    f"Optional executable '{executable}' was not found on PATH.",
                )
            ],
        )
    return ToolAvailability(
        tool_name=tool_name,
        adapter_name=adapter_name,
        capability=capability,
        status="available",
        fallback_available=fallback_available,
        license_sensitive=license_sensitive,
    )


class CelloLogicSynthesisAdapter:
    tool_name = "cello"
    adapter_name = "cello_wrapper"
    capability = CAPABILITY_LOGIC_SYNTHESIS

    def __init__(self, wrapper: CelloWrapper | None = None):
        self.wrapper = wrapper or CelloWrapper()

    def available(self) -> ToolAvailability:
        if self.wrapper.cello_command is None:
            return ToolAvailability(
                tool_name=self.tool_name,
                adapter_name=self.adapter_name,
                capability=self.capability,
                status="fallback",
                fallback_available=True,
                fallback_used=True,
                warnings=[
                    normalize_tool_warning(
                        "FALLBACK_USED",
                        "Cello command is not configured; using mock Cello fallback.",
                    )
                ],
            )
        executable = (
            self.wrapper.cello_command[0]
            if isinstance(self.wrapper.cello_command, list)
            else str(self.wrapper.cello_command).split()[0]
        )
        return detect_cli_tool(
            executable,
            tool_name=self.tool_name,
            adapter_name=self.adapter_name,
            capability=self.capability,
            fallback_available=True,
        )

    def validate_input(self, payload: dict[str, Any]) -> list[ToolWarning]:
        if not payload.get("state") and not payload.get("verilog"):
            return [
                normalize_tool_warning(
                    "MISSING_INPUT",
                    "Cello adapter needs a DesignState or a Verilog string.",
                    "error",
                )
            ]
        return []

    def run(self, payload: dict[str, Any]) -> ToolAdapterResult:
        validation_warnings = self.validate_input(payload)
        if any(warning.severity == "error" for warning in validation_warnings):
            availability = self.available()
            return ToolAdapterResult(
                availability=availability,
                status="failed",
                warnings=validation_warnings + availability.warnings,
            )

        state = payload.get("state")
        if not isinstance(state, DesignState):
            state = DesignState()
            state.verilog_codes = [str(payload.get("verilog") or "")]

        result_state = self.wrapper.run(state)
        topology = (
            result_state.candidate_topologies[0]
            if result_state.candidate_topologies
            else {}
        )
        availability = self.available()
        status = (
            "ok" if topology.get("mapping_status") == "mapped" else availability.status
        )
        warnings = list(validation_warnings)
        warnings.extend(availability.warnings)
        if topology.get("cello_warning"):
            warnings.append(
                normalize_tool_warning("TOOL_WARNING", str(topology["cello_warning"]))
            )

        return ToolAdapterResult(
            availability=availability,
            status=status,
            output={"topology": topology},
            metrics={
                key: topology.get(key)
                for key in (
                    "orthogonality_score",
                    "cello_assignment_score",
                    "cello_buildable",
                    "toxicity",
                    "toxicity_score",
                )
                if key in topology
            },
            artifacts={
                key: topology.get(key)
                for key in ("cello_artifact_dir", "cello_artifact_manifest_path")
                if topology.get(key)
            },
            warnings=warnings,
        )


class ODESimulationAdapter:
    tool_name = "internal_ode_simulator"
    adapter_name = "batch_ode_simulator"
    capability = CAPABILITY_ODE_SIMULATION

    def __init__(self, simulator: BatchODESimulator | None = None):
        self.simulator = simulator or BatchODESimulator()

    def available(self) -> ToolAvailability:
        availability = detect_python_module(
            "scipy",
            tool_name=self.tool_name,
            adapter_name=self.adapter_name,
            capability=self.capability,
            fallback_available=True,
        )
        if availability.status == "unavailable":
            availability.status = "fallback"
            availability.fallback_used = True
            availability.warnings.append(
                normalize_tool_warning(
                    "FALLBACK_USED",
                    "SciPy is unavailable; using internal RK4 ODE integration fallback.",
                )
            )
        return availability

    def validate_input(self, payload: dict[str, Any]) -> list[ToolWarning]:
        if not isinstance(payload.get("topology"), dict):
            return [
                normalize_tool_warning(
                    "MISSING_INPUT",
                    "ODE adapter needs a topology dictionary.",
                    "error",
                )
            ]
        return []

    def run(self, payload: dict[str, Any]) -> ToolAdapterResult:
        validation_warnings = self.validate_input(payload)
        availability = self.available()
        if any(warning.severity == "error" for warning in validation_warnings):
            return ToolAdapterResult(
                availability=availability,
                status="failed",
                warnings=validation_warnings + availability.warnings,
            )
        topology = self.simulator.simulate_topology(dict(payload["topology"]))
        status = "ok" if topology.get("ode_status") == "simulated" else "failed"
        warnings = validation_warnings + availability.warnings
        if topology.get("ode_error"):
            warnings.append(
                normalize_tool_warning("TOOL_FAILED", topology["ode_error"])
            )
        return ToolAdapterResult(
            availability=availability,
            status=status,
            output={"topology": topology},
            metrics={
                key: topology.get(key)
                for key in (
                    "dynamic_margin",
                    "signal_to_noise_ratio",
                    "kinetic_score",
                    "max_burden_nM",
                )
                if key in topology
            },
            warnings=warnings,
        )


def _heuristic_rna_folding_energy(sequence: str) -> float:
    # Heuristic base-pairing energy estimation
    # Returns an estimated MFE (kcal/mol)
    seq = str(sequence).upper().replace("T", "U")
    n = len(seq)
    min_energy = 0.0

    # Simple stem-loop search: look for a stem of length L >= 4
    # and a loop of length 3 to 10
    for loop_len in range(3, 11):
        for stem_len in range(4, 12):
            for i in range(n - 2 * stem_len - loop_len + 1):
                stem1 = seq[i : i + stem_len]
                # Reverse complement of stem2
                stem2 = seq[i + stem_len + loop_len : i + 2 * stem_len + loop_len]
                stem2_rev = stem2[::-1]
                if len(stem1) != stem_len or len(stem2_rev) != stem_len:
                    continue

                # Check compatibility
                matches = 0
                for b1, b2 in zip(stem1, stem2_rev):
                    pair = {b1, b2}
                    if pair == {"A", "U"} or pair == {"G", "C"}:
                        matches += 1
                    elif pair == {"G", "U"}:
                        matches += 0.5

                if matches >= 0.8 * stem_len:
                    # Estimate free energy: each GC/CG is -3.0, AU/UA is -1.5, GU/UG is -0.8
                    # Loop penalty is +3.0
                    energy = 3.0
                    for b1, b2 in zip(stem1, stem2_rev):
                        pair = {b1, b2}
                        if pair == {"G", "C"}:
                            energy -= 3.0
                        elif pair == {"A", "U"}:
                            energy -= 1.5
                        elif pair == {"G", "U"}:
                            energy -= 0.8
                    min_energy = min(min_energy, energy)

    return round(min_energy, 2)


class RNAFoldingAdapter:
    tool_name = "viennarna"
    adapter_name = "viennarna_adapter"
    capability = "rna_folding"

    def available(self) -> ToolAvailability:
        # Detect if 'RNA' (ViennaRNA python binding) is installed
        availability = detect_python_module(
            "RNA",
            tool_name=self.tool_name,
            adapter_name=self.adapter_name,
            capability=self.capability,
            fallback_available=True,
        )
        if availability.status == "unavailable":
            availability.status = "fallback"
            availability.fallback_used = True
            availability.warnings.append(
                normalize_tool_warning(
                    "FALLBACK_USED",
                    "ViennaRNA 'RNA' module is unavailable; using pure-python heuristic fallback.",
                )
            )
        return availability

    def validate_input(self, payload: dict[str, Any]) -> list[ToolWarning]:
        if (
            not isinstance(payload.get("sequence"), str)
            or not payload["sequence"].strip()
        ):
            return [
                normalize_tool_warning(
                    "MISSING_INPUT",
                    "RNA folding adapter needs a non-empty sequence string.",
                    "error",
                )
            ]
        return []

    def run(self, payload: dict[str, Any]) -> ToolAdapterResult:
        validation_warnings = self.validate_input(payload)
        availability = self.available()
        if any(warning.severity == "error" for warning in validation_warnings):
            return ToolAdapterResult(
                availability=availability,
                status="failed",
                warnings=validation_warnings + availability.warnings,
            )

        sequence = str(payload["sequence"]).strip()
        status = "ok"
        output = {}

        if availability.status == "available":
            # Real ViennaRNA folding
            try:
                import RNA

                struct, mfe = RNA.fold(sequence)
                output["structure"] = struct
                output["mfe"] = float(mfe)
                output["free_energy"] = float(mfe)
            except Exception as e:
                status = "failed"
                validation_warnings.append(
                    normalize_tool_warning("TOOL_FAILED", f"ViennaRNA fold failed: {e}")
                )
        else:
            # Fallback heuristic folding energy estimation
            mfe = _heuristic_rna_folding_energy(sequence)
            output["structure"] = "." * len(sequence)
            output["mfe"] = mfe
            output["free_energy"] = mfe

        return ToolAdapterResult(
            availability=availability,
            status=status,
            output=output,
            metrics={"free_energy": output.get("free_energy", 0.0)},
            warnings=validation_warnings + availability.warnings,
        )


class StochasticSimulationAdapter:
    tool_name = "internal_stochastic_simulator"
    adapter_name = "batch_stochastic_simulator"
    capability = "stochastic_simulation"

    def available(self) -> ToolAvailability:
        return ToolAvailability(
            tool_name=self.tool_name,
            adapter_name=self.adapter_name,
            capability=self.capability,
            status="available",
            fallback_available=False,
            fallback_used=False,
            license_sensitive=False,
            warnings=[
                normalize_tool_warning(
                    "INTERNAL_SIMULATOR_USED",
                    "Using the built-in pure-Python Gillespie SSA stochastic simulator.",
                    "info",
                )
            ],
        )

    def validate_input(self, payload: dict[str, Any]) -> list[ToolWarning]:
        warnings: list[ToolWarning] = []
        topology = payload.get("topology")
        if not isinstance(topology, dict) or not topology:
            warnings.append(
                normalize_tool_warning(
                    "MISSING_INPUT",
                    "Stochastic adapter needs a non-empty topology dictionary.",
                    "error",
                )
            )
        else:
            from tools.ode_simulator import (
                parse_verilog_netlist,
                validate_operon_configuration,
            )

            verilog = topology.get("verilog")
            if not isinstance(verilog, str) or not verilog.strip():
                warnings.append(
                    normalize_tool_warning(
                        "INVALID_TOPOLOGY",
                        "topology.verilog must be a non-empty string.",
                        "error",
                    )
                )
            else:
                signals, _ = parse_verilog_netlist(verilog)
                dynamic_signals = [
                    name
                    for name in sorted(signals)
                    if signals[name] in ("wire", "output")
                ]
                if not dynamic_signals:
                    warnings.append(
                        normalize_tool_warning(
                            "INVALID_TOPOLOGY",
                            "topology.verilog must define at least one wire or output signal.",
                            "error",
                        )
                    )
                operon_errors = validate_operon_configuration(
                    topology.get("operons"),
                    dynamic_signals,
                )
                for error in operon_errors:
                    warnings.append(
                        normalize_tool_warning("INVALID_OPERON", error, "error")
                    )

        runs = payload.get("runs", 50)
        if isinstance(runs, bool) or not isinstance(runs, int) or runs <= 0:
            warnings.append(
                normalize_tool_warning(
                    "INVALID_RUNS",
                    "runs must be a positive integer.",
                    "error",
                )
            )

        scale_factor = payload.get("scale_factor", 10.0)
        try:
            valid_scale = (
                not isinstance(scale_factor, bool)
                and math.isfinite(float(scale_factor))
                and float(scale_factor) > 0.0
            )
        except (TypeError, ValueError):
            valid_scale = False
        if not valid_scale:
            warnings.append(
                normalize_tool_warning(
                    "INVALID_SCALE_FACTOR",
                    "scale_factor must be a finite number greater than zero.",
                    "error",
                )
            )
        max_steps = payload.get("max_steps", 15000)
        if (
            isinstance(max_steps, bool)
            or not isinstance(max_steps, int)
            or max_steps <= 0
        ):
            warnings.append(
                normalize_tool_warning(
                    "INVALID_MAX_STEPS",
                    "max_steps must be a positive integer.",
                    "error",
                )
            )

        # Validate random_seed if present
        if "random_seed" in payload:
            random_seed = payload["random_seed"]
            if random_seed is not None and (
                isinstance(random_seed, bool) or not isinstance(random_seed, int)
            ):
                warnings.append(
                    normalize_tool_warning(
                        "INVALID_RANDOM_SEED",
                        "random_seed must be an integer.",
                        "error",
                    )
                )

        # Validate simulation_time if present
        if "simulation_time" in payload:
            sim_time = payload["simulation_time"]
            if isinstance(sim_time, bool):
                warnings.append(
                    normalize_tool_warning(
                        "INVALID_SIMULATION_TIME",
                        "simulation_time must be a finite number greater than zero.",
                        "error",
                    )
                )
            else:
                try:
                    valid_time = (
                        math.isfinite(float(sim_time)) and float(sim_time) > 0.0
                    )
                except (TypeError, ValueError):
                    valid_time = False
                if not valid_time:
                    warnings.append(
                        normalize_tool_warning(
                            "INVALID_SIMULATION_TIME",
                            "simulation_time must be a finite number greater than zero.",
                            "error",
                        )
                    )

        # Validate sample_count if present
        if "sample_count" in payload:
            sc = payload["sample_count"]
            if isinstance(sc, bool) or not isinstance(sc, int) or sc <= 0:
                warnings.append(
                    normalize_tool_warning(
                        "INVALID_SAMPLE_COUNT",
                        "sample_count must be a positive integer.",
                        "error",
                    )
                )
        # Validate temporal_inputs if present
        if "temporal_inputs" in payload:
            temp_in = payload["temporal_inputs"]
            if temp_in is not None and not isinstance(temp_in, dict):
                warnings.append(
                    normalize_tool_warning(
                        "INVALID_TEMPORAL_INPUTS",
                        "temporal_inputs must be a dictionary.",
                        "error",
                    )
                )

        return warnings

    def run(self, payload: dict[str, Any]) -> ToolAdapterResult:
        validation_warnings = self.validate_input(payload)
        availability = self.available()
        if any(warning.severity == "error" for warning in validation_warnings):
            return ToolAdapterResult(
                availability=availability,
                status="failed",
                warnings=validation_warnings + availability.warnings,
            )

        from tools.ode_simulator import BatchODESimulator

        # Extract inputs
        random_seed = payload.get("random_seed")
        if random_seed is not None:
            try:
                random_seed = int(random_seed)
            except (TypeError, ValueError):
                pass

        simulation_time = payload.get("simulation_time")
        if simulation_time is not None:
            try:
                simulation_time = float(simulation_time)
            except (TypeError, ValueError):
                pass

        sample_count = payload.get("sample_count")
        if sample_count is not None:
            try:
                sample_count = int(sample_count)
            except (TypeError, ValueError):
                pass

        temporal_inputs = payload.get("temporal_inputs")

        simulator = BatchODESimulator(
            simulation_time=simulation_time if simulation_time is not None else 600.0,
            sample_count=sample_count if sample_count is not None else 80,
            random_seed=random_seed,
            temporal_inputs=temporal_inputs,
        )
        topology = dict(payload["topology"])
        runs = int(payload.get("runs", 50))
        scale_factor = float(payload.get("scale_factor", 10.0))
        max_steps = int(payload.get("max_steps", 15000))

        try:
            output = simulator.simulate_stochastic(
                topology,
                runs=runs,
                scale_factor=scale_factor,
                max_steps=max_steps,
            )
            if output.get("simulation_status") == "truncated":
                status = "failed"
                validation_warnings.append(
                    normalize_tool_warning(
                        "SSA_STEP_LIMIT_REACHED",
                        "One or more stochastic runs reached max_steps before the requested simulation time.",
                        "error",
                    )
                )
            else:
                status = "ok"
        except Exception as e:
            output = {}
            status = "failed"
            validation_warnings.append(
                normalize_tool_warning(
                    "TOOL_FAILED", f"Stochastic simulation failed: {e}"
                )
            )

        return ToolAdapterResult(
            availability=availability,
            status=status,
            output={"stochastic_result": output},
            metrics={
                "fano_factors": output.get("fano_factors", {}),
                "memory_stability": output.get("memory_stability", 1.0),
                "switching_failure_probability": output.get(
                    "switching_failure_probability", 0.0
                ),
                "simulation_status": output.get("simulation_status"),
                "completed_run_count": output.get("completed_run_count", 0),
                "truncated_run_count": output.get("truncated_run_count", 0),
                "random_seed": output.get("random_seed"),
                "simulation_time": output.get("simulation_time"),
                "sample_count": output.get("sample_count"),
                "temporal_inputs": output.get("temporal_inputs"),
            },
            warnings=validation_warnings + availability.warnings,
        )


class SequenceRecordLookupAdapter:
    tool_name = "ncbi_entrez"
    adapter_name = "sequence_record_lookup_adapter"
    capability = "sequence_record_lookup"

    def __init__(self, cache_dir: str | None = None, fixtures: dict[str, dict[str, Any]] | None = None):
        import pathlib
        self.cache_dir = pathlib.Path(cache_dir or ".scientific_cache")
        self.fixtures = fixtures or {
            "NC_000913.3": {
                "accession": "NC_000913",
                "version": "NC_000913.3",
                "organism": "Escherichia coli str. K-12 substr. MG1655",
                "molecule_type": "DNA",
                "sequence": "ATGAAGCAAGCAACCGAACTTGAAACCCGCC",
                "protein_translation": "MKQATELETR",
                "checksum": "a7b8c9d0e1f2",
            }
        }

    def available(self) -> ToolAvailability:
        return ToolAvailability(
            tool_name=self.tool_name,
            adapter_name=self.adapter_name,
            capability=self.capability,
            status="fallback",
            version="1.0.0",
            fallback_available=True,
            fallback_used=True,
            license_sensitive=False,
            warnings=[
                normalize_tool_warning(
                    "FIXTURE_ONLY",
                    "No live Entrez provider client is implemented; only local fixtures/cache are available.",
                )
            ],
        )

    def validate_input(self, payload: dict[str, Any]) -> list[ToolWarning]:
        warnings: list[ToolWarning] = []
        accession = payload.get("accession")
        if not accession or not isinstance(accession, str):
            warnings.append(
                normalize_tool_warning("INVALID_ACCESSION", "Explicit accession string is required.")
            )
        return warnings

    def run(self, payload: dict[str, Any]) -> ToolAdapterResult:
        availability = self.available()
        warnings = self.validate_input(payload)
        if warnings:
            return ToolAdapterResult(
                availability=availability,
                status="invalid_input",
                output={},
                warnings=warnings,
            )

        accession = payload["accession"].strip()
        version = payload.get("version")
        full_id = f"{accession}.{version}" if version else accession

        # Check local fixtures or cache
        if full_id in self.fixtures or accession in self.fixtures:
            record = self.fixtures.get(full_id) or self.fixtures.get(accession)
            return ToolAdapterResult(
                availability=availability,
                status="ok",
                output={
                    "provider": "ncbi_fixture",
                    "status": "fixture",
                    "record": record,
                },
                metrics={"sequence_length": len(record.get("sequence", ""))},
                warnings=availability.warnings,
            )

        # No provider call is attempted: this adapter is explicitly fixture/cache-only.
        return ToolAdapterResult(
            availability=availability,
            status="unresolved",
            output={
                "provider": "ncbi_entrez",
                "status": "unavailable",
                "accession": full_id,
            },
            warnings=availability.warnings + [
                normalize_tool_warning(
                    "RECORD_UNRESOLVED",
                    f"Sequence record for accession '{full_id}' could not be resolved in offline cache/fixture.",
                    severity="warning",
                )
            ],
        )


class DNAChiselSequenceOptimizationAdapter:
    tool_name = "dna_chisel"
    adapter_name = "dna_chisel_adapter"
    capability = CAPABILITY_SEQUENCE_OPTIMIZATION

    def available(self) -> ToolAvailability:
        availability = detect_python_module(
            "dnachisel",
            tool_name=self.tool_name,
            adapter_name=self.adapter_name,
            capability=self.capability,
            fallback_available=True,
        )
        if availability.status == "unavailable":
            availability.status = "fallback"
            availability.fallback_used = True
            availability.warnings.append(
                normalize_tool_warning(
                    "FALLBACK_USED",
                    "dnachisel module is unavailable; using local sequence optimization fallback.",
                )
            )
        return availability

    def validate_input(self, payload: dict[str, Any]) -> list[ToolWarning]:
        if not isinstance(payload.get("sequence"), str) or not payload["sequence"].strip():
            return [
                normalize_tool_warning(
                    "MISSING_INPUT",
                    "Sequence optimization adapter requires a non-empty sequence string.",
                    "error",
                )
            ]
        return []

    def run(self, payload: dict[str, Any]) -> ToolAdapterResult:
        validation_warnings = self.validate_input(payload)
        availability = self.available()
        if any(w.severity == "error" for w in validation_warnings):
            return ToolAdapterResult(
                availability=availability,
                status="failed",
                warnings=validation_warnings + availability.warnings,
            )

        sequence = str(payload["sequence"]).strip().upper()
        avoid_sites = list(payload.get("avoid_sites") or ["GAATTC", "GGATCC", "GGTCTC"])
        gc_min = float(payload.get("gc_min", 0.40))
        gc_max = float(payload.get("gc_max", 0.65))

        if availability.status == "available":
            try:
                import dnachisel as dc

                constraints = [
                    dc.EnforceGCContent(mini=gc_min, maxi=gc_max),
                ]
                for site in avoid_sites:
                    constraints.append(dc.AvoidPattern(site))

                problem = dc.DnaOptimizationProblem(sequence=sequence, constraints=constraints)
                problem.resolve_constraints()
                opt_seq = str(problem.sequence)
                status = "ok"
                modified = opt_seq != sequence
            except Exception as e:
                opt_seq = sequence
                status = "failed"
                validation_warnings.append(
                    normalize_tool_warning("TOOL_FAILED", f"DNA Chisel optimization failed: {e}")
                )
                modified = False
        else:
            opt_seq = sequence
            modified = False
            for site in avoid_sites:
                if site in opt_seq:
                    replacement = "A" if site[0] != "A" else "T"
                    opt_seq = opt_seq.replace(site, replacement + site[1:])
                    modified = True
            status = "ok"

        gc_content = (opt_seq.count("G") + opt_seq.count("C")) / max(len(opt_seq), 1)
        return ToolAdapterResult(
            availability=availability,
            status=status,
            output={
                "original_sequence": sequence,
                "optimized_sequence": opt_seq,
                "modified": modified,
                "avoid_sites_checked": avoid_sites,
            },
            metrics={
                "gc_content": round(gc_content, 4),
                "sequence_length": len(opt_seq),
                "modified": modified,
            },
            warnings=validation_warnings + availability.warnings,
        )


class DNACauldronAssemblySimulationAdapter:
    tool_name = "dna_cauldron"
    adapter_name = "dna_cauldron_adapter"
    capability = CAPABILITY_ASSEMBLY_SIMULATION

    def available(self) -> ToolAvailability:
        availability = detect_python_module(
            "dnacauldron",
            tool_name=self.tool_name,
            adapter_name=self.adapter_name,
            capability=self.capability,
            fallback_available=True,
        )
        if availability.status == "unavailable":
            availability.status = "fallback"
            availability.fallback_used = True
            availability.warnings.append(
                normalize_tool_warning(
                    "FALLBACK_USED",
                    "dnacauldron module is unavailable; using local assembly planner fallback.",
                )
            )
        return availability

    def validate_input(self, payload: dict[str, Any]) -> list[ToolWarning]:
        fragments = payload.get("fragments")
        if not isinstance(fragments, list) or len(fragments) < 2:
            return [
                normalize_tool_warning(
                    "MISSING_INPUT",
                    "Assembly simulation adapter requires at least two fragment sequences.",
                    "error",
                )
            ]
        return []

    def run(self, payload: dict[str, Any]) -> ToolAdapterResult:
        validation_warnings = self.validate_input(payload)
        availability = self.available()
        if any(w.severity == "error" for w in validation_warnings):
            return ToolAdapterResult(
                availability=availability,
                status="failed",
                warnings=validation_warnings + availability.warnings,
            )

        fragments = [str(f).strip().upper() for f in payload["fragments"]]
        assembly_type = str(payload.get("assembly_type", "golden_gate")).lower()
        enzyme = str(payload.get("enzyme", "BsaI"))

        if availability.status == "available":
            try:
                import dnacauldron as dc

                parts = [dc.Sequence(f, id=f"frag_{i}") for i, f in enumerate(fragments)]
                asm = dc.SingleAssembly(parts, enzyme=enzyme)
                constructs = asm.simulate()
                assembled_seq = str(constructs[0].sequence) if constructs else "".join(fragments)
                status = "ok"
            except Exception as e:
                assembled_seq = "".join(fragments)
                status = "failed"
                validation_warnings.append(
                    normalize_tool_warning("TOOL_FAILED", f"DNA Cauldron assembly failed: {e}")
                )
        else:
            assembled_seq = "".join(fragments)
            status = "ok"

        return ToolAdapterResult(
            availability=availability,
            status=status,
            output={
                "assembled_sequence": assembled_seq,
                "fragment_count": len(fragments),
                "assembly_type": assembly_type,
                "enzyme": enzyme,
            },
            metrics={
                "assembled_length": len(assembled_seq),
                "fragment_count": len(fragments),
            },
            warnings=validation_warnings + availability.warnings,
        )


class Primer3Adapter:
    tool_name = "primer3"
    adapter_name = "primer3_adapter"
    capability = CAPABILITY_PRIMER_DESIGN

    def available(self) -> ToolAvailability:
        availability = detect_python_module(
            "primer3",
            tool_name=self.tool_name,
            adapter_name=self.adapter_name,
            capability=self.capability,
            fallback_available=True,
        )
        if availability.status == "unavailable":
            availability.status = "fallback"
            availability.fallback_used = True
            availability.warnings.append(
                normalize_tool_warning(
                    "FALLBACK_USED",
                    "primer3 module is unavailable; using local primer designer fallback.",
                )
            )
        return availability

    def validate_input(self, payload: dict[str, Any]) -> list[ToolWarning]:
        if not isinstance(payload.get("sequence"), str) or not payload["sequence"].strip():
            return [
                normalize_tool_warning(
                    "MISSING_INPUT",
                    "Primer3 adapter requires a non-empty sequence string.",
                    "error",
                )
            ]
        return []

    def run(self, payload: dict[str, Any]) -> ToolAdapterResult:
        validation_warnings = self.validate_input(payload)
        availability = self.available()
        if any(w.severity == "error" for w in validation_warnings):
            return ToolAdapterResult(
                availability=availability,
                status="failed",
                warnings=validation_warnings + availability.warnings,
            )

        sequence = str(payload["sequence"]).strip().upper()
        target_tm = float(payload.get("target_tm", 60.0))

        if availability.status == "available":
            try:
                import primer3

                tm = float(primer3.calc_tm(sequence[:20]))
                fwd_primer = sequence[:20]
                rev_stem = sequence[-20:][::-1]
                tr = str.maketrans("ATGC", "TACG")
                rev_primer = rev_stem.translate(tr)
                rev_tm = float(primer3.calc_tm(rev_primer))
                status = "ok"
            except Exception as e:
                fwd_primer = sequence[:20]
                rev_primer = sequence[-20:]
                tm = 60.0
                rev_tm = 60.0
                status = "failed"
                validation_warnings.append(
                    normalize_tool_warning("TOOL_FAILED", f"Primer3 calculation failed: {e}")
                )
        else:
            fwd_primer = sequence[:20]
            rev_stem = sequence[-20:][::-1]
            tr = str.maketrans("ATGC", "TACG")
            rev_primer = rev_stem.translate(tr)
            tm = 64.9 + 41.0 * ((fwd_primer.count("G") + fwd_primer.count("C") - 16.4) / len(fwd_primer))
            rev_tm = 64.9 + 41.0 * ((rev_primer.count("G") + rev_primer.count("C") - 16.4) / len(rev_primer))
            status = "ok"

        return ToolAdapterResult(
            availability=availability,
            status=status,
            output={
                "forward_primer": fwd_primer,
                "reverse_primer": rev_primer,
                "forward_tm": round(tm, 2),
                "reverse_tm": round(rev_tm, 2),
                "target_tm": target_tm,
            },
            metrics={
                "forward_tm": round(tm, 2),
                "reverse_tm": round(rev_tm, 2),
                "primer_length": len(fwd_primer),
            },
            warnings=validation_warnings + availability.warnings,
        )


class DNAFeaturesViewerAdapter:
    tool_name = "dna_features_viewer"
    adapter_name = "dna_features_viewer_adapter"
    capability = CAPABILITY_SEQUENCE_ANNOTATION

    def available(self) -> ToolAvailability:
        availability = detect_python_module(
            "dna_features_viewer",
            tool_name=self.tool_name,
            adapter_name=self.adapter_name,
            capability=self.capability,
            fallback_available=True,
        )
        if availability.status == "unavailable":
            availability.status = "fallback"
            availability.fallback_used = True
            availability.warnings.append(
                normalize_tool_warning(
                    "FALLBACK_USED",
                    "dna_features_viewer is unavailable; using text feature map fallback.",
                )
            )
        return availability

    def validate_input(self, payload: dict[str, Any]) -> list[ToolWarning]:
        if not isinstance(payload.get("features"), list):
            return [
                normalize_tool_warning(
                    "MISSING_INPUT",
                    "DNAFeaturesViewer adapter requires a features list.",
                    "error",
                )
            ]
        return []

    def run(self, payload: dict[str, Any]) -> ToolAdapterResult:
        validation_warnings = self.validate_input(payload)
        availability = self.available()
        if any(w.severity == "error" for w in validation_warnings):
            return ToolAdapterResult(
                availability=availability,
                status="failed",
                warnings=validation_warnings + availability.warnings,
            )

        features = list(payload["features"])
        sequence_length = int(payload.get("sequence_length", 1000))

        if availability.status == "available":
            try:
                import dna_features_viewer as dfv

                graphic_features = [
                    dfv.GraphicFeature(
                        start=feat.get("start", 0),
                        end=feat.get("end", 100),
                        strand=feat.get("strand", 1),
                        color=feat.get("color", "#ffd700"),
                        label=feat.get("label", "feature"),
                    )
                    for feat in features
                ]
                dfv.GraphicRecord(sequence_length=sequence_length, features=graphic_features)
                status = "ok"
                summary_map = f"GraphicRecord with {len(features)} features"
            except Exception as e:
                status = "failed"
                summary_map = "Feature map generation failed"
                validation_warnings.append(
                    normalize_tool_warning("TOOL_FAILED", f"dna_features_viewer failed: {e}")
                )
        else:
            status = "ok"
            summary_map = f"TextFeatureMap: {len(features)} features across {sequence_length} bp"

        return ToolAdapterResult(
            availability=availability,
            status=status,
            output={
                "feature_count": len(features),
                "sequence_length": sequence_length,
                "summary_map": summary_map,
                "features": features,
            },
            metrics={"feature_count": len(features)},
            warnings=validation_warnings + availability.warnings,
        )


class SBOL3FormatValidationAdapter:
    tool_name = "sbol3"
    adapter_name = "sbol3_validation_adapter"
    capability = CAPABILITY_FORMAT_VALIDATION

    def available(self) -> ToolAvailability:
        availability = detect_python_module(
            "sbol3",
            tool_name=self.tool_name,
            adapter_name=self.adapter_name,
            capability=self.capability,
            fallback_available=True,
        )
        if availability.status == "unavailable":
            availability.status = "fallback"
            availability.fallback_used = True
            availability.warnings.append(
                normalize_tool_warning(
                    "FALLBACK_USED",
                    "sbol3 module is unavailable; using local SBOL3 structural validator fallback.",
                )
            )
        return availability

    def validate_input(self, payload: dict[str, Any]) -> list[ToolWarning]:
        if not isinstance(payload.get("sbol_content"), str) and not isinstance(payload.get("sbol_document"), dict):
            return [
                normalize_tool_warning(
                    "MISSING_INPUT",
                    "SBOL3 validation adapter requires sbol_content string or sbol_document dict.",
                    "error",
                )
            ]
        return []

    def run(self, payload: dict[str, Any]) -> ToolAdapterResult:
        validation_warnings = self.validate_input(payload)
        availability = self.available()
        if any(w.severity == "error" for w in validation_warnings):
            return ToolAdapterResult(
                availability=availability,
                status="failed",
                warnings=validation_warnings + availability.warnings,
            )

        content = payload.get("sbol_content") or payload.get("sbol_document")

        if availability.status == "available":
            try:
                import sbol3

                doc = sbol3.Document()
                if isinstance(content, str):
                    doc.read_string(content, sbol3.TURTLE)
                report = doc.validate()
                is_valid = len(report.errors) == 0
                errors = [str(e) for e in report.errors]
                status = "ok" if is_valid else "validation_warnings"
            except Exception as e:
                is_valid = False
                errors = [str(e)]
                status = "failed"
                validation_warnings.append(
                    normalize_tool_warning("TOOL_FAILED", f"SBOL3 validation error: {e}")
                )
        else:
            is_valid = True
            errors = []
            if isinstance(content, str):
                if "Component" not in content and "@prefix" not in content and "http://" not in content:
                    is_valid = False
                    errors.append("String content does not match expected SBOL3 Turtle format.")
            status = "ok" if is_valid else "validation_warnings"

        return ToolAdapterResult(
            availability=availability,
            status=status,
            output={
                "is_valid": is_valid,
                "validation_errors": errors,
                "validator": "sbol3" if availability.status == "available" else "local_fallback",
            },
            metrics={"valid": is_valid, "error_count": len(errors)},
            warnings=validation_warnings + availability.warnings,
        )


def default_tool_adapters() -> list[ToolAdapter]:
    return [
        CelloLogicSynthesisAdapter(),
        ODESimulationAdapter(),
        RNAFoldingAdapter(),
        StochasticSimulationAdapter(),
        SequenceRecordLookupAdapter(),
        DNAChiselSequenceOptimizationAdapter(),
        DNACauldronAssemblySimulationAdapter(),
        Primer3Adapter(),
        DNAFeaturesViewerAdapter(),
        SBOL3FormatValidationAdapter(),
    ]


def inspect_capabilities(adapters: list[ToolAdapter] | None = None) -> dict[str, Any]:
    selected = adapters or default_tool_adapters()
    records = [adapter.available().to_dict() for adapter in selected]
    return {
        "catalog": CAPABILITY_CATALOG,
        "tools": records,
        "capabilities": sorted({record["capability"] for record in records}),
    }
