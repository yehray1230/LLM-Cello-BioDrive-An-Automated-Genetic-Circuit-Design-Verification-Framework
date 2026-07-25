from __future__ import annotations

import math
import re
from copy import deepcopy
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import numpy as np

try:
    from scipy.integrate import solve_ivp
except ModuleNotFoundError:
    solve_ivp = None

try:
    from joblib import Memory
except ModuleNotFoundError:
    Memory = None

from agents.data_miner_agent import DEFAULT_BIOKINETIC_PARAMETERS
from schemas.simulation import (
    SIMULATION_MODEL_VERSION,
    SimulationResult,
    parse_logic_value,
    simulation_spec_from_topology,
    stable_seed,
)
from schemas.state import DesignState
from utils.boolean_values import defaulted_bool as _coerce_bool


LEGACY_RESOURCE_MODEL_MODE = "legacy_preview"
BASELINE_RELATIVE_RESOURCE_MODEL_MODE = "baseline_relative_v0.1"
DEFAULT_HOST_RNAP_BASELINE_FREE_FRACTION = 0.80
DEFAULT_HOST_RIBOSOME_BASELINE_FREE_FRACTION = 0.70
DEFAULT_PROTEIN_LENGTH_AA = 250.0
DEFAULT_TRANSLATION_ELONGATION_RATE_AA_S = 15.0


@dataclass
class WarmStartResourceSolver:
    rnap_free: float
    ribosome_free: float
    fixed_point_iterations: int = 5
    bisection_iterations: int = 18
    tolerance: float = 1e-6

    def solve_rnap(
        self, total: float, demands: np.ndarray, km: float
    ) -> tuple[float, float]:
        free = self._solve(total, demands, km, self.rnap_free)
        self.rnap_free = free
        occupancy = 1.0 - free / max(total, 1e-9)
        return free, _clamp01(occupancy)

    def solve_ribosome(
        self, total: float, mrna: np.ndarray, km: float
    ) -> tuple[float, float]:
        free = self._solve(total, np.maximum(mrna, 0.0), km, self.ribosome_free)
        self.ribosome_free = free
        occupancy = 1.0 - free / max(total, 1e-9)
        return free, _clamp01(occupancy)

    def _solve(
        self, total: float, demands: np.ndarray, km: float, warm_start: float
    ) -> float:
        total = max(float(total), 1e-9)
        km = max(float(km), 1e-9)
        x = min(max(float(warm_start), 0.0), total)

        for _ in range(self.fixed_point_iterations):
            f_value, derivative = self._constraint(x, total, demands, km)
            if abs(f_value) <= self.tolerance * total:
                return x
            step = f_value / max(derivative, 1e-9)
            x = min(max(x - step, 0.0), total)

        lo, hi = 0.0, total
        for _ in range(self.bisection_iterations):
            mid = 0.5 * (lo + hi)
            f_mid, _ = self._constraint(mid, total, demands, km)
            if f_mid > 0.0:
                hi = mid
            else:
                lo = mid
        return 0.5 * (lo + hi)

    @staticmethod
    def _constraint(
        x: float, total: float, demands: np.ndarray, km: float
    ) -> tuple[float, float]:
        denom = km + x
        bound = float(np.sum(demands * x / denom))
        derivative = 1.0 + float(np.sum(demands * km / (denom * denom)))
        return x + bound - total, derivative


def _strip_verilog_comments(verilog: str) -> str:
    without_block = re.sub(r"/\*.*?\*/", "", verilog, flags=re.DOTALL)
    return re.sub(r"//.*", "", without_block)


def _clean_signal_name(value: str) -> str:
    value = re.sub(r"\b(?:input|output|wire|reg)\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\[[^\]]+\]", "", value)
    match = re.search(r"[A-Za-z_]\w*", value.strip())
    return match.group(0) if match else ""


def _extract_verilog_signals(code: str) -> tuple[set[str], set[str], set[str]]:
    signals: dict[str, set[str]] = {"input": set(), "output": set(), "wire": set()}
    for keyword in signals:
        for match in re.finditer(
            rf"\b{keyword}\b\s*(?:\[[^\]]+\]\s*)?([^;);]+)", code, flags=re.IGNORECASE
        ):
            declaration = re.split(
                r"\b(?:input|output|wire|module|endmodule)\b",
                match.group(1),
                flags=re.IGNORECASE,
            )[0]
            for name in re.split(r",", declaration):
                signal = _clean_signal_name(name)
                if signal:
                    signals[keyword].add(signal)
        for match in re.finditer(
            rf"\b{keyword}\b\s*(?:\[[^\]]+\]\s*)?([A-Za-z_]\w*)",
            code,
            flags=re.IGNORECASE,
        ):
            signals[keyword].add(match.group(1))
    return signals["input"], signals["output"], signals["wire"]


def parse_verilog_netlist(
    verilog: str,
) -> tuple[dict[str, str], dict[str, tuple[str, list[str]]]]:
    code = _strip_verilog_comments(verilog)
    inputs, outputs, wires = _extract_verilog_signals(code)

    signals = {}
    for inp in inputs:
        signals[inp] = "input"
    for out in outputs:
        signals[out] = "output"
    for w in wires:
        signals.setdefault(w, "wire")

    deps = {}

    # 1. Parse primitive gates
    for gate, body in re.findall(
        r"\b(and|or|not|nand|nor|xor|xnor|buf)\s+(?:[A-Za-z_]\w*\s*)?\(([^;]+?)\)\s*;",
        code,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        parts = [_clean_signal_name(p) for p in body.split(",") if p.strip()]
        parts = [p for p in parts if p]
        if len(parts) >= 2:
            out_sig = parts[0]
            in_sigs = parts[1:]
            deps[out_sig] = (gate.lower(), in_sigs)
            signals.setdefault(out_sig, "wire" if out_sig not in outputs else "output")
            for sig in in_sigs:
                if sig not in signals:
                    signals[sig] = "wire"

    # 2. Parse assign statements
    for lhs, rhs in re.findall(
        r"\bassign\s+([^=;]+?)\s*=\s*([^;]+?)\s*;",
        code,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        out_sig = _clean_signal_name(lhs)
        if not out_sig:
            continue
        rhs_expr = rhs.strip()
        rhs_expr_clean = re.sub(r"\s+", "", rhs_expr)

        # NOR pattern
        nor_match = re.match(r"^[~!]\(([^|)]+)(?:\|\||\|)([^|)]+)\)$", rhs_expr_clean)
        if nor_match:
            in1 = _clean_signal_name(nor_match.group(1))
            in2 = _clean_signal_name(nor_match.group(2))
            deps[out_sig] = ("nor", [in1, in2])
            signals.setdefault(out_sig, "wire" if out_sig not in outputs else "output")
            continue

        # NAND pattern
        nand_match = re.match(r"^[~!]\(([^&)]+)(?:&&|&)([^&)]+)\)$", rhs_expr_clean)
        if nand_match:
            in1 = _clean_signal_name(nand_match.group(1))
            in2 = _clean_signal_name(nand_match.group(2))
            deps[out_sig] = ("nand", [in1, in2])
            signals.setdefault(out_sig, "wire" if out_sig not in outputs else "output")
            continue

        # NOT pattern
        not_match = re.match(r"^[~!]([A-Za-z_]\w*)$", rhs_expr_clean)
        if not_match:
            in1 = _clean_signal_name(not_match.group(1))
            deps[out_sig] = ("not", [in1])
            signals.setdefault(out_sig, "wire" if out_sig not in outputs else "output")
            continue

        # AND pattern
        and_match = re.match(r"^([A-Za-z_]\w*)(?:&&|&)([A-Za-z_]\w*)$", rhs_expr_clean)
        if and_match:
            in1 = _clean_signal_name(and_match.group(1))
            in2 = _clean_signal_name(and_match.group(2))
            deps[out_sig] = ("and", [in1, in2])
            signals.setdefault(out_sig, "wire" if out_sig not in outputs else "output")
            continue

        # OR pattern
        or_match = re.match(
            r"^([A-Za-z_]\w*)(?:\|\||\|)([A-Za-z_]\w*)$", rhs_expr_clean
        )
        if or_match:
            in1 = _clean_signal_name(or_match.group(1))
            in2 = _clean_signal_name(or_match.group(2))
            deps[out_sig] = ("or", [in1, in2])
            signals.setdefault(out_sig, "wire" if out_sig not in outputs else "output")
            continue

        # Buffer pattern
        buf_match = re.match(r"^([A-Za-z_]\w*)$", rhs_expr_clean)
        if buf_match:
            in1 = _clean_signal_name(buf_match.group(1))
            deps[out_sig] = ("buf", [in1])
            signals.setdefault(out_sig, "wire" if out_sig not in outputs else "output")
            continue

    for name in inputs:
        signals[name] = "input"
    for name in outputs:
        signals[name] = "output"

    return signals, deps


def validate_operon_configuration(
    raw_operons: Any,
    dynamic_signals: list[str],
) -> list[str]:
    if raw_operons is None:
        return []
    if not isinstance(raw_operons, list):
        return ["operons must be a list of non-empty gene lists."]

    errors: list[str] = []
    known_genes = set(dynamic_signals)
    assigned_genes: set[str] = set()
    for operon_index, operon in enumerate(raw_operons):
        if not isinstance(operon, list) or not operon:
            errors.append(f"operons[{operon_index}] must be a non-empty gene list.")
            continue
        for gene_index, raw_gene in enumerate(operon):
            if not isinstance(raw_gene, str) or not raw_gene.strip():
                errors.append(
                    f"operons[{operon_index}][{gene_index}] must be a non-empty gene name."
                )
                continue
            gene = raw_gene.strip()
            if gene not in known_genes:
                errors.append(
                    f"operons[{operon_index}] references unknown gene '{gene}'."
                )
            elif gene in assigned_genes:
                errors.append(f"gene '{gene}' appears in more than one operon.")
            else:
                assigned_genes.add(gene)
    return errors


@dataclass
class ResourceAwareSimulation:
    signals: dict[str, str]
    deps: dict[str, tuple[str, list[str]]]
    params: dict[str, float]
    solver: WarmStartResourceSolver
    resource_trace: list[dict[str, float]] = field(default_factory=list)
    temporal_inputs: dict[str, Any] | None = None
    operons_config: list[list[str]] | None = None

    def __post_init__(self):
        self.dynamic_signals = [
            name
            for name in sorted(self.signals.keys())
            if self.signals[name] in ("wire", "output")
        ]
        self.signal_idx = {name: idx for idx, name in enumerate(self.dynamic_signals)}

        # Pre-calculate regulator targets: maps regulator (signal name) to list of (target_gate_output, kd)
        self.regulator_targets = {}
        default_kd = self.params.get("kd", 1.0)
        for out_sig, (gate_type, gate_inputs) in self.deps.items():
            kd_val = self.params.get(f"kd_{out_sig}", default_kd)
            for inp in gate_inputs:
                self.regulator_targets.setdefault(inp, []).append((out_sig, kd_val))

        # Tracking max retroactivity index for each regulator
        self.max_retroactivity = {reg: 0.0 for reg in self.regulator_targets.keys()}
        # Warm-start values for multi-target free regulator solver
        self.regulator_free_prev = {reg: 0.0 for reg in self.regulator_targets.keys()}

        # Parse operons
        raw_operons = self.operons_config
        if isinstance(raw_operons, list):
            self.operons = [list(op) for op in raw_operons]
            # Find any dynamic signals not covered by raw_operons
            covered = {gene for op in self.operons for gene in op}
            for name in self.dynamic_signals:
                if name not in covered:
                    self.operons.append([name])
        else:
            self.operons = [[name] for name in self.dynamic_signals]

        self.num_operons = len(self.operons)

        # Maps gene name to (operon_idx, position_in_operon)
        self.gene_to_operon = {}
        for op_idx, op in enumerate(self.operons):
            for pos, gene in enumerate(op):
                self.gene_to_operon[gene] = (op_idx, pos)

        self.baseline_relative_resources = bool(
            self.params.get("baseline_relative_resource_model", 0.0) >= 0.5
        )
        if self.baseline_relative_resources:
            rnap_total = self.params["rnap_total"]
            ribosome_total = self.params["ribosome_total"]
            rnap_fraction = self.params["host_rnap_baseline_free_fraction"]
            ribosome_fraction = self.params["host_ribosome_baseline_free_fraction"]
            self.rnap_baseline_free = rnap_total * rnap_fraction
            self.ribosome_baseline_free = ribosome_total * ribosome_fraction
            self.host_rnap_background_demand = _background_demand_for_free_fraction(
                rnap_total,
                self.params["km_rnap"],
                rnap_fraction,
            )
            self.host_ribosome_background_demand = _background_demand_for_free_fraction(
                ribosome_total,
                self.params["km_ribosome"],
                ribosome_fraction,
            )
        else:
            self.rnap_baseline_free = self.params["rnap_total"]
            self.ribosome_baseline_free = self.params["ribosome_total"]
            self.host_rnap_background_demand = 0.0
            self.host_ribosome_background_demand = 0.0

    def _translation_demand_weight(self, gene: str) -> float:
        default_length = max(
            self.params.get("default_protein_length_aa", DEFAULT_PROTEIN_LENGTH_AA),
            1.0,
        )
        length_aa = max(
            self.params.get(f"protein_length_aa_{gene}", default_length),
            1.0,
        )
        default_elongation = max(
            self.params.get(
                "translation_elongation_rate_aa_s",
                DEFAULT_TRANSLATION_ELONGATION_RATE_AA_S,
            ),
            1e-9,
        )
        elongation = max(
            self.params.get(
                f"translation_elongation_rate_aa_s_{gene}",
                default_elongation,
            ),
            1e-9,
        )
        residence_time = length_aa / elongation
        reference_residence_time = default_length / default_elongation
        base_translation = max(self.params.get("translation_rate", 1.0), 1e-9)
        gene_translation = max(
            self.params.get(f"translation_rate_{gene}", base_translation),
            0.0,
        )
        rbs_strength = max(
            self.params.get(
                f"rbs_strength_{gene}",
                gene_translation / base_translation,
            ),
            0.0,
        )
        return rbs_strength * residence_time / max(reference_residence_time, 1e-9)

    def _solve_free_regulator(
        self,
        regulator: str,
        p_tot: float,
        copy_number: float,
        targets: list[tuple[str, float]],
    ) -> float:
        if p_tot <= 0.0:
            return 0.0
        if copy_number <= 0.0 or not targets:
            return p_tot

        m = len(targets)
        if m == 1:
            # Analytical quadratic solution
            _, kd = targets[0]
            b = kd + copy_number - p_tot
            c = -kd * p_tot
            disc = b * b - 4.0 * c
            p_free = 0.5 * (-b + math.sqrt(max(disc, 0.0)))
            return min(max(p_free, 0.0), p_tot)
        else:
            # Newton-Raphson with warm start
            x_prev = self.regulator_free_prev.get(regulator, p_tot)
            x = min(max(x_prev, 0.0), p_tot)

            # Newton iterations
            converged = False
            for _ in range(8):
                f_val = x - p_tot
                f_prime = 1.0
                for _, kd in targets:
                    denom = kd + x
                    f_val += copy_number * x / denom
                    f_prime += copy_number * kd / (denom * denom)

                if abs(f_val) < 1e-7 * p_tot:
                    converged = True
                    break
                step = f_val / f_prime
                x = x - step
                x = min(max(x, 0.0), p_tot)

            if not converged:
                # Bisection fallback
                lo, hi = 0.0, p_tot
                for _ in range(16):
                    mid = 0.5 * (lo + hi)
                    f_mid = mid - p_tot
                    for _, kd in targets:
                        f_mid += copy_number * mid / (kd + mid)
                    if f_mid > 0.0:
                        hi = mid
                    else:
                        lo = mid
                x = 0.5 * (lo + hi)

            self.regulator_free_prev[regulator] = x
            return x

    def _get_input_value(self, name: str, t: float) -> float:
        default_val = self.params.get(f"input_{name}", 0.0)
        if not self.temporal_inputs:
            return default_val
        spec = self.temporal_inputs.get(name)
        if not spec:
            return default_val

        if isinstance(spec, list):
            for stage in spec:
                start = float(stage.get("start", 0.0))
                end = float(stage.get("end", float("inf")))
                if start <= t < end:
                    return float(stage.get("value", default_val))
            return default_val

        if isinstance(spec, dict):
            pattern_type = str(spec.get("type", "")).lower()
            if pattern_type == "step":
                step_t = float(spec.get("time", 0.0))
                val_start = float(spec.get("start_value", 0.0))
                val_end = float(spec.get("end_value", default_val))
                return val_end if t >= step_t else val_start
            elif pattern_type == "pulse":
                start_t = float(spec.get("start_time", 0.0))
                end_t = float(spec.get("end_time", float("inf")))
                val_active = float(spec.get("active_value", default_val))
                val_basal = float(spec.get("basal_value", 0.0))
                return val_active if start_t <= t <= end_t else val_basal
            elif pattern_type == "sine":
                amplitude = float(spec.get("amplitude", 1.0))
                frequency = float(spec.get("frequency", 0.1))
                bias = float(spec.get("bias", default_val))
                return bias + amplitude * math.sin(2.0 * math.pi * frequency * t)

        return default_val

    def rhs(self, _time: float, y: np.ndarray) -> np.ndarray:
        y = np.maximum(y, 0.0)
        n = len(self.dynamic_signals)
        num_op = self.num_operons
        mrna = y[:num_op]
        protein_immature = y[num_op : num_op + n]
        protein_mature = y[num_op + n :]

        protein_tot_env = {}
        for name, sig_type in self.signals.items():
            if sig_type == "input":
                protein_tot_env[name] = self._get_input_value(name, _time)
            else:
                idx = self.signal_idx.get(name)
                protein_tot_env[name] = protein_mature[idx] if idx is not None else 0.0

        protein_env = {}
        copy_number = self.params.get("copy_number", 1.0)
        for name, p_tot in protein_tot_env.items():
            if name in self.regulator_targets:
                p_free = self._solve_free_regulator(
                    name, p_tot, copy_number, self.regulator_targets[name]
                )
                protein_env[name] = p_free

                # Update max retroactivity index for this regulator
                if p_tot > 1e-9:
                    ri = 1.0 - p_free / p_tot
                    self.max_retroactivity[name] = max(self.max_retroactivity[name], ri)
            else:
                protein_env[name] = p_tot

        regulation = np.ones(n)
        promoter_demand = np.zeros(n)

        kd_vec = np.zeros(n)
        hill_vec = np.zeros(n)
        leak_vec = np.zeros(n)

        default_kd = self.params["kd"]
        default_hill = self.params["hill_coefficient"]
        default_leak = self.params["leak_fraction"]

        for idx, name in enumerate(self.dynamic_signals):
            kd_vec[idx] = self.params.get(f"kd_{name}", default_kd)
            hill_vec[idx] = self.params.get(f"hill_coefficient_{name}", default_hill)
            leak_vec[idx] = self.params.get(f"leak_fraction_{name}", default_leak)

        base_demand = self.params.get(
            "promoter_resource_demand", max(1.0, self.params["km_rnap"] * 0.35)
        )
        copy_number = self.params.get("copy_number", 1.0)

        for idx, name in enumerate(self.dynamic_signals):
            gate_type, gate_inputs = self.deps.get(name, (None, []))
            reg_val = 1.0

            kd = max(kd_vec[idx], 1e-9)
            hill = max(hill_vec[idx], 1.0)
            leak = max(0.0, min(1.0, leak_vec[idx]))

            if gate_type and gate_inputs:
                gate_type = gate_type.lower()
                if gate_type == "not" and len(gate_inputs) == 1:
                    rep_p = protein_env.get(gate_inputs[0], 0.0)
                    reg_val = leak + (1.0 - leak) / (1.0 + np.power(rep_p / kd, hill))
                elif gate_type == "nor" and len(gate_inputs) >= 1:
                    for inp in gate_inputs:
                        rep_p = protein_env.get(inp, 0.0)
                        reg_val *= leak + (1.0 - leak) / (
                            1.0 + np.power(rep_p / kd, hill)
                        )
                elif gate_type == "nand" and len(gate_inputs) >= 1:
                    active_frac = 1.0
                    for inp in gate_inputs:
                        rep_p = protein_env.get(inp, 0.0)
                        active_frac *= np.power(rep_p, hill) / (
                            np.power(kd, hill) + np.power(rep_p, hill)
                        )
                    reg_val = 1.0 - (1.0 - leak) * active_frac
                elif gate_type == "and" and len(gate_inputs) >= 1:
                    active_frac = 1.0
                    for inp in gate_inputs:
                        rep_p = protein_env.get(inp, 0.0)
                        active_frac *= np.power(rep_p, hill) / (
                            np.power(kd, hill) + np.power(rep_p, hill)
                        )
                    reg_val = leak + (1.0 - leak) * active_frac
                elif gate_type == "or" and len(gate_inputs) >= 1:
                    inactive_frac = 1.0
                    for inp in gate_inputs:
                        rep_p = protein_env.get(inp, 0.0)
                        inactive_frac *= 1.0 - (
                            np.power(rep_p, hill)
                            / (np.power(kd, hill) + np.power(rep_p, hill))
                        )
                    reg_val = leak + (1.0 - leak) * (1.0 - inactive_frac)
                elif gate_type in ("buf", "bufif1", "bufif0") and len(gate_inputs) == 1:
                    rep_p = protein_env.get(gate_inputs[0], 0.0)
                    reg_val = leak + (1.0 - leak) * (
                        np.power(rep_p, hill)
                        / (np.power(kd, hill) + np.power(rep_p, hill))
                    )
                else:
                    if len(gate_inputs) == 1:
                        rep_p = protein_env.get(gate_inputs[0], 0.0)
                        reg_val = leak + (1.0 - leak) / (
                            1.0 + np.power(rep_p / kd, hill)
                        )
                    else:
                        for inp in gate_inputs:
                            rep_p = protein_env.get(inp, 0.0)
                            reg_val *= leak + (1.0 - leak) / (
                                1.0 + np.power(rep_p / kd, hill)
                            )

            regulation[idx] = reg_val
            promoter_demand[idx] = base_demand * reg_val * copy_number

        promoter_demand_op = np.zeros(num_op)
        for op_idx, op in enumerate(self.operons):
            first_gene = op[0]
            first_idx = self.signal_idx[first_gene]
            promoter_demand_op[op_idx] = promoter_demand[first_idx]

        rnap_demands = promoter_demand_op
        if self.baseline_relative_resources:
            rnap_demands = np.concatenate(
                ([self.host_rnap_background_demand], promoter_demand_op)
            )
        rnap_free, rnap_total_occupancy = self.solver.solve_rnap(
            self.params["rnap_total"], rnap_demands, self.params["km_rnap"]
        )

        translating_mrna = np.zeros(n)
        translation_demand = np.zeros(n)
        for idx, name in enumerate(self.dynamic_signals):
            op_idx, _ = self.gene_to_operon[name]
            translating_mrna[idx] = mrna[op_idx]
            translation_demand[idx] = translating_mrna[
                idx
            ] * self._translation_demand_weight(name)

        ribosome_demands = translating_mrna
        if self.baseline_relative_resources:
            ribosome_demands = np.concatenate(
                ([self.host_ribosome_background_demand], translation_demand)
            )
        ribo_free, ribosome_total_occupancy = self.solver.solve_ribosome(
            self.params["ribosome_total"],
            ribosome_demands,
            self.params["km_ribosome"],
        )

        mu_max = self.params.get("growth_rate_dilution", 0.0004)
        if self.baseline_relative_resources:
            rnap_capacity_fraction = _clamp01(
                rnap_free / max(self.rnap_baseline_free, 1e-9)
            )
            ribosome_capacity_fraction = _clamp01(
                ribo_free / max(self.ribosome_baseline_free, 1e-9)
            )
            rnap_factor = _relative_saturation(
                rnap_free,
                self.rnap_baseline_free,
                self.params["km_rnap"],
            )
            ribo_factor = _relative_saturation(
                ribo_free,
                self.ribosome_baseline_free,
                self.params["km_ribosome"],
            )
            rnap_occupancy = 1.0 - rnap_capacity_fraction
            ribo_occupancy = 1.0 - ribosome_capacity_fraction
            relative_growth_rate = ribosome_capacity_fraction
            mu = mu_max * relative_growth_rate
        else:
            rnap_factor = rnap_free / (self.params["km_rnap"] + rnap_free)
            ribo_factor = ribo_free / (self.params["km_ribosome"] + ribo_free)
            rnap_capacity_fraction = rnap_free / max(self.params["rnap_total"], 1e-9)
            ribosome_capacity_fraction = ribo_free / max(
                self.params["ribosome_total"], 1e-9
            )
            rnap_occupancy = rnap_total_occupancy
            ribo_occupancy = ribosome_total_occupancy
            relative_growth_rate = ribosome_capacity_fraction
            mu = mu_max * relative_growth_rate
        k_mat = self.params.get("maturation_rate", 0.0011)

        default_mrna_deg = self.params["mrna_degradation_rate"]
        default_protein_deg = self.params["protein_degradation_rate"]

        d_mrna = np.zeros(num_op)
        for op_idx, op in enumerate(self.operons):
            first_gene = op[0]
            first_idx = self.signal_idx[first_gene]

            mrna_deg_val = self.params.get(
                f"mrna_degradation_rate_{first_gene}", default_mrna_deg
            )
            trans_rate_val = self.params.get(
                f"transcription_rate_{first_gene}", self.params["transcription_rate"]
            )

            d_mrna[op_idx] = (
                trans_rate_val * copy_number * regulation[first_idx] * rnap_factor
                - (mrna_deg_val + mu) * mrna[op_idx]
            )

        translation_rates = np.zeros(n)
        for op_idx, op in enumerate(self.operons):
            for pos, gene in enumerate(op):
                gene_idx = self.signal_idx[gene]
                rbs_basal = self.params.get(
                    f"translation_rate_{gene}", self.params["translation_rate"]
                )

                if pos == 0:
                    rate = rbs_basal
                else:
                    up_gene = op[pos - 1]
                    up_idx = self.signal_idx[up_gene]
                    flux_up = translation_rates[up_idx]

                    spacing = self.params.get(f"intergenic_spacing_{gene}", -4.0)
                    if spacing <= 20.0:
                        chi = 0.8
                    elif spacing < 30.0:
                        chi = 0.8 * math.exp(-0.3 * (spacing - 20.0))
                    else:
                        chi = 0.0

                    rate = rbs_basal + chi * flux_up

                lambda_val = self.params.get("polarity_decay_constant", 0.1)
                rate_effective = rate * math.exp(-lambda_val * pos)
                translation_rates[gene_idx] = rate_effective

        protein_deg = np.zeros(n)
        for idx, name in enumerate(self.dynamic_signals):
            protein_deg[idx] = self.params.get(
                f"protein_degradation_rate_{name}", default_protein_deg
            )

        d_protein_immature = np.zeros(n)
        d_protein_mature = np.zeros(n)
        for idx, name in enumerate(self.dynamic_signals):
            op_idx, _ = self.gene_to_operon[name]
            d_protein_immature[idx] = (
                translation_rates[idx] * mrna[op_idx] * ribo_factor
                - (protein_deg[idx] + k_mat + mu) * protein_immature[idx]
            )
            d_protein_mature[idx] = (
                k_mat * protein_immature[idx]
                - (protein_deg[idx] + mu) * protein_mature[idx]
            )

        self.resource_trace.append(
            {
                "rnap_free": rnap_free,
                "ribosome_free": ribo_free,
                "rnap_occupancy": rnap_occupancy,
                "ribosome_occupancy": ribo_occupancy,
                "rnap_total_occupancy": rnap_total_occupancy,
                "ribosome_total_occupancy": ribosome_total_occupancy,
                "rnap_capacity_fraction": rnap_capacity_fraction,
                "ribosome_capacity_fraction": ribosome_capacity_fraction,
                "relative_growth_rate": relative_growth_rate,
                "capacity_loss_fraction": max(
                    1.0 - rnap_capacity_fraction,
                    1.0 - ribosome_capacity_fraction,
                ),
                "transcriptional_demand_index": float(
                    np.sum(promoter_demand_op) / max(self.rnap_baseline_free, 1e-9)
                ),
                "translational_demand_index": float(
                    np.sum(translation_demand) / max(self.ribosome_baseline_free, 1e-9)
                ),
                "burden_nM": float(
                    np.sum(mrna) + np.sum(protein_immature) + np.sum(protein_mature)
                ),
            }
        )
        return np.concatenate([d_mrna, d_protein_immature, d_protein_mature])


class BatchODESimulator:
    def __init__(
        self,
        simulation_time: float = 600.0,
        sample_count: int = 80,
        monte_carlo_samples: int = 1,
        noise_fraction: float = 0.15,
        noise_level: float | None = None,
        cache_dir: str | None = None,
        random_seed: int | None = None,
        temporal_inputs: dict[str, Any] | None = None,
    ):
        self.simulation_time = simulation_time
        self.sample_count = sample_count
        self.monte_carlo_samples = max(1, int(monte_carlo_samples))
        selected_noise = noise_fraction if noise_level is None else noise_level
        self.noise_fraction = max(0.0, float(selected_noise))
        self.random_seed = random_seed
        self._memory = (
            Memory(cache_dir, verbose=0) if cache_dir and Memory is not None else None
        )
        self._local_cache: dict[tuple, dict[str, Any]] = {}
        self.temporal_inputs = temporal_inputs

    def run(self, state: DesignState) -> DesignState:
        node = (
            state.tree_nodes.get(state.current_node_id)
            if state.current_node_id
            else None
        )
        topologies = node.candidate_topologies if node else state.candidate_topologies
        for index, topology in enumerate(topologies):
            self._simulate_topology(topology, index)
        if node:
            node.candidate_topologies = topologies
        state.candidate_topologies = topologies
        return state

    def simulate_topology(self, topology: dict[str, Any]) -> dict[str, Any]:
        selected = deepcopy(topology)
        self._simulate_topology(selected, 0)
        return selected

    def _simulate_topology(self, topology: dict[str, Any], index: int) -> None:
        self.current_operons = topology.get("operons")
        verilog = str(topology.get("verilog") or "")
        signals, deps = parse_verilog_netlist(verilog)
        dynamic_signals = [
            name
            for name in sorted(signals.keys())
            if signals[name] in ("wire", "output")
        ]
        gene_count = len(dynamic_signals)

        if gene_count == 0:
            fallback_count = _infer_gene_count(topology)
            signals = {f"G_{i}": "wire" for i in range(fallback_count)}
            signals["G_0"] = "input"
            signals[f"G_{fallback_count - 1}"] = "output"
            deps = {f"G_{i}": ("not", [f"G_{i - 1}"]) for i in range(1, fallback_count)}
            dynamic_signals = [
                name
                for name in sorted(signals.keys())
                if signals[name] in ("wire", "output")
            ]
            gene_count = len(dynamic_signals)

        output_signals = [
            name for name in sorted(signals.keys()) if signals[name] == "output"
        ]
        target_output = (
            output_signals[0]
            if output_signals
            else (dynamic_signals[-1] if dynamic_signals else None)
        )
        target_idx = (
            dynamic_signals.index(target_output)
            if target_output in dynamic_signals
            else -1
        )
        input_signals = [
            name for name in sorted(signals.keys()) if signals[name] == "input"
        ]
        spec = simulation_spec_from_topology(
            topology,
            simulation_time=self.simulation_time,
            sample_count=self.sample_count,
            monte_carlo_samples=self.monte_carlo_samples,
            noise_fraction=self.noise_fraction,
            input_signals=input_signals,
            target_output=target_output,
            random_seed=self.random_seed,
            temporal_inputs=self.temporal_inputs,
        )

        truth_table = (
            topology.get("truth_table")
            or topology.get("truth_table_or_logic_matrix")
            or topology.get("logic_matrix")
            or []
        )
        if not isinstance(truth_table, list) or len(truth_table) == 0:
            default_row = {inp: 1 for inp in input_signals}
            default_row["Y"] = 1
            truth_table = [default_row]

        biokinetic_parameters = topology.get("biokinetic_parameters", {})
        params = _flatten_parameters(biokinetic_parameters)
        params["copy_number"] = spec.copy_number
        resource_model_contract = _configure_resource_model(
            topology,
            dynamic_signals,
            params,
        )
        parameter_provenance = _parameter_provenance(biokinetic_parameters)
        params["promoter_resource_demand"] = max(1.0, params["km_rnap"] * 0.35)
        physical_assignment_metrics = _physical_assignment_metrics(topology)

        operon_errors = validate_operon_configuration(
            topology.get("operons"),
            dynamic_signals,
        )
        if operon_errors:
            error_msg = "Invalid operon configuration: " + " ".join(operon_errors)
            simulation_result = SimulationResult(
                status="failed",
                configuration_hash=spec.configuration_hash,
                parameter_set_hash=spec.parameter_set_hash,
                scenario_set_hash=spec.scenario_set_hash,
                error=error_msg,
                solver={"methods": spec.solver_methods},
            )
            topology.update(
                {
                    "ode_status": "failed",
                    "kinetic_score": 0.0,
                    "robustness_score": 0.0,
                    "signal_to_noise_ratio": 0.0,
                    "monte_carlo_runs": self.monte_carlo_samples,
                    "score": 0.0,
                    "error": error_msg,
                    "warnings": operon_errors,
                    "simulation_model_version": SIMULATION_MODEL_VERSION,
                    "simulation_spec": spec.to_dict(),
                    "simulation_result": simulation_result.to_dict(),
                    "benchmark_report": {
                        "score": 0.0,
                        "robustness_score": 0.0,
                        "signal_to_noise_ratio": 0.0,
                        "monte_carlo_runs": self.monte_carlo_samples,
                        **physical_assignment_metrics,
                        "details": [
                            {
                                "metric": "kinetic",
                                "status": "input_validation_failed",
                                "error": error_msg,
                            }
                        ],
                    },
                }
            )
            return

        cache_key = _cache_key(
            topology,
            index,
            gene_count,
            params,
            self.simulation_time,
            self.sample_count,
            self.monte_carlo_samples,
            self.noise_fraction,
            spec.configuration_hash,
        )
        cached = self._get_cached(cache_key)
        if cached is not None:
            topology.update(deepcopy(cached))
            return

        t_eval = np.linspace(0.0, self.simulation_time, self.sample_count)
        all_row_metrics = []
        on_values = []
        off_values = []
        best_result = None
        best_resource_trace = None
        best_final_val = -1.0
        success = True
        error_msg = ""

        first_row = truth_table[0] if truth_table else {}
        output_key = None
        for key in ("Y", "OUT", "OUTPUT", "Z", "output", "out"):
            if key in first_row:
                output_key = key
                break
        if not output_key:
            other_keys = [k for k in first_row.keys() if k not in input_signals]
            output_key = other_keys[0] if other_keys else None

        overall_max_retroactivity = {}

        for row in truth_table:
            row_params = params.copy()
            for inp in input_signals:
                val = row.get(inp, row.get(inp.upper(), row.get(inp.lower(), 1)))
                is_high = False
                if isinstance(val, str):
                    is_high = val.strip().lower() in ("1", "true", "yes", "high", "on")
                else:
                    is_high = bool(val)
                row_params[f"input_{inp}"] = 200.0 if is_high else 0.0

            simulation, result = self._run_single_simulation(
                signals, deps, row_params, t_eval
            )
            if result is None or not result.success:
                success = False
                error_msg = getattr(result, "message", "ODE simulation failed.")
                break

            for reg, val in getattr(simulation, "max_retroactivity", {}).items():
                overall_max_retroactivity[reg] = max(
                    overall_max_retroactivity.get(reg, 0.0), val
                )

            row_metrics = _simulation_metrics(
                result.y, simulation.resource_trace, row_params, target_idx, gene_count
            )
            all_row_metrics.append(row_metrics)

            final_val = (
                float(result.y[result.y.shape[0] - gene_count + target_idx, -1])
                if target_idx != -1
                else 0.0
            )
            expected_out = (
                parse_logic_value(row[output_key], True)
                if output_key is not None
                else True
            )
            if expected_out:
                on_values.append(final_val)
            else:
                off_values.append(final_val)

            if final_val > best_final_val or best_result is None:
                best_final_val = final_val
                best_result = result
                best_resource_trace = simulation.resource_trace

        if not success or best_result is None:
            simulation_result = SimulationResult(
                status="failed",
                configuration_hash=spec.configuration_hash,
                parameter_set_hash=spec.parameter_set_hash,
                scenario_set_hash=spec.scenario_set_hash,
                error=error_msg,
                solver={"methods": spec.solver_methods},
            )
            updates = {
                "ode_status": "failed",
                "kinetic_score": 0.0,
                "robustness_score": 0.0,
                "signal_to_noise_ratio": 0.0,
                "monte_carlo_runs": self.monte_carlo_samples,
                "score": 0.0,
                "simulation_model_version": SIMULATION_MODEL_VERSION,
                "simulation_spec": spec.to_dict(),
                "simulation_result": simulation_result.to_dict(),
                "benchmark_report": {
                    "score": 0.0,
                    "robustness_score": 0.0,
                    "signal_to_noise_ratio": 0.0,
                    "monte_carlo_runs": self.monte_carlo_samples,
                    **physical_assignment_metrics,
                    "details": [
                        {
                            "metric": "kinetic",
                            "status": "ode_failed",
                            "error": error_msg,
                        }
                    ],
                },
            }
            topology.update(updates)
            self._set_cached(cache_key, updates)
            return

        max_burden = max(m["max_burden_nM"] for m in all_row_metrics)
        max_rnap_occ = max(m["rnap_occupancy_max"] for m in all_row_metrics)
        max_ribo_occ = max(m["ribosome_occupancy_max"] for m in all_row_metrics)
        min_rnap_free = min(m["rnap_free_min"] for m in all_row_metrics)
        min_ribo_free = min(m["ribosome_free_min"] for m in all_row_metrics)
        min_relative_growth = min(
            m["relative_growth_rate_min"] for m in all_row_metrics
        )
        max_capacity_loss = max(
            m["capacity_loss_fraction_max"] for m in all_row_metrics
        )
        max_tx_demand = max(
            m["transcriptional_demand_index_max"] for m in all_row_metrics
        )
        max_tl_demand = max(
            m["translational_demand_index_max"] for m in all_row_metrics
        )
        max_rnap_total_occ = max(m["rnap_total_occupancy_max"] for m in all_row_metrics)
        max_ribo_total_occ = max(
            m["ribosome_total_occupancy_max"] for m in all_row_metrics
        )
        avg_cv = float(np.mean([m["output_cv"] for m in all_row_metrics]))
        avg_snr = float(np.mean([m["signal_to_noise_ratio"] for m in all_row_metrics]))

        min_on = min(on_values) if on_values else 0.0
        max_off = max(off_values) if off_values else 0.0
        dynamic_margin = min_on - max_off

        # Calculate Stoichiometric Balance Score
        regulator_values = []
        for name in dynamic_signals:
            if name in simulation.regulator_targets:
                idx = simulation.signal_idx[name]
                final_val = float(
                    best_result.y[best_result.y.shape[0] - gene_count + idx, -1]
                )
                regulator_values.append(final_val)

        if len(regulator_values) > 1:
            mean_val = float(np.mean(regulator_values))
            std_val = float(np.std(regulator_values))
            if mean_val > 1e-9:
                cv = std_val / mean_val
                stoichiometry_score = float(np.exp(-cv))
            else:
                stoichiometry_score = 0.0
        else:
            stoichiometry_score = 1.0

        metrics = {
            "max_burden_nM": max_burden,
            "output_cv": avg_cv,
            "signal_to_noise_ratio": avg_snr,
            "dynamic_margin": dynamic_margin,
            "rnap_occupancy_max": max_rnap_occ,
            "ribosome_occupancy_max": max_ribo_occ,
            "rnap_free_min": min_rnap_free,
            "ribosome_free_min": min_ribo_free,
            "relative_growth_rate_min": min_relative_growth,
            "capacity_loss_fraction_max": max_capacity_loss,
            "transcriptional_demand_index_max": max_tx_demand,
            "translational_demand_index_max": max_tl_demand,
            "rnap_total_occupancy_max": max_rnap_total_occ,
            "ribosome_total_occupancy_max": max_ribo_total_occ,
            "host_rnap_baseline_free_fraction": params.get(
                "host_rnap_baseline_free_fraction", 1.0
            ),
            "host_ribosome_baseline_free_fraction": params.get(
                "host_ribosome_baseline_free_fraction", 1.0
            ),
            "stoichiometry_score": stoichiometry_score,
            "resource_capacity_factor": min(
                1.0,
                0.5
                * params["rnap_total"]
                / DEFAULT_BIOKINETIC_PARAMETERS["rnap_total"]["value"]
                + 0.5
                * params["ribosome_total"]
                / DEFAULT_BIOKINETIC_PARAMETERS["ribosome_total"]["value"],
            ),
            "burden_penalty": _sigmoid_penalty(
                max_burden, params["burden_soft_limit"], 0.00018
            ),
            "toxicity_penalty": _sigmoid_penalty(
                max_burden, params["toxicity_threshold"], 0.00022
            ),
        }

        ode_trace = _simulation_trace(
            t_eval, best_result.y, best_resource_trace, target_idx, gene_count
        )
        if self.monte_carlo_samples > 1:
            metrics.update(
                self._monte_carlo_metrics(
                    signals,
                    deps,
                    params,
                    t_eval,
                    target_idx,
                    input_signals,
                    truth_table,
                )
            )

        kinetic_score = _kinetic_score(metrics)
        robustness_score = kinetic_score
        base_score = float(topology.get("score", 0.65 + index * 0.02))
        final_score = max(0.0, min(1.0, 0.35 * base_score + 0.65 * kinetic_score))

        resource_occupancy = {
            "rnap_max": metrics["rnap_occupancy_max"],
            "ribosome_max": metrics["ribosome_occupancy_max"],
            "rnap_free_min": metrics["rnap_free_min"],
            "ribosome_free_min": metrics["ribosome_free_min"],
            "rnap_total_max": metrics["rnap_total_occupancy_max"],
            "ribosome_total_max": metrics["ribosome_total_occupancy_max"],
        }

        baseline_relative_summary = _baseline_relative_summary(
            metrics,
            resource_model_contract,
        )
        resource_model_comparison = {
            "active_mode": resource_model_contract["mode"],
            "legacy_outputs": {
                "status": "retained",
                "resource_occupancy": resource_occupancy,
            },
            BASELINE_RELATIVE_RESOURCE_MODEL_MODE: (
                baseline_relative_summary
                if resource_model_contract["enabled"]
                else {"status": "available_not_applied"}
            ),
        }

        details = [
            {"metric": "kinetic", "score": kinetic_score},
            {"metric": "robustness", "score": robustness_score},
            {
                "metric": "signal_to_noise_ratio",
                "value": metrics["signal_to_noise_ratio"],
            },
            {"metric": "max_burden", "value": metrics["max_burden_nM"], "unit": "nM"},
            {"metric": "output_cv", "value": metrics["output_cv"]},
            {"metric": "stoichiometry_score", "value": stoichiometry_score},
            {"metric": "resource_occupancy", "value": resource_occupancy},
            {"metric": "parameter_provenance", "value": parameter_provenance},
            {
                "metric": "resource_model_comparison",
                "value": resource_model_comparison,
            },
        ]
        if self.monte_carlo_samples > 1:
            details.append(
                {
                    "metric": "monte_carlo",
                    "samples": self.monte_carlo_samples,
                    "noise_fraction": self.noise_fraction,
                    "terminal_output_cv": metrics["monte_carlo_terminal_output_cv"],
                    "failure_rate": metrics["monte_carlo_failure_rate"],
                }
            )

        simulation_warnings = list(resource_model_contract["warnings"])
        for reg, val in overall_max_retroactivity.items():
            if val > 0.3:
                simulation_warnings.append(
                    f"Retroactivity warning: regulator {reg} has high load sequestration "
                    f"(max Ri = {val:.2f}). This may cause signal delay or loss of memory state."
                )

        rbs_warnings = []
        rbs_seqs = topology.get("rbs_sequences") or {}
        from tools.tool_adapters import RNAFoldingAdapter

        folding_adapter = RNAFoldingAdapter()
        for gene, seq in rbs_seqs.items():
            if gene in dynamic_signals:
                result_fold = folding_adapter.run({"sequence": seq})
                mfe = result_fold.output.get("mfe", 0.0)
                if mfe < -8.0:
                    op_idx, pos = simulation.gene_to_operon.get(gene, (None, None))
                    is_blocked = False
                    if pos == 0 or pos is None:
                        is_blocked = True
                    else:
                        up_gene = simulation.operons[op_idx][pos - 1]
                        up_rate = params.get(
                            f"translation_rate_{up_gene}",
                            params.get("translation_rate", 1.0),
                        )
                        if up_rate < 0.5:
                            is_blocked = True
                    if is_blocked:
                        rbs_warnings.append(
                            f"RBS blocking warning: gene {gene} has low RBS accessibility "
                            f"(MFE = {mfe:.2f} kcal/mol) and insufficient upstream translation flux."
                        )
        simulation_warnings.extend(rbs_warnings)

        simulation_result = SimulationResult(
            status="simulated",
            configuration_hash=spec.configuration_hash,
            parameter_set_hash=spec.parameter_set_hash,
            scenario_set_hash=spec.scenario_set_hash,
            metrics=metrics,
            solver={"methods": spec.solver_methods},
            warnings=simulation_warnings,
        )
        max_ri = (
            max(overall_max_retroactivity.values())
            if overall_max_retroactivity
            else 0.0
        )
        updates = {
            "ode_status": "simulated",
            "gene_count": gene_count,
            "kinetic_score": kinetic_score,
            "robustness_score": robustness_score,
            "signal_to_noise_ratio": metrics["signal_to_noise_ratio"],
            "monte_carlo_runs": self.monte_carlo_samples,
            "score": final_score,
            "metrics_max_burden": metrics["max_burden_nM"],
            "metrics_cv": metrics["output_cv"],
            "dynamic_margin": metrics["dynamic_margin"],
            "ode_trace": ode_trace,
            "resource_occupancy": resource_occupancy,
            "resource_model_mode": resource_model_contract["mode"],
            "resource_model_comparison": resource_model_comparison,
            "parameter_provenance": parameter_provenance,
            "warnings": simulation_warnings,
            "stoichiometry_score": stoichiometry_score,
            "retroactivity_max": max_ri,
            "retroactivity_score": 1.0 - max_ri,
            "rbs_blocking_detected": True if rbs_warnings else False,
            "monte_carlo_samples": self.monte_carlo_samples,
            "monte_carlo_noise_fraction": self.noise_fraction,
            "simulation_model_version": SIMULATION_MODEL_VERSION,
            "simulation_spec": spec.to_dict(),
            "simulation_result": simulation_result.to_dict(),
            "benchmark_report": {
                "score": final_score,
                "robustness_score": robustness_score,
                "signal_to_noise_ratio": metrics["signal_to_noise_ratio"],
                "monte_carlo_runs": self.monte_carlo_samples,
                "stoichiometry_score": stoichiometry_score,
                "retroactivity_max": max_ri,
                "retroactivity_score": 1.0 - max_ri,
                "rbs_blocking_detected": True if rbs_warnings else False,
                **physical_assignment_metrics,
                "details": details,
            },
        }
        if resource_model_contract["enabled"]:
            updates["baseline_relative_resources"] = baseline_relative_summary
        if self.monte_carlo_samples > 1:
            updates["monte_carlo_failure_rate"] = metrics["monte_carlo_failure_rate"]
            updates["monte_carlo_terminal_output_cv"] = metrics[
                "monte_carlo_terminal_output_cv"
            ]
        topology.update(updates)
        self._set_cached(cache_key, updates)

    def _run_single_simulation(
        self,
        signals: dict[str, str],
        deps: dict[str, tuple[str, list[str]]],
        params: dict[str, float],
        t_eval: np.ndarray,
        initial_state: np.ndarray | None = None,
    ) -> tuple[ResourceAwareSimulation, Any]:
        solver = WarmStartResourceSolver(
            rnap_free=params["rnap_total"],
            ribosome_free=params["ribosome_total"],
        )
        simulation = ResourceAwareSimulation(
            signals=signals,
            deps=deps,
            params=params,
            solver=solver,
            temporal_inputs=getattr(self, "temporal_inputs", None),
            operons_config=getattr(self, "current_operons", None),
        )
        gene_count = len(simulation.dynamic_signals)
        if initial_state is None:
            initial_state = np.zeros(simulation.num_operons + gene_count * 2)
        return simulation, self._integrate(simulation, initial_state, t_eval)

    def simulate_noisy_response(
        self,
        topology: dict[str, Any],
        noise_level: float | None = None,
        rng: np.random.Generator | None = None,
    ) -> dict[str, float | bool | str]:
        self.current_operons = topology.get("operons")
        verilog = str(topology.get("verilog") or "")
        signals, deps = parse_verilog_netlist(verilog)
        dynamic_signals = [
            name
            for name in sorted(signals.keys())
            if signals[name] in ("wire", "output")
        ]
        gene_count = len(dynamic_signals)

        if gene_count == 0:
            fallback_count = _infer_gene_count(topology)
            signals = {f"G_{i}": "wire" for i in range(fallback_count)}
            signals["G_0"] = "input"
            signals[f"G_{fallback_count - 1}"] = "output"
            deps = {f"G_{i}": ("not", [f"G_{i - 1}"]) for i in range(1, fallback_count)}
            dynamic_signals = [
                name
                for name in sorted(signals.keys())
                if signals[name] in ("wire", "output")
            ]
            gene_count = len(dynamic_signals)

        output_signals = [
            name for name in sorted(signals.keys()) if signals[name] == "output"
        ]
        target_output = (
            output_signals[0]
            if output_signals
            else (dynamic_signals[-1] if dynamic_signals else None)
        )
        target_idx = (
            dynamic_signals.index(target_output)
            if target_output in dynamic_signals
            else -1
        )
        input_signals = [
            name for name in sorted(signals.keys()) if signals[name] == "input"
        ]

        truth_table = (
            topology.get("truth_table")
            or topology.get("truth_table_or_logic_matrix")
            or topology.get("logic_matrix")
            or []
        )
        if not isinstance(truth_table, list) or len(truth_table) == 0:
            default_row = {inp: 1 for inp in input_signals}
            default_row["Y"] = 1
            truth_table = [default_row]

        params = _flatten_parameters(topology.get("biokinetic_parameters", {}))
        params["copy_number"] = float(topology.get("copy_number", 1.0))
        _configure_resource_model(topology, dynamic_signals, params)
        params["promoter_resource_demand"] = max(1.0, params["km_rnap"] * 0.35)

        sample_params = _perturb_biokinetic_parameters(
            params,
            self.noise_fraction
            if noise_level is None
            else max(0.0, float(noise_level)),
            rng or np.random.default_rng(),
        )

        on_values = []
        off_values = []
        first_row = truth_table[0] if truth_table else {}
        output_key = None
        for key in ("Y", "OUT", "OUTPUT", "Z", "output", "out"):
            if key in first_row:
                output_key = key
                break
        if not output_key:
            other_keys = [k for k in first_row.keys() if k not in input_signals]
            output_key = other_keys[0] if other_keys else None

        t_eval = np.linspace(0.0, self.simulation_time, self.sample_count)
        success = True
        error_msg = ""

        for row in truth_table:
            row_params = sample_params.copy()
            for inp in input_signals:
                val = row.get(inp, row.get(inp.upper(), row.get(inp.lower(), 1)))
                is_high = False
                if isinstance(val, str):
                    is_high = val.strip().lower() in ("1", "true", "yes", "high", "on")
                else:
                    is_high = bool(val)
                row_params[f"input_{inp}"] = 200.0 if is_high else 0.0

            simulation, result = self._run_single_simulation(
                signals, deps, row_params, t_eval
            )
            if result is None or not result.success:
                success = False
                error_msg = getattr(result, "message", "ODE simulation failed.")
                break

            final_val = (
                float(result.y[result.y.shape[0] - gene_count + target_idx, -1])
                if target_idx != -1
                else 0.0
            )
            expected_out = (
                parse_logic_value(row[output_key], True)
                if output_key is not None
                else True
            )
            if expected_out:
                on_values.append(final_val)
            else:
                off_values.append(final_val)

        if not success:
            return {
                "success": False,
                "on_value": 0.0,
                "off_value": float("inf"),
                "error": error_msg,
            }

        min_on = min(on_values) if on_values else 0.0
        max_off = max(off_values) if off_values else 0.0

        return {
            "success": True,
            "on_value": min_on,
            "off_value": max_off,
            "signal_to_noise_ratio": min_on / max(max_off, 1e-9),
        }

    def _monte_carlo_metrics(
        self,
        signals: dict[str, str],
        deps: dict[str, tuple[str, list[str]]],
        params: dict[str, float],
        t_eval: np.ndarray,
        target_idx: int,
        input_signals: list[str],
        truth_table: list[dict[str, Any]],
    ) -> dict[str, float]:
        gene_count = len([s for s in signals if signals[s] in ("wire", "output")])
        rng = np.random.default_rng(
            self.random_seed
            if self.random_seed is not None
            else _stable_seed(
                gene_count, params, self.simulation_time, self.sample_count
            )
        )
        terminal_outputs: list[float] = []
        failures = 0
        perturbable = [
            "transcription_rate",
            "translation_rate",
            "kd",
            "hill_coefficient",
            "leak_fraction",
            "mrna_degradation_rate",
            "protein_degradation_rate",
            "y_min",
            "ymax",
            "y_max",
            "copy_number",
        ]

        first_row = truth_table[0] if truth_table else {}
        output_key = None
        for key in ("Y", "OUT", "OUTPUT", "Z", "output", "out"):
            if key in first_row:
                output_key = key
                break
        if not output_key:
            other_keys = [k for k in first_row.keys() if k not in input_signals]
            output_key = other_keys[0] if other_keys else None

        for _ in range(self.monte_carlo_samples):
            sample_params = _perturb_biokinetic_parameters(
                params,
                self.noise_fraction,
                rng,
                perturbable,
            )

            row_outputs = []
            row_expected = []

            for row in truth_table:
                row_params = sample_params.copy()
                for inp in input_signals:
                    val = row.get(inp, row.get(inp.upper(), row.get(inp.lower(), 1)))
                    is_high = False
                    if isinstance(val, str):
                        is_high = val.strip().lower() in (
                            "1",
                            "true",
                            "yes",
                            "high",
                            "on",
                        )
                    else:
                        is_high = bool(val)
                    row_params[f"input_{inp}"] = 200.0 if is_high else 0.0

                simulation, result = self._run_single_simulation(
                    signals, deps, row_params, t_eval
                )
                if result is None or not result.success:
                    continue

                final_val = (
                    float(result.y[result.y.shape[0] - gene_count + target_idx, -1])
                    if target_idx != -1
                    else 0.0
                )
                row_outputs.append(final_val)
                expected_out = (
                    parse_logic_value(row[output_key], True)
                    if output_key is not None
                    else True
                )
                row_expected.append(expected_out)

            if len(row_outputs) < len(truth_table):
                failures += 1
                continue

            ons = [v for v, exp in zip(row_outputs, row_expected) if exp]
            offs = [v for v, exp in zip(row_outputs, row_expected) if not exp]

            if ons and offs:
                if min(ons) <= max(offs):
                    failures += 1

            terminal_outputs.append(row_outputs[-1] if row_outputs else 0.0)

        mean_output = float(np.mean(terminal_outputs)) if terminal_outputs else 0.0
        cv = (
            float(np.std(terminal_outputs)) / max(mean_output, 1e-9)
            if terminal_outputs
            else 1.0
        )
        return {
            "monte_carlo_terminal_output_cv": cv,
            "monte_carlo_failure_rate": failures / max(1, self.monte_carlo_samples),
        }

    def simulate_stochastic(
        self,
        topology: dict[str, Any],
        runs: int = 50,
        scale_factor: float = 10.0,
        max_steps: int = 15000,
    ) -> dict[str, Any]:
        if not isinstance(topology, dict) or not topology:
            raise ValueError(
                "Stochastic simulation requires a non-empty topology dictionary."
            )
        if (
            str(topology.get("resource_model_mode") or LEGACY_RESOURCE_MODEL_MODE)
            == BASELINE_RELATIVE_RESOURCE_MODEL_MODE
        ):
            raise ValueError(
                "baseline_relative_v0.1 is currently supported by the ODE path only; "
                "stochastic resource propensities remain a future calibration slice."
            )
        if isinstance(runs, bool) or not isinstance(runs, int) or runs <= 0:
            raise ValueError("runs must be a positive integer.")
        if isinstance(scale_factor, bool):
            raise ValueError("scale_factor must be a finite number greater than zero.")
        scale_factor = float(scale_factor)
        if not math.isfinite(scale_factor) or scale_factor <= 0.0:
            raise ValueError("scale_factor must be a finite number greater than zero.")
        if (
            isinstance(max_steps, bool)
            or not isinstance(max_steps, int)
            or max_steps <= 0
        ):
            raise ValueError("max_steps must be a positive integer.")

        # 1. Parse Verilog and set up simulation object
        verilog = str(topology.get("verilog") or "")
        signals, deps = parse_verilog_netlist(verilog)
        dynamic_signals = [
            name
            for name in sorted(signals.keys())
            if signals[name] in ("wire", "output")
        ]
        gene_count = len(dynamic_signals)

        if gene_count == 0:
            raise ValueError(
                "topology.verilog must define at least one wire or output signal."
            )

        operon_errors = validate_operon_configuration(
            topology.get("operons"),
            dynamic_signals,
        )
        if operon_errors:
            raise ValueError("Invalid operon configuration: " + " ".join(operon_errors))

        biokinetic_parameters = topology.get("biokinetic_parameters", {})
        params = _flatten_parameters(biokinetic_parameters)
        params["copy_number"] = float(topology.get("copy_number", 1.0))
        params["promoter_resource_demand"] = max(1.0, params["km_rnap"] * 0.35)

        solver = WarmStartResourceSolver(
            rnap_free=params["rnap_total"],
            ribosome_free=params["ribosome_total"],
        )

        # Set up a ResourceAwareSimulation instance
        simulation = ResourceAwareSimulation(
            signals=signals,
            deps=deps,
            params=params,
            solver=solver,
            temporal_inputs=self.temporal_inputs,
            operons_config=topology.get("operons"),
        )

        num_op = simulation.num_operons

        # Define species list
        species_names = []
        for j in range(num_op):
            op_genes = "-".join(simulation.operons[j])
            species_names.append(f"mRNA_{op_genes}")
        for name in dynamic_signals:
            species_names.append(f"protein_immature_{name}")
        for name in dynamic_signals:
            species_names.append(f"protein_mature_{name}")

        num_species = len(species_names)

        # We want to resample the trajectories onto a common time grid
        t_max = self.simulation_time
        t_eval = np.linspace(0.0, t_max, self.sample_count)

        trajectories = []
        run_statuses = []
        rng = np.random.default_rng(self.random_seed)

        initial_counts = np.zeros(num_species)
        if "initial_molecules" in topology:
            for spec_name, val in topology["initial_molecules"].items():
                if spec_name in species_names:
                    idx = species_names.index(spec_name)
                    initial_counts[idx] = float(val)
        else:
            # Check if there is a way to set sequential logic initial state
            if "Q" in dynamic_signals:
                q_idx = num_op + gene_count + dynamic_signals.index("Q")
                initial_counts[q_idx] = 20.0 * scale_factor  # 20 nM
            if "Qbar" in dynamic_signals:
                qb_idx = num_op + gene_count + dynamic_signals.index("Qbar")
                initial_counts[qb_idx] = 0.0

        # Run stochastic Gillespie simulations
        for run_idx in range(runs):
            state = initial_counts.copy()
            t = 0.0

            # Record trajectory points for interpolation
            trajectory_t = [0.0]
            trajectory_y = [state.copy()]

            # Gillespie SSA Loop
            step = 0
            while t < t_max and step < max_steps:
                # 1. Convert state to concentrations
                mrna_conc = state[:num_op] / scale_factor
                protein_mature_conc = state[num_op + gene_count :] / scale_factor

                # 2. Get input concentrations
                protein_tot_env = {}
                for name, sig_type in simulation.signals.items():
                    if sig_type == "input":
                        protein_tot_env[name] = simulation._get_input_value(name, t)
                    else:
                        idx = simulation.signal_idx.get(name)
                        protein_tot_env[name] = (
                            protein_mature_conc[idx] if idx is not None else 0.0
                        )

                # 3. Solve free regulators
                protein_env = {}
                copy_number = simulation.params.get("copy_number", 1.0)
                for name, p_tot in protein_tot_env.items():
                    if name in simulation.regulator_targets:
                        p_free = simulation._solve_free_regulator(
                            name, p_tot, copy_number, simulation.regulator_targets[name]
                        )
                        protein_env[name] = p_free
                    else:
                        protein_env[name] = p_tot

                # 4. Evaluate promoter regulation
                regulation = np.ones(gene_count)
                promoter_demand = np.zeros(gene_count)

                kd_vec = np.zeros(gene_count)
                hill_vec = np.zeros(gene_count)
                leak_vec = np.zeros(gene_count)
                default_kd = simulation.params["kd"]
                default_hill = simulation.params["hill_coefficient"]
                default_leak = simulation.params["leak_fraction"]

                for idx, name in enumerate(simulation.dynamic_signals):
                    kd_vec[idx] = simulation.params.get(f"kd_{name}", default_kd)
                    hill_vec[idx] = simulation.params.get(
                        f"hill_coefficient_{name}", default_hill
                    )
                    leak_vec[idx] = simulation.params.get(
                        f"leak_fraction_{name}", default_leak
                    )

                base_demand = simulation.params.get(
                    "promoter_resource_demand",
                    max(1.0, simulation.params["km_rnap"] * 0.35),
                )

                for idx, name in enumerate(simulation.dynamic_signals):
                    gate_type, gate_inputs = simulation.deps.get(name, (None, []))
                    reg_val = 1.0
                    kd = max(kd_vec[idx], 1e-9)
                    hill = max(hill_vec[idx], 1.0)
                    leak = max(0.0, min(1.0, leak_vec[idx]))

                    if gate_type and gate_inputs:
                        gate_type = gate_type.lower()
                        if gate_type == "not" and len(gate_inputs) == 1:
                            rep_p = protein_env.get(gate_inputs[0], 0.0)
                            reg_val = leak + (1.0 - leak) / (
                                1.0 + np.power(rep_p / kd, hill)
                            )
                        elif gate_type == "nor" and len(gate_inputs) >= 1:
                            for inp in gate_inputs:
                                rep_p = protein_env.get(inp, 0.0)
                                reg_val *= leak + (1.0 - leak) / (
                                    1.0 + np.power(rep_p / kd, hill)
                                )
                        elif gate_type == "nand" and len(gate_inputs) >= 1:
                            active_frac = 1.0
                            for inp in gate_inputs:
                                rep_p = protein_env.get(inp, 0.0)
                                active_frac *= np.power(rep_p, hill) / (
                                    np.power(kd, hill) + np.power(rep_p, hill)
                                )
                            reg_val = 1.0 - (1.0 - leak) * active_frac
                        elif gate_type == "and" and len(gate_inputs) >= 1:
                            active_frac = 1.0
                            for inp in gate_inputs:
                                rep_p = protein_env.get(inp, 0.0)
                                active_frac *= np.power(rep_p, hill) / (
                                    np.power(kd, hill) + np.power(rep_p, hill)
                                )
                            reg_val = leak + (1.0 - leak) * active_frac
                        elif gate_type == "or" and len(gate_inputs) >= 1:
                            inactive_frac = 1.0
                            for inp in gate_inputs:
                                rep_p = protein_env.get(inp, 0.0)
                                inactive_frac *= 1.0 - (
                                    np.power(rep_p, hill)
                                    / (np.power(kd, hill) + np.power(rep_p, hill))
                                )
                            reg_val = leak + (1.0 - leak) * (1.0 - inactive_frac)
                        elif (
                            gate_type in ("buf", "bufif1", "bufif0")
                            and len(gate_inputs) == 1
                        ):
                            rep_p = protein_env.get(gate_inputs[0], 0.0)
                            reg_val = leak + (1.0 - leak) * (
                                np.power(rep_p, hill)
                                / (np.power(kd, hill) + np.power(rep_p, hill))
                            )
                        else:
                            if len(gate_inputs) == 1:
                                rep_p = protein_env.get(gate_inputs[0], 0.0)
                                reg_val = leak + (1.0 - leak) / (
                                    1.0 + np.power(rep_p / kd, hill)
                                )
                            else:
                                for inp in gate_inputs:
                                    rep_p = protein_env.get(inp, 0.0)
                                    reg_val *= leak + (1.0 - leak) / (
                                        1.0 + np.power(rep_p / kd, hill)
                                    )

                    regulation[idx] = reg_val
                    promoter_demand[idx] = base_demand * reg_val * copy_number

                # 5. Solve resources
                promoter_demand_op = np.zeros(num_op)
                for op_idx, op in enumerate(simulation.operons):
                    first_gene = op[0]
                    first_idx = simulation.signal_idx[first_gene]
                    promoter_demand_op[op_idx] = promoter_demand[first_idx]

                rnap_free, _ = simulation.solver.solve_rnap(
                    simulation.params["rnap_total"],
                    promoter_demand_op,
                    simulation.params["km_rnap"],
                )

                translating_mrna = np.zeros(gene_count)
                for idx, name in enumerate(simulation.dynamic_signals):
                    op_idx, _ = simulation.gene_to_operon[name]
                    translating_mrna[idx] = mrna_conc[op_idx]

                ribo_free, _ = simulation.solver.solve_ribosome(
                    simulation.params["ribosome_total"],
                    translating_mrna,
                    simulation.params["km_ribosome"],
                )

                rnap_factor = rnap_free / (simulation.params["km_rnap"] + rnap_free)
                ribo_factor = ribo_free / (simulation.params["km_ribosome"] + ribo_free)

                mu_max = simulation.params.get("growth_rate_dilution", 0.0004)
                ribo_total = simulation.params.get("ribosome_total", 20000.0)
                mu = mu_max * (ribo_free / max(ribo_total, 1e-9))
                k_mat = simulation.params.get("maturation_rate", 0.0011)

                default_mrna_deg = simulation.params["mrna_degradation_rate"]
                default_protein_deg = simulation.params["protein_degradation_rate"]

                # 6. Calculate translation rates
                translation_rates = np.zeros(gene_count)
                for op_idx, op in enumerate(simulation.operons):
                    for pos, gene in enumerate(op):
                        g_idx = simulation.signal_idx[gene]
                        rbs_basal = simulation.params.get(
                            f"translation_rate_{gene}",
                            simulation.params["translation_rate"],
                        )
                        if pos == 0:
                            rate = rbs_basal
                        else:
                            up_gene = op[pos - 1]
                            up_idx = simulation.signal_idx[up_gene]
                            flux_up = translation_rates[up_idx]
                            spacing = simulation.params.get(
                                f"intergenic_spacing_{gene}", -4.0
                            )
                            if spacing <= 20.0:
                                chi = 0.8
                            elif spacing < 30.0:
                                chi = 0.8 * math.exp(-0.3 * (spacing - 20.0))
                            else:
                                chi = 0.0
                            rate = rbs_basal + chi * flux_up
                        lambda_val = simulation.params.get(
                            "polarity_decay_constant", 0.1
                        )
                        translation_rates[g_idx] = rate * math.exp(-lambda_val * pos)

                protein_deg = np.zeros(gene_count)
                for idx, name in enumerate(simulation.dynamic_signals):
                    protein_deg[idx] = simulation.params.get(
                        f"protein_degradation_rate_{name}", default_protein_deg
                    )

                # 7. Construct Reactions and Propensities list
                propensities = []
                reactions = []

                # Operon reactions
                for op_idx, op in enumerate(simulation.operons):
                    first_gene = op[0]
                    first_idx = simulation.signal_idx[first_gene]
                    mrna_deg_val = simulation.params.get(
                        f"mrna_degradation_rate_{first_gene}", default_mrna_deg
                    )
                    trans_rate_val = simulation.params.get(
                        f"transcription_rate_{first_gene}",
                        simulation.params["transcription_rate"],
                    )

                    # Transcription
                    a_tx = (
                        trans_rate_val
                        * copy_number
                        * regulation[first_idx]
                        * rnap_factor
                        * scale_factor
                    )
                    propensities.append(a_tx)
                    change = np.zeros(num_species)
                    change[op_idx] = 1
                    reactions.append(change)

                    # mRNA Degradation
                    a_deg_m = (mrna_deg_val + mu) * state[op_idx]
                    propensities.append(a_deg_m)
                    change = np.zeros(num_species)
                    change[op_idx] = -1
                    reactions.append(change)

                # Gene reactions
                for idx, name in enumerate(simulation.dynamic_signals):
                    op_idx, _ = simulation.gene_to_operon[name]

                    # Translation
                    a_tl = translation_rates[idx] * state[op_idx] * ribo_factor
                    propensities.append(a_tl)
                    change = np.zeros(num_species)
                    change[num_op + idx] = 1
                    reactions.append(change)

                    # Maturation
                    a_mat = k_mat * state[num_op + idx]
                    propensities.append(a_mat)
                    change = np.zeros(num_species)
                    change[num_op + idx] = -1
                    change[num_op + gene_count + idx] = 1
                    reactions.append(change)

                    # Immature Degradation
                    a_deg_pi = (protein_deg[idx] + mu) * state[num_op + idx]
                    propensities.append(a_deg_pi)
                    change = np.zeros(num_species)
                    change[num_op + idx] = -1
                    reactions.append(change)

                    # Mature Degradation
                    a_deg_pm = (protein_deg[idx] + mu) * state[
                        num_op + gene_count + idx
                    ]
                    propensities.append(a_deg_pm)
                    change = np.zeros(num_species)
                    change[num_op + gene_count + idx] = -1
                    reactions.append(change)

                # 8. Gillespie Step
                prop_sum = sum(propensities)
                if prop_sum <= 1e-12:
                    t = t_max
                    break

                tau = -math.log(rng.random()) / prop_sum
                t += tau
                if t > t_max:
                    break

                r = rng.random() * prop_sum
                accum = 0.0
                fired_idx = 0
                for idx_p, p in enumerate(propensities):
                    accum += p
                    if r <= accum:
                        fired_idx = idx_p
                        break

                state += reactions[fired_idx]
                state = np.maximum(state, 0.0)

                trajectory_t.append(t)
                trajectory_y.append(state.copy())
                step += 1

            truncated = step >= max_steps and t < t_max
            if not truncated and trajectory_t[-1] < t_max:
                trajectory_t.append(t_max)
                trajectory_y.append(trajectory_y[-1].copy())
            run_statuses.append(
                {
                    "run_index": run_idx,
                    "status": "truncated" if truncated else "completed",
                    "steps": step,
                    "simulated_until": min(t, t_max),
                }
            )

            trajectory_y_arr = np.array(trajectory_y)
            resampled_run_concs = np.zeros((num_species, len(t_eval)))
            for s_idx in range(num_species):
                resampled_run_concs[s_idx, :] = (
                    np.interp(t_eval, trajectory_t, trajectory_y_arr[:, s_idx])
                    / scale_factor
                )

            trajectories.append(resampled_run_concs)

        trajectories_arr = np.array(trajectories)
        mean_trajectory_arr = np.mean(trajectories_arr, axis=0)
        var_trajectory_arr = np.var(trajectories_arr, axis=0)

        fano_factors = {}
        for idx, name in enumerate(dynamic_signals):
            mature_idx = num_op + gene_count + idx
            final_mean = float(mean_trajectory_arr[mature_idx, -1])
            final_var = float(var_trajectory_arr[mature_idx, -1])
            fano_factors[name] = final_var / max(final_mean, 1e-9)

        memory_stability = 1.0
        if "Q" in dynamic_signals and "Qbar" in dynamic_signals:
            q_idx = num_op + gene_count + dynamic_signals.index("Q")
            qb_idx = num_op + gene_count + dynamic_signals.index("Qbar")

            stable_runs = 0
            for run_idx in range(runs):
                q_trace = trajectories_arr[run_idx, q_idx, :]
                qb_trace = trajectories_arr[run_idx, qb_idx, :]
                if np.all(q_trace >= qb_trace):
                    stable_runs += 1
            memory_stability = stable_runs / runs

        switching_failure_probability = 0.0

        runs_output = []
        for run_idx in range(min(5, runs)):
            run_data = {"time": t_eval.tolist()}
            for s_idx, spec_name in enumerate(species_names):
                run_data[spec_name] = trajectories_arr[run_idx, s_idx, :].tolist()
            runs_output.append(run_data)

        mean_trajectory = {"time": t_eval.tolist()}
        for s_idx, spec_name in enumerate(species_names):
            mean_trajectory[spec_name] = mean_trajectory_arr[s_idx, :].tolist()

        truncated_run_count = sum(
            status["status"] == "truncated" for status in run_statuses
        )

        return {
            "simulation_status": "truncated" if truncated_run_count else "completed",
            "runs": runs_output,
            "mean_trajectory": mean_trajectory,
            "fano_factors": fano_factors,
            "memory_stability": memory_stability,
            "switching_failure_probability": switching_failure_probability,
            "species_names": species_names,
            "completed_run_count": runs - truncated_run_count,
            "truncated_run_count": truncated_run_count,
            "max_steps_per_run": max_steps,
            "run_statuses": run_statuses,
            "random_seed": self.random_seed,
            "simulation_time": self.simulation_time,
            "sample_count": self.sample_count,
            "temporal_inputs": self.temporal_inputs,
        }

    def _integrate(
        self,
        simulation: ResourceAwareSimulation,
        initial_state: np.ndarray,
        t_eval: np.ndarray,
    ) -> Any:
        if solve_ivp is not None:
            for method in ("BDF", "Radau"):
                try:
                    result = solve_ivp(
                        simulation.rhs,
                        (0.0, self.simulation_time),
                        initial_state,
                        method=method,
                        t_eval=t_eval,
                        rtol=1e-5,
                        atol=1e-8,
                    )
                except Exception:
                    result = None
                if result is not None and result.success:
                    return result
        return _rk4_integrate(simulation.rhs, initial_state, t_eval)

    def _get_cached(self, key: tuple) -> dict[str, Any] | None:
        if self._memory is None:
            return deepcopy(self._local_cache.get(key))
        return _cached_lookup(self._memory, key, self._local_cache)

    def _set_cached(self, key: tuple, value: dict[str, Any]) -> None:
        self._local_cache[key] = deepcopy(value)


def _flatten_parameters(raw: dict[str, Any]) -> dict[str, float]:
    params = {
        key: float(value["value"])
        for key, value in DEFAULT_BIOKINETIC_PARAMETERS.items()
    }
    records = raw.get("parameters", raw) if isinstance(raw, dict) else {}
    for key, value in records.items():
        if isinstance(value, dict) and "value" in value:
            try:
                params[key] = float(value["value"])
            except (TypeError, ValueError):
                continue
        else:
            try:
                params[key] = float(value)
            except (TypeError, ValueError):
                continue
    return params


def _configure_resource_model(
    topology: dict[str, Any],
    dynamic_signals: list[str],
    params: dict[str, float],
) -> dict[str, Any]:
    requested_mode = str(
        topology.get("resource_model_mode") or LEGACY_RESOURCE_MODEL_MODE
    ).strip()
    warnings: list[str] = []
    if requested_mode not in {
        LEGACY_RESOURCE_MODEL_MODE,
        BASELINE_RELATIVE_RESOURCE_MODEL_MODE,
    }:
        warnings.append(
            f"Unknown resource_model_mode {requested_mode!r}; using "
            f"{LEGACY_RESOURCE_MODEL_MODE!r}."
        )
        requested_mode = LEGACY_RESOURCE_MODEL_MODE

    enabled = requested_mode == BASELINE_RELATIVE_RESOURCE_MODEL_MODE
    params["baseline_relative_resource_model"] = 1.0 if enabled else 0.0
    if not enabled:
        return {
            "mode": LEGACY_RESOURCE_MODEL_MODE,
            "enabled": False,
            "warnings": warnings,
            "parameter_sources": {},
            "cds_length_source_by_gene": {},
            "assumptions": [],
        }

    parameter_sources: dict[str, str] = {}
    for key, default in (
        (
            "host_rnap_baseline_free_fraction",
            DEFAULT_HOST_RNAP_BASELINE_FREE_FRACTION,
        ),
        (
            "host_ribosome_baseline_free_fraction",
            DEFAULT_HOST_RIBOSOME_BASELINE_FREE_FRACTION,
        ),
    ):
        if key in params:
            parameter_sources[key] = "biokinetic_parameter_override"
        else:
            params[key] = default
            parameter_sources[key] = "fixed_assumption"
        if not 0.0 < params[key] <= 1.0:
            warnings.append(
                f"Invalid {key}={params[key]!r}; using fixed-assumption "
                f"default {default}."
            )
            params[key] = default
            parameter_sources[key] = "fixed_assumption_after_invalid_override"

    for key, default in (
        ("default_protein_length_aa", DEFAULT_PROTEIN_LENGTH_AA),
        (
            "translation_elongation_rate_aa_s",
            DEFAULT_TRANSLATION_ELONGATION_RATE_AA_S,
        ),
    ):
        if key in params:
            parameter_sources[key] = "biokinetic_parameter_override"
        else:
            params[key] = default
            parameter_sources[key] = "fixed_assumption"
        if params[key] <= 0.0:
            warnings.append(
                f"Invalid {key}={params[key]!r}; using fixed-assumption "
                f"default {default}."
            )
            params[key] = default
            parameter_sources[key] = "fixed_assumption_after_invalid_override"

    protein_lengths = _numeric_mapping(topology.get("protein_lengths_aa"))
    cds_lengths_bp = _numeric_mapping(topology.get("cds_lengths_bp"))
    construct_metadata = _construct_metadata_by_gene(topology.get("construct_metadata"))
    cds_length_source_by_gene: dict[str, str] = {}
    defaulted_genes: list[str] = []
    for gene in dynamic_signals:
        parameter_key = f"protein_length_aa_{gene}"
        source = ""
        length_aa: float | None = None
        if parameter_key in params and params[parameter_key] > 0.0:
            length_aa = params[parameter_key]
            source = "biokinetic_parameter_override"
        elif protein_lengths.get(gene, 0.0) > 0.0:
            length_aa = protein_lengths[gene]
            source = "topology_protein_lengths_aa"
        elif cds_lengths_bp.get(gene, 0.0) > 0.0:
            length_aa = max(1.0, math.floor(cds_lengths_bp[gene] / 3.0))
            source = "topology_cds_lengths_bp"
        else:
            metadata = construct_metadata.get(gene, {})
            length_aa = _positive_metadata_number(metadata, "protein_length_aa")
            if length_aa is not None:
                source = "construct_metadata_protein_length_aa"
            else:
                cds_bp = _positive_metadata_number(metadata, "cds_length_bp")
                if cds_bp is not None:
                    length_aa = max(1.0, math.floor(cds_bp / 3.0))
                    source = "construct_metadata_cds_length_bp"
        if length_aa is None:
            length_aa = params["default_protein_length_aa"]
            source = "fixed_assumption_default_protein_length"
            defaulted_genes.append(gene)
        params[parameter_key] = float(length_aa)
        cds_length_source_by_gene[gene] = source

    if defaulted_genes:
        warnings.append(
            "CDS/protein length missing for "
            f"{', '.join(sorted(defaulted_genes))}; using fixed-assumption "
            f"default {params['default_protein_length_aa']:.0f} aa."
        )

    return {
        "mode": BASELINE_RELATIVE_RESOURCE_MODEL_MODE,
        "enabled": True,
        "warnings": warnings,
        "parameter_sources": parameter_sources,
        "cds_length_source_by_gene": cds_length_source_by_gene,
        "assumptions": [
            "Host background demand is represented by baseline free-resource fractions.",
            "Growth is normalized to matched empty-vector ribosome capacity.",
            "Translation demand scales with RBS strength and ribosome residence time.",
        ],
    }


def _background_demand_for_free_fraction(
    total: float,
    km: float,
    free_fraction: float,
) -> float:
    total = max(float(total), 1e-9)
    km = max(float(km), 1e-9)
    fraction = min(1.0, max(float(free_fraction), 1e-9))
    free = total * fraction
    return max(0.0, (total - free) * (km + free) / max(free, 1e-9))


def _relative_saturation(
    candidate_free: float, baseline_free: float, km: float
) -> float:
    candidate = max(float(candidate_free), 0.0)
    baseline = max(float(baseline_free), 1e-9)
    km = max(float(km), 1e-9)
    candidate_saturation = candidate / (km + candidate)
    baseline_saturation = baseline / (km + baseline)
    return _clamp01(candidate_saturation / max(baseline_saturation, 1e-9))


def _numeric_mapping(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for key, item in value.items():
        try:
            numeric = float(item)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            result[str(key)] = numeric
    return result


def _construct_metadata_by_gene(value: Any) -> dict[str, dict[str, Any]]:
    if isinstance(value, dict):
        return {
            str(key): dict(item)
            for key, item in value.items()
            if isinstance(item, dict)
        }
    if not isinstance(value, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        gene = str(
            item.get("gene")
            or item.get("gene_id")
            or item.get("signal")
            or item.get("name")
            or ""
        ).strip()
        if gene:
            result[gene] = dict(item)
    return result


def _positive_metadata_number(metadata: dict[str, Any], key: str) -> float | None:
    try:
        value = float(metadata.get(key))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0.0 else None


def _baseline_relative_summary(
    metrics: dict[str, float],
    contract: dict[str, Any],
) -> dict[str, Any]:
    if not contract["enabled"]:
        return {"status": "not_applied"}
    tx_demand = metrics["transcriptional_demand_index_max"]
    tl_demand = metrics["translational_demand_index_max"]
    if max(tx_demand, tl_demand) <= 1e-12:
        limiting_layer = "none_detected"
    elif tx_demand >= tl_demand:
        limiting_layer = "transcription"
    else:
        limiting_layer = "translation"
    return {
        "status": "research_preview",
        "model_mode": contract["mode"],
        "relative_growth_rate_min": metrics["relative_growth_rate_min"],
        "capacity_loss_fraction_max": metrics["capacity_loss_fraction_max"],
        "transcriptional_demand_index_max": tx_demand,
        "translational_demand_index_max": tl_demand,
        "resource_limited_layer": limiting_layer,
        "host_baseline_free_fractions": {
            "rnap": metrics.get("host_rnap_baseline_free_fraction"),
            "ribosome": metrics.get("host_ribosome_baseline_free_fraction"),
        },
        "parameter_sources": contract["parameter_sources"],
        "cds_length_source_by_gene": contract["cds_length_source_by_gene"],
        "assumptions": contract["assumptions"],
        "claim_boundary": "comparative computational screening; not an absolute in-vivo prediction",
    }


def _parameter_provenance(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "source_summary": {
                "conservative_default": len(DEFAULT_BIOKINETIC_PARAMETERS)
            },
            "origin_summary": {"default": len(DEFAULT_BIOKINETIC_PARAMETERS)},
            "data_boundary_summary": {"public": len(DEFAULT_BIOKINETIC_PARAMETERS)},
            "local_private_parameter_count": 0,
            "override_count": 0,
            "unit_system": "nM and seconds",
        }
    summary = raw.get("mining_summary", {})
    if isinstance(summary, dict) and summary:
        res = {
            "source_summary": summary.get("source_summary", {}),
            "origin_summary": summary.get("origin_summary", {}),
            "data_boundary_summary": summary.get("data_boundary_summary", {}),
            "local_private_parameter_count": summary.get(
                "local_private_parameter_count", 0
            ),
            "override_count": summary.get("override_count", 0),
            "records_used": summary.get("records_used", []),
            "all_parameters_have_external_source": summary.get(
                "all_parameters_have_external_source", False
            ),
            "unit_system": summary.get("unit_system", "nM and seconds"),
        }
        for k, v in summary.items():
            if k not in res:
                res[k] = v
        return res
    parameters = raw.get("parameters", raw)
    source_summary: dict[str, int] = {}
    origin_summary: dict[str, int] = {}
    boundary_summary: dict[str, int] = {}
    override_count = 0
    local_private_count = 0
    if isinstance(parameters, dict):
        for parameter in parameters.values():
            if isinstance(parameter, dict):
                source = str(parameter.get("source", "unknown"))
                origin = str(parameter.get("parameter_origin", "unknown"))
                boundary = str(parameter.get("data_boundary", "unknown"))
                source_summary[source] = source_summary.get(source, 0) + 1
                origin_summary[origin] = origin_summary.get(origin, 0) + 1
                boundary_summary[boundary] = boundary_summary.get(boundary, 0) + 1
                if parameter.get("is_override"):
                    override_count += 1
                if boundary == "local_private":
                    local_private_count += 1
            else:
                source_summary["unknown"] = source_summary.get("unknown", 0) + 1
                origin_summary["unknown"] = origin_summary.get("unknown", 0) + 1
                boundary_summary["unknown"] = boundary_summary.get("unknown", 0) + 1
    return {
        "source_summary": source_summary,
        "origin_summary": origin_summary,
        "data_boundary_summary": boundary_summary,
        "local_private_parameter_count": local_private_count,
        "override_count": override_count,
        "unit_system": "nM and seconds",
    }


def _perturb_biokinetic_parameters(
    params: dict[str, float],
    noise_level: float,
    rng: np.random.Generator,
    perturbable: list[str] | None = None,
) -> dict[str, float]:
    sample_params = params.copy()
    keys = perturbable or [
        "transcription_rate",
        "translation_rate",
        "kd",
        "hill_coefficient",
        "leak_fraction",
        "mrna_degradation_rate",
        "protein_degradation_rate",
        "y_min",
        "ymax",
        "y_max",
        "copy_number",
    ]
    for key in keys:
        if key not in sample_params:
            continue
        original = float(sample_params[key])
        if key == "copy_number":
            if original > 0.0:
                cv = max(0.0, float(noise_level))
                s = math.sqrt(math.log(1.0 + cv * cv))
                sample_params[key] = original * math.exp(
                    rng.normal(0.0, s) - 0.5 * s * s
                )
            else:
                sample_params[key] = 0.0
        else:
            sigma = abs(original) * max(0.0, float(noise_level))
            sample_params[key] = max(0.0, float(rng.normal(original, sigma)))
    return sample_params


def _rk4_integrate(rhs, initial_state: np.ndarray, t_eval: np.ndarray) -> Any:
    y = np.zeros((len(initial_state), len(t_eval)))
    y[:, 0] = initial_state
    current = initial_state.copy()
    for index in range(1, len(t_eval)):
        t0 = float(t_eval[index - 1])
        dt = float(t_eval[index] - t_eval[index - 1])
        k1 = rhs(t0, current)
        k2 = rhs(t0 + 0.5 * dt, np.maximum(current + 0.5 * dt * k1, 0.0))
        k3 = rhs(t0 + 0.5 * dt, np.maximum(current + 0.5 * dt * k2, 0.0))
        k4 = rhs(t0 + dt, np.maximum(current + dt * k3, 0.0))
        current = np.maximum(current + (dt / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4), 0.0)
        y[:, index] = current
    return SimpleNamespace(success=True, y=y, message="internal RK4 fallback")


def _infer_gene_count(topology: dict[str, Any]) -> int:
    if topology.get("biokinetic_parameters", {}).get("gene_count"):
        return max(1, int(topology["biokinetic_parameters"]["gene_count"]))
    if topology.get("gate_count"):
        return max(1, int(topology["gate_count"]))
    verilog = str(topology.get("verilog") or "")
    primitive_count = len(re.findall(r"\b(and|or|not|nand|nor|xor|xnor)\s*\(", verilog))
    assign_count = len(re.findall(r"\bassign\b", verilog))
    return max(1, primitive_count + assign_count)


def _simulation_metrics(
    y: np.ndarray,
    trace: list[dict[str, float]],
    params: dict[str, float],
    target_idx: int,
    gene_count: int | None = None,
) -> dict[str, float]:
    if gene_count is None:
        gene_count = y.shape[0] // 3
    num_op = y.shape[0] - 2 * gene_count
    protein = np.maximum(y[num_op + gene_count :, :], 0.0)
    output = (
        protein[target_idx, :]
        if (protein.size and target_idx != -1)
        else np.zeros(y.shape[1])
    )
    output_mean = float(np.mean(output))
    output_std = float(np.std(output))
    burden_values = [entry["burden_nM"] for entry in trace] or [0.0]
    rnap_occupancies = [entry["rnap_occupancy"] for entry in trace] or [0.0]
    ribo_occupancies = [entry["ribosome_occupancy"] for entry in trace] or [0.0]
    rnap_free = [entry["rnap_free"] for entry in trace] or [params["rnap_total"]]
    ribo_free = [entry["ribosome_free"] for entry in trace] or [
        params["ribosome_total"]
    ]
    relative_growth = [entry.get("relative_growth_rate", 1.0) for entry in trace] or [
        1.0
    ]
    capacity_loss = [entry.get("capacity_loss_fraction", 0.0) for entry in trace] or [
        0.0
    ]
    tx_demand = [entry.get("transcriptional_demand_index", 0.0) for entry in trace] or [
        0.0
    ]
    tl_demand = [entry.get("translational_demand_index", 0.0) for entry in trace] or [
        0.0
    ]
    rnap_total_occupancy = [
        entry.get("rnap_total_occupancy", entry.get("rnap_occupancy", 0.0))
        for entry in trace
    ] or [0.0]
    ribosome_total_occupancy = [
        entry.get("ribosome_total_occupancy", entry.get("ribosome_occupancy", 0.0))
        for entry in trace
    ] or [0.0]
    max_burden = float(max(burden_values))
    return {
        "max_burden_nM": max_burden,
        "output_cv": output_std / max(output_mean, 1e-9),
        "signal_to_noise_ratio": output_mean / max(output_std, 1e-9),
        "rnap_occupancy_max": float(max(rnap_occupancies)),
        "ribosome_occupancy_max": float(max(ribo_occupancies)),
        "rnap_free_min": float(min(rnap_free)),
        "ribosome_free_min": float(min(ribo_free)),
        "relative_growth_rate_min": float(min(relative_growth)),
        "capacity_loss_fraction_max": float(max(capacity_loss)),
        "transcriptional_demand_index_max": float(max(tx_demand)),
        "translational_demand_index_max": float(max(tl_demand)),
        "rnap_total_occupancy_max": float(max(rnap_total_occupancy)),
        "ribosome_total_occupancy_max": float(max(ribosome_total_occupancy)),
        "host_rnap_baseline_free_fraction": float(
            params.get("host_rnap_baseline_free_fraction", 1.0)
        ),
        "host_ribosome_baseline_free_fraction": float(
            params.get("host_ribosome_baseline_free_fraction", 1.0)
        ),
    }


def _simulation_trace(
    t_eval: np.ndarray,
    y: np.ndarray,
    trace: list[dict[str, float]],
    target_idx: int,
    gene_count: int | None = None,
) -> dict[str, list[float]]:
    if gene_count is None:
        gene_count = y.shape[0] // 3
    num_op = y.shape[0] - 2 * gene_count
    mrna = np.maximum(y[:num_op, :], 0.0)
    protein_immature = np.maximum(y[num_op : num_op + gene_count, :], 0.0)
    protein_mature = np.maximum(y[num_op + gene_count :, :], 0.0)
    output = (
        protein_mature[target_idx, :]
        if (protein_mature.size and target_idx != -1)
        else np.zeros(y.shape[1])
    )
    sampled_trace = _resample_resource_trace(trace, len(t_eval))
    return {
        "time": _round_series(t_eval),
        "output_protein": _round_series(output),
        "total_mrna": _round_series(
            np.sum(mrna, axis=0) if mrna.size else np.zeros(len(t_eval))
        ),
        "total_protein": _round_series(
            np.sum(protein_immature + protein_mature, axis=0)
            if (protein_immature.size or protein_mature.size)
            else np.zeros(len(t_eval))
        ),
        "rnap_occupancy": _round_series(
            [entry.get("rnap_occupancy", 0.0) for entry in sampled_trace]
        ),
        "ribosome_occupancy": _round_series(
            [entry.get("ribosome_occupancy", 0.0) for entry in sampled_trace]
        ),
        "relative_growth_rate": _round_series(
            [entry.get("relative_growth_rate", 1.0) for entry in sampled_trace]
        ),
        "capacity_loss_fraction": _round_series(
            [entry.get("capacity_loss_fraction", 0.0) for entry in sampled_trace]
        ),
    }


def _resample_resource_trace(
    trace: list[dict[str, float]], target_count: int
) -> list[dict[str, float]]:
    if target_count <= 0:
        return []
    if not trace:
        return [
            {"rnap_occupancy": 0.0, "ribosome_occupancy": 0.0}
            for _ in range(target_count)
        ]
    if len(trace) == target_count:
        return trace
    if target_count == 1:
        return [trace[-1]]
    positions = np.linspace(0, len(trace) - 1, target_count)
    return [trace[int(round(position))] for position in positions]


def _round_series(values: Any) -> list[float]:
    array = np.asarray(values, dtype=float)
    return [round(float(value), 6) for value in array.tolist()]


def _kinetic_score(metrics: dict[str, float]) -> float:
    stability = 1.0 / (1.0 + metrics["output_cv"])
    if "monte_carlo_terminal_output_cv" in metrics:
        stability *= 1.0 / (1.0 + metrics["monte_carlo_terminal_output_cv"])
    resource_penalty = 1.0 - 0.5 * (
        metrics["rnap_occupancy_max"] + metrics["ribosome_occupancy_max"]
    )
    failure_penalty = 1.0 - metrics.get("monte_carlo_failure_rate", 0.0)
    score = (
        0.4 * stability
        + 0.3 * metrics.get("burden_penalty", 1.0)
        + 0.3 * _clamp01(resource_penalty)
    )
    return _clamp01(
        score * failure_penalty * (0.35 + 0.65 * metrics["resource_capacity_factor"])
    )


def _sigmoid_penalty(value: float, soft_limit: float, steepness: float) -> float:
    exponent = steepness * (value - soft_limit)
    exponent = min(60.0, max(-60.0, exponent))
    return 1.0 / (1.0 + math.exp(exponent))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _coerce_float(value: Any, default: float) -> float:
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _physical_assignment_metrics(topology: dict[str, Any]) -> dict[str, float | bool]:
    benchmark_report = topology.get("benchmark_report")
    if not isinstance(benchmark_report, dict):
        benchmark_report = {}
    return {
        "orthogonality_score": _coerce_float(
            topology.get(
                "orthogonality_score", benchmark_report.get("orthogonality_score")
            ),
            1.0,
        ),
        "cello_assignment_score": _coerce_float(
            topology.get(
                "cello_assignment_score", benchmark_report.get("cello_assignment_score")
            ),
            0.0,
        ),
        "cello_buildable": _coerce_bool(
            topology.get("cello_buildable", benchmark_report.get("cello_buildable")),
            False,
        ),
        "toxicity": _coerce_float(
            topology.get("toxicity", benchmark_report.get("toxicity")),
            0.0,
        ),
        "toxicity_score": _coerce_float(
            topology.get("toxicity_score", benchmark_report.get("toxicity_score")),
            1.0,
        ),
    }


def _cache_key(
    topology: dict[str, Any],
    index: int,
    gene_count: int,
    params: dict[str, float],
    simulation_time: float,
    sample_count: int,
    monte_carlo_samples: int,
    noise_fraction: float,
    configuration_hash: str = "",
) -> tuple:
    return (
        SIMULATION_MODEL_VERSION,
        configuration_hash,
        str(topology.get("verilog", "")),
        int(topology.get("gate_count", gene_count)),
        index,
        tuple(sorted((key, round(float(value), 9)) for key, value in params.items())),
        tuple(sorted(_physical_assignment_metrics(topology).items())),
        round(float(simulation_time), 6),
        int(sample_count),
        int(monte_carlo_samples),
        round(float(noise_fraction), 6),
    )


def _stable_seed(
    gene_count: int, params: dict[str, float], simulation_time: float, sample_count: int
) -> int:
    return stable_seed(
        {
            "model_version": SIMULATION_MODEL_VERSION,
            "gene_count": gene_count,
            "parameters": {
                key: round(float(value), 6) for key, value in sorted(params.items())
            },
            "simulation_time": round(float(simulation_time), 6),
            "sample_count": sample_count,
        }
    )


def _cached_lookup(
    _memory, key: tuple, local_cache: dict[tuple, dict[str, Any]]
) -> dict[str, Any] | None:
    return deepcopy(local_cache.get(key))
