from __future__ import annotations

import numpy as np
import pytest

from schemas.simulation import SIMULATION_MODEL_VERSION, simulation_spec_from_topology
from tools.ode_simulator import (
    BASELINE_RELATIVE_RESOURCE_MODEL_MODE,
    LEGACY_RESOURCE_MODEL_MODE,
    BatchODESimulator,
    ResourceAwareSimulation,
    WarmStartResourceSolver,
)


def _params(*, protein_length_aa: float = 250.0) -> dict[str, float]:
    return {
        "rnap_total": 5000.0,
        "ribosome_total": 25000.0,
        "km_rnap": 75.0,
        "km_ribosome": 120.0,
        "transcription_rate": 0.08,
        "translation_rate": 0.045,
        "mrna_degradation_rate": 0.0038,
        "protein_degradation_rate": 0.00058,
        "growth_rate_dilution": 0.0004,
        "maturation_rate": 0.0011,
        "kd": 50.0,
        "hill_coefficient": 2.0,
        "leak_fraction": 0.02,
        "copy_number": 1.0,
        "promoter_resource_demand": 26.25,
        "baseline_relative_resource_model": 1.0,
        "host_rnap_baseline_free_fraction": 0.80,
        "host_ribosome_baseline_free_fraction": 0.70,
        "default_protein_length_aa": 250.0,
        "translation_elongation_rate_aa_s": 15.0,
        "protein_length_aa_Y": protein_length_aa,
    }


def _simulation(*, protein_length_aa: float = 250.0) -> ResourceAwareSimulation:
    params = _params(protein_length_aa=protein_length_aa)
    return ResourceAwareSimulation(
        signals={"A": "input", "Y": "output"},
        deps={"Y": ("buf", ["A"])},
        params=params,
        solver=WarmStartResourceSolver(
            rnap_free=params["rnap_total"],
            ribosome_free=params["ribosome_total"],
        ),
    )


def test_baseline_relative_zero_synthetic_load_returns_matched_baseline() -> None:
    params = _params()
    simulation = ResourceAwareSimulation(
        signals={"A": "input"},
        deps={},
        params=params,
        solver=WarmStartResourceSolver(
            rnap_free=params["rnap_total"],
            ribosome_free=params["ribosome_total"],
        ),
    )

    derivative = simulation.rhs(0.0, np.array([], dtype=float))
    trace = simulation.resource_trace[-1]

    assert derivative.size == 0
    assert trace["rnap_total_occupancy"] > 0.0
    assert trace["ribosome_total_occupancy"] > 0.0
    assert trace["rnap_occupancy"] == pytest.approx(0.0, abs=1e-6)
    assert trace["ribosome_occupancy"] == pytest.approx(0.0, abs=1e-6)
    assert trace["relative_growth_rate"] == pytest.approx(1.0, abs=1e-6)
    assert trace["capacity_loss_fraction"] == pytest.approx(0.0, abs=1e-6)


def test_more_mrna_monotonically_reduces_relative_ribosome_capacity() -> None:
    low = _simulation()
    high = _simulation()

    low.rhs(0.0, np.array([5.0, 0.0, 0.0]))
    high.rhs(0.0, np.array([25.0, 0.0, 0.0]))

    low_trace = low.resource_trace[-1]
    high_trace = high.resource_trace[-1]
    assert (
        high_trace["translational_demand_index"]
        > low_trace["translational_demand_index"]
    )
    assert (
        high_trace["ribosome_capacity_fraction"]
        < low_trace["ribosome_capacity_fraction"]
    )
    assert high_trace["relative_growth_rate"] < low_trace["relative_growth_rate"]


def test_longer_cds_increases_residence_demand_at_equal_mrna() -> None:
    short = _simulation(protein_length_aa=100.0)
    long = _simulation(protein_length_aa=1000.0)
    state = np.array([10.0, 0.0, 0.0])

    short.rhs(0.0, state)
    long.rhs(0.0, state)

    short_trace = short.resource_trace[-1]
    long_trace = long.resource_trace[-1]
    assert (
        long_trace["translational_demand_index"]
        > short_trace["translational_demand_index"]
    )
    assert (
        long_trace["ribosome_capacity_fraction"]
        < short_trace["ribosome_capacity_fraction"]
    )


def test_batch_simulator_reports_opt_in_model_side_by_side() -> None:
    topology = {
        "resource_model_mode": BASELINE_RELATIVE_RESOURCE_MODEL_MODE,
        "verilog": "module c(input A, output Y); assign Y = A; endmodule",
        "protein_lengths_aa": {"Y": 800},
    }

    result = BatchODESimulator(
        simulation_time=60.0,
        sample_count=16,
    ).simulate_topology(topology)

    summary = result["baseline_relative_resources"]
    comparison = result["resource_model_comparison"]
    assert result["resource_model_mode"] == BASELINE_RELATIVE_RESOURCE_MODEL_MODE
    assert result["simulation_model_version"] == SIMULATION_MODEL_VERSION
    assert summary["status"] == "research_preview"
    assert 0.0 < summary["relative_growth_rate_min"] <= 1.0
    assert 0.0 <= summary["capacity_loss_fraction_max"] <= 1.0
    assert summary["cds_length_source_by_gene"]["Y"] == ("topology_protein_lengths_aa")
    assert comparison["legacy_outputs"]["status"] == "retained"
    assert comparison[BASELINE_RELATIVE_RESOURCE_MODEL_MODE] == summary
    assert len(result["ode_trace"]["relative_growth_rate"]) == 16
    assert len(result["ode_trace"]["capacity_loss_fraction"]) == 16


def test_cds_base_pairs_are_converted_to_amino_acid_residence_length() -> None:
    topology = {
        "resource_model_mode": BASELINE_RELATIVE_RESOURCE_MODEL_MODE,
        "verilog": "module c(input A, output Y); assign Y = A; endmodule",
        "cds_lengths_bp": {"Y": 900},
    }

    result = BatchODESimulator(
        simulation_time=30.0,
        sample_count=8,
    ).simulate_topology(topology)

    assert (
        result["baseline_relative_resources"]["cds_length_source_by_gene"]["Y"]
        == "topology_cds_lengths_bp"
    )


def test_stochastic_path_rejects_uncalibrated_baseline_relative_propensities() -> None:
    topology = {
        "resource_model_mode": BASELINE_RELATIVE_RESOURCE_MODEL_MODE,
        "verilog": "module c(input A, output Y); assign Y = A; endmodule",
    }

    with pytest.raises(ValueError, match="ODE path only"):
        BatchODESimulator().simulate_stochastic(topology, runs=1)


def test_legacy_preview_remains_default_and_resource_inputs_are_hashed() -> None:
    legacy = {"verilog": "module c(input A, output Y); assign Y = A; endmodule"}
    baseline = {
        **legacy,
        "resource_model_mode": BASELINE_RELATIVE_RESOURCE_MODEL_MODE,
        "protein_lengths_aa": {"Y": 800},
    }

    legacy_result = BatchODESimulator(
        simulation_time=30.0,
        sample_count=8,
    ).simulate_topology(legacy)
    legacy_spec = simulation_spec_from_topology(legacy)
    baseline_spec = simulation_spec_from_topology(baseline)

    assert legacy_result["resource_model_mode"] == LEGACY_RESOURCE_MODEL_MODE
    assert "baseline_relative_resources" not in legacy_result
    assert legacy_result["resource_model_comparison"][
        BASELINE_RELATIVE_RESOURCE_MODEL_MODE
    ] == {"status": "available_not_applied"}
    assert legacy_spec.configuration_hash != baseline_spec.configuration_hash
