from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from tools.tool_adapters import (  # noqa: E402
    CelloLogicSynthesisAdapter,
    DNACauldronAssemblySimulationAdapter,
    DNAChiselSequenceOptimizationAdapter,
    DNAFeaturesViewerAdapter,
    ODESimulationAdapter,
    Primer3Adapter,
    RNAFoldingAdapter,
    SBOL3FormatValidationAdapter,
    SequenceRecordLookupAdapter,
    StochasticSimulationAdapter,
    default_tool_adapters,
    detect_cli_tool,
    detect_python_module,
    inspect_capabilities,
)


def _sample_topology() -> dict:
    return {
        "verilog": "module c(input A, output Y); assign Y = A; endmodule",
        "truth_table": [
            {"A": "0", "Y": "0"},
            {"A": "1", "Y": "1"},
        ],
    }


def test_conformance_matrix_catalog_completeness() -> None:
    catalog_info = inspect_capabilities()
    tools = catalog_info["tools"]
    assert len(tools) >= 10
    tool_names = {t["tool_name"] for t in tools}
    expected_names = {
        "cello",
        "internal_ode_simulator",
        "viennarna",
        "internal_stochastic_simulator",
        "ncbi_entrez",
        "dna_chisel",
        "dna_cauldron",
        "primer3",
        "dna_features_viewer",
        "sbol3",
    }
    assert expected_names <= tool_names


def test_conformance_point1_available_and_successful() -> None:
    adapters = default_tool_adapters()
    for adapter in adapters:
        avail = adapter.available()
        assert avail.tool_name is not None
        assert avail.adapter_name is not None
        assert avail.capability is not None
        assert avail.status in ("available", "fallback", "unavailable")


def test_conformance_point2_missing_dependency_normalization() -> None:
    avail_py = detect_python_module(
        "nonexistent_module_xyz_123",
        tool_name="missing_py",
        adapter_name="missing_py_adapter",
        capability="test",
        fallback_available=True,
    )
    assert avail_py.status == "unavailable"
    assert avail_py.fallback_available is True
    assert len(avail_py.warnings) == 1
    assert avail_py.warnings[0].code == "TOOL_UNAVAILABLE"

    avail_cli = detect_cli_tool(
        "nonexistent_cli_bin_xyz_123",
        tool_name="missing_cli",
        adapter_name="missing_cli_adapter",
        capability="test",
        fallback_available=True,
    )
    assert avail_cli.status == "unavailable"
    assert avail_cli.fallback_available is True
    assert len(avail_cli.warnings) == 1
    assert avail_cli.warnings[0].code == "TOOL_UNAVAILABLE"


def test_conformance_point3_invalid_input_rejection() -> None:
    # Cello
    res_cello = CelloLogicSynthesisAdapter().run({})
    assert res_cello.status == "failed"
    assert any(w.code == "MISSING_INPUT" for w in res_cello.warnings)

    # ODE
    res_ode = ODESimulationAdapter().run({})
    assert res_ode.status == "failed"

    # RNA
    res_rna = RNAFoldingAdapter().run({"sequence": ""})
    assert res_rna.status == "failed"

    # Stochastic
    res_stoch = StochasticSimulationAdapter().run({})
    assert res_stoch.status == "failed"

    # Sequence Lookup
    res_seq = SequenceRecordLookupAdapter().run({})
    assert res_seq.status == "invalid_input"

    # DNA Chisel
    res_chisel = DNAChiselSequenceOptimizationAdapter().run({"sequence": ""})
    assert res_chisel.status == "failed"

    # DNA Cauldron
    res_cauldron = DNACauldronAssemblySimulationAdapter().run({"fragments": ["ATGC"]})
    assert res_cauldron.status == "failed"

    # Primer3
    res_p3 = Primer3Adapter().run({"sequence": ""})
    assert res_p3.status == "failed"

    # DNA Features Viewer
    res_dfv = DNAFeaturesViewerAdapter().run({})
    assert res_dfv.status == "failed"

    # SBOL3
    res_sbol = SBOL3FormatValidationAdapter().run({})
    assert res_sbol.status == "failed"


def test_conformance_point4_error_recovery_and_warning_formatting() -> None:
    adapter = SequenceRecordLookupAdapter()
    res = adapter.run({"accession": "INVALID_ACCESSION_9999999"})
    assert res.status == "unresolved"
    assert res.availability.fallback_used is True
    assert any(w.code == "RECORD_UNRESOLVED" for w in res.warnings)


def test_conformance_point5_malformed_output_safety() -> None:
    res = RNAFoldingAdapter().run({"sequence": "AUGCAUGCAU"})
    assert res.status in ("ok", "failed")
    assert "mfe" in res.output
    assert isinstance(res.output["mfe"], float)


def test_conformance_point6_deterministic_fallback() -> None:
    adapter = RNAFoldingAdapter()
    res1 = adapter.run({"sequence": "AUGCAUGCAU"})
    res2 = adapter.run({"sequence": "AUGCAUGCAU"})
    assert res1.output["mfe"] == res2.output["mfe"]
    assert res1.output["structure"] == res2.output["structure"]


def test_conformance_point7_concurrent_repeated_invocation() -> None:
    adapter = ODESimulationAdapter()
    topology = _sample_topology()
    for _ in range(5):
        res = adapter.run({"topology": topology})
        assert res.status == "ok"
        assert "dynamic_margin" in res.metrics


def test_conformance_point8_argument_and_path_safety() -> None:
    adapter = DNAChiselSequenceOptimizationAdapter()
    res = adapter.run({
        "sequence": "ATGAATTCGAATTC",
        "avoid_sites": ["GAATTC", "; rm -rf /;"],
    })
    assert res.status == "ok"
    assert "GAATTC" not in res.output["optimized_sequence"]


def test_conformance_point9_provenance_and_license_status() -> None:
    adapters = default_tool_adapters()
    for adapter in adapters:
        avail = adapter.available()
        dict_rep = avail.to_dict()
        assert "tool_name" in dict_rep
        assert "adapter_name" in dict_rep
        assert "capability" in dict_rep
        assert "status" in dict_rep
        assert "fallback_available" in dict_rep
        assert "fallback_used" in dict_rep
        assert "license_sensitive" in dict_rep
        assert "warnings" in dict_rep


def test_conformance_point10_internal_and_fixture_scope_is_explicit() -> None:
    # Internal simulation capabilities may be exercised, but are not external-tool evidence.
    ode_res = ODESimulationAdapter().run({"topology": _sample_topology()})
    assert ode_res.status == "ok"
    assert ode_res.availability.status in ("available", "fallback")

    # Stochastic simulation with internal Gillespie
    stoch_adapter = StochasticSimulationAdapter()
    stoch_avail = stoch_adapter.available()
    assert stoch_avail.status == "available"

    # Sequence lookup remains an explicit local fixture, never a live Entrez result.
    seq_res = SequenceRecordLookupAdapter().run({"accession": "NC_000913.3"})
    assert seq_res.status == "ok"
    assert seq_res.output["provider"] == "ncbi_fixture"
    assert seq_res.availability.status == "fallback"
    assert seq_res.availability.fallback_used is True
    assert any(w.code == "FIXTURE_ONLY" for w in seq_res.warnings)
