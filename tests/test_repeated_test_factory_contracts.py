from __future__ import annotations

import importlib.util
from functools import lru_cache
from io import StringIO
from pathlib import Path
from types import ModuleType

from Bio import SeqIO

from schemas.design_ir import DesignIR
from schemas.design_ir_v2 import DesignIRV2


TESTS_DIR = Path(__file__).resolve().parent


@lru_cache
def _test_module(stem: str) -> ModuleType:
    path = TESTS_DIR / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(f"cand007_{stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load test module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _factory(stem: str, name: str):
    return getattr(_test_module(stem), name)


def test_complete_design_factories_match_except_for_scenario_identity() -> None:
    export_design = _factory("test_design_exporters", "_complete_design")()
    plasmid_design = _factory("test_plasmid_assembler", "_complete_design")()

    assert isinstance(export_design, DesignIR)
    assert isinstance(plasmid_design, DesignIR)
    export_payload = export_design.to_dict()
    plasmid_payload = plasmid_design.to_dict()
    assert export_payload.pop("design_id") == "export_test"
    assert plasmid_payload.pop("design_id") == "plasmid_test"
    assert export_payload == plasmid_payload


def test_complete_design_factories_return_isolated_mutable_graphs() -> None:
    factory = _factory("test_design_exporters", "_complete_design")
    changed = factory()
    unchanged = factory()

    changed.parts[0].sequence = "ATGINVALID"
    changed.validation_status["sequences"] = "mutated"

    assert unchanged.parts[0].sequence != "ATGINVALID"
    assert unchanged.validation_status["sequences"] == "complete"


def test_design_factories_are_distinct_scenario_objects() -> None:
    factories = {
        "assembly_planner": _factory("test_assembly_planner", "_design"),
        "readiness": _factory("test_readiness_evaluator", "_design"),
        "sequence_optimization": _factory(
            "test_sequence_optimization_phase1", "_design"
        ),
        "plasmid_tools": _factory("test_plasmid_tools", "_design"),
        "host_optimization": _factory(
            "test_host_optimization_phase2", "_design"
        ),
        "sequence_analysis": _factory("test_sequence_analysis", "_design"),
    }
    designs = {name: factory() for name, factory in factories.items()}

    assert all(isinstance(design, DesignIRV2) for design in designs.values())
    signatures = {
        name: (
            tuple(design.specification.outputs),
            tuple(part.sequence for part in design.parts),
            tuple(part.evidence_level for part in design.parts),
            len(design.constructs),
            len(design.plasmids),
        )
        for name, design in designs.items()
    }
    assert len(set(signatures.values())) == len(signatures)
    assert factories["assembly_planner"]("ATGCCCTAA").parts[0].sequence == (
        "ATGCCCTAA"
    )


def test_design_factory_mutations_do_not_leak_between_calls() -> None:
    factory = _factory("test_plasmid_tools", "_design")
    changed = factory()
    unchanged = factory()

    changed.parts[0].evidence_level = "illustrative"
    changed.parts[1].sequence = None
    changed.constructs[0].part_instances[0].orientation = "reverse"

    assert unchanged.parts[0].evidence_level == "user_verified"
    assert unchanged.parts[1].sequence == "ATGAAATAA"
    assert unchanged.constructs[0].part_instances[0].orientation == "forward"


def test_buffer_topology_family_preserves_representation_boundaries() -> None:
    simulation = _factory("test_simulation_foundation", "_buffer_topology")()
    temporal = _factory("test_temporal_inputs", "_buffer_topology")()
    sensitivity = _factory("test_sensitivity_analysis", "_buffer_topology")()
    adapter = _factory("test_tool_adapters_phase9", "_buffer_topology")()

    assert simulation == temporal
    assert simulation["verilog"] == sensitivity["verilog"]
    assert simulation["truth_table"] == sensitivity["truth_table"]
    assert simulation["copy_number"] == sensitivity["copy_number"] == 5
    assert type(simulation["copy_number"]) is int
    assert type(sensitivity["copy_number"]) is float
    assert "copy_number" not in adapter
    assert adapter == {
        "verilog": simulation["verilog"],
        "truth_table": simulation["truth_table"],
    }


def test_buffer_topology_factories_return_isolated_nested_payloads() -> None:
    factory = _factory("test_simulation_foundation", "_buffer_topology")
    changed = factory()
    unchanged = factory()

    changed["truth_table"][0]["Y"] = "1"
    changed["copy_number"] = 10

    assert unchanged["truth_table"][0]["Y"] == "0"
    assert unchanged["copy_number"] == 5


def test_backbone_genbank_helpers_encode_different_scenarios() -> None:
    plasmid_text = _factory("test_plasmid_tools", "_backbone_genbank")()
    planner_factory = _factory("test_assembly_planner", "_backbone_genbank")
    planner_text = planner_factory()
    custom_text = planner_factory("C" * 260)

    plasmid = SeqIO.read(StringIO(plasmid_text), "genbank")
    planner = SeqIO.read(StringIO(planner_text), "genbank")
    custom = SeqIO.read(StringIO(custom_text), "genbank")

    assert (plasmid.id, len(plasmid)) == ("BACKBONE1", 100)
    assert (planner.id, len(planner)) == ("PLANNER_BACKBONE", 260)
    assert str(custom.seq) == "C" * 260
    assert [int(feature.location.start) for feature in plasmid.features] == [
        5,
        40,
        70,
    ]
    assert [int(feature.location.start) for feature in planner.features] == [
        10,
        100,
        190,
    ]
