from __future__ import annotations

from tools.tool_adapters import (
    CAPABILITY_ASSEMBLY_SIMULATION,
    CAPABILITY_FORMAT_VALIDATION,
    CAPABILITY_PRIMER_DESIGN,
    CAPABILITY_SEQUENCE_ANNOTATION,
    CAPABILITY_SEQUENCE_OPTIMIZATION,
    DNACauldronAssemblySimulationAdapter,
    DNAChiselSequenceOptimizationAdapter,
    DNAFeaturesViewerAdapter,
    Primer3Adapter,
    SBOL3FormatValidationAdapter,
    inspect_capabilities,
)


def test_inspect_capabilities_catalog_includes_all_adapters() -> None:
    capabilities_info = inspect_capabilities()
    capabilities_list = capabilities_info["capabilities"]

    assert CAPABILITY_SEQUENCE_OPTIMIZATION in capabilities_list
    assert CAPABILITY_ASSEMBLY_SIMULATION in capabilities_list
    assert CAPABILITY_PRIMER_DESIGN in capabilities_list
    assert CAPABILITY_SEQUENCE_ANNOTATION in capabilities_list
    assert CAPABILITY_FORMAT_VALIDATION in capabilities_list

    tool_names = {t["tool_name"] for t in capabilities_info["tools"]}
    assert "dna_chisel" in tool_names
    assert "dna_cauldron" in tool_names
    assert "primer3" in tool_names
    assert "dna_features_viewer" in tool_names
    assert "sbol3" in tool_names


def test_dna_chisel_adapter_run_and_fallback() -> None:
    adapter = DNAChiselSequenceOptimizationAdapter()
    availability = adapter.available()

    assert availability.tool_name == "dna_chisel"
    assert availability.capability == CAPABILITY_SEQUENCE_OPTIMIZATION
    assert availability.fallback_available is True

    # Test run with restriction site GAATTC
    res = adapter.run({"sequence": "ATGAATTCGAATTCATGAATTC", "avoid_sites": ["GAATTC"]})
    assert res.status == "ok"
    assert "optimized_sequence" in res.output
    assert "GAATTC" not in res.output["optimized_sequence"]
    assert res.metrics["gc_content"] > 0.0


def test_dna_cauldron_adapter_run_and_fallback() -> None:
    adapter = DNACauldronAssemblySimulationAdapter()
    availability = adapter.available()

    assert availability.tool_name == "dna_cauldron"
    assert availability.capability == CAPABILITY_ASSEMBLY_SIMULATION

    res = adapter.run({
        "fragments": ["ATGCGATC", "CGATCGAT", "GATCGA"],
        "assembly_type": "golden_gate",
        "enzyme": "BsaI",
    })
    assert res.status == "ok"
    assert res.output["assembled_sequence"] == "ATGCGATCCGATCGATGATCGA"
    assert res.metrics["fragment_count"] == 3


def test_primer3_adapter_run() -> None:
    adapter = Primer3Adapter()
    availability = adapter.available()

    assert availability.tool_name == "primer3"
    assert availability.capability == CAPABILITY_PRIMER_DESIGN

    res = adapter.run({"sequence": "ATGCGTACGTACGTACGATCGATCGATCGATCGATC", "target_tm": 60.0})
    assert res.status == "ok"
    assert "forward_primer" in res.output
    assert "reverse_primer" in res.output
    assert res.metrics["forward_tm"] > 0.0
    assert res.metrics["reverse_tm"] > 0.0


def test_dna_features_viewer_adapter_run() -> None:
    adapter = DNAFeaturesViewerAdapter()
    availability = adapter.available()

    assert availability.tool_name == "dna_features_viewer"
    assert availability.capability == CAPABILITY_SEQUENCE_ANNOTATION

    features = [
        {"start": 10, "end": 100, "label": "Promoter_pTet", "color": "#ff0000"},
        {"start": 120, "end": 800, "label": "GFP_CDS", "color": "#00ff00"},
    ]
    res = adapter.run({"features": features, "sequence_length": 1000})
    assert res.status == "ok"
    assert res.output["feature_count"] == 2
    assert res.metrics["feature_count"] == 2


def test_sbol3_validation_adapter_run() -> None:
    adapter = SBOL3FormatValidationAdapter()
    availability = adapter.available()

    assert availability.tool_name == "sbol3"
    assert availability.capability == CAPABILITY_FORMAT_VALIDATION

    sbol_doc = {
        "identity": "http://example.org/component1",
        "type": "Component",
        "display_id": "component1",
    }
    res = adapter.run({"sbol_document": sbol_doc})
    assert res.status == "ok"
    assert res.output["is_valid"] is True
    assert res.metrics["valid"] is True
