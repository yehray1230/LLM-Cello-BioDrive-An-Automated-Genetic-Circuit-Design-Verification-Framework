from __future__ import annotations

import csv
from io import StringIO

from exporters.bom_exporter import BOM_COLUMNS, export_bom_csv
from exporters.genbank_exporter import export_genbank
from exporters.sbol3_exporter import export_sbol3_turtle
from schemas.design_ir import DesignIR, topology_to_design_ir
from importers.genbank_importer import genbank_to_import_draft


def _complete_design() -> DesignIR:
    design = topology_to_design_ir(
        {
            "verilog": (
                "module c(input A, output GFP); "
                "wire n; not(n, A); assign GFP = n; endmodule"
            )
        },
        design_id="export_test",
    )
    sequences = {
        "promoter": "TTGACA",
        "RBS": "AGGAGG",
        "CDS": "ATGAAATAA",
        "terminator": "GCCGCC",
        "sensor": "ATGCCCTAA",
    }
    for part in design.parts:
        part.sequence = sequences[part.part_type]
        part.confidence = "test_fixture"
    design.validation_status["sequences"] = "complete"
    return design


def test_bom_export_contains_ordered_construct_parts_and_evidence() -> None:
    design = _complete_design()

    result = export_bom_csv(design)
    rows = list(csv.DictReader(StringIO(result.content)))

    assert result.ok is True
    assert result.media_type == "text/csv"
    assert list(rows[0]) == BOM_COLUMNS
    assert len(rows) == sum(len(construct.parts) for construct in design.constructs)
    assert rows[0]["position"] == "1"
    assert rows[0]["sequence_status"] == "available"
    assert rows[0]["revision_id"] == "revision_1"


def test_genbank_export_blocks_incomplete_construct_sequences() -> None:
    design = topology_to_design_ir(
        {"verilog": "module c(input A, output GFP); assign GFP = A; endmodule"}
    )

    result = export_genbank(design)

    assert result.ok is False
    assert result.status == "blocked_missing_sequences"
    assert result.content == ""
    assert any("missing sequences" in error for error in result.errors)


def test_genbank_export_writes_features_and_origin_for_complete_design() -> None:
    design = _complete_design()

    result = export_genbank(design)

    assert result.ok is True
    assert result.content.count("LOCUS       ") == len(design.constructs)
    assert result.content.count("//") == len(design.constructs)
    assert "FEATURES             Location/Qualifiers" in result.content
    assert "     promoter" in result.content
    assert "     RBS" in result.content
    assert "     CDS" in result.content
    assert "     terminator" in result.content
    assert "ORIGIN" in result.content
    assert "/revision=\"revision_1\"" in result.content


def test_sbol3_export_represents_components_sequences_order_and_interactions() -> None:
    design = _complete_design()

    result = export_sbol3_turtle(design)

    assert result.ok is True
    assert result.media_type == "text/turtle"
    assert "@prefix sbol: <http://sbols.org/v3#>" in result.content
    assert "a sbol:Component" in result.content
    assert "a sbol:Sequence" in result.content
    assert "a sbol:SubComponent" in result.content
    assert "a sbol:Range" in result.content
    assert "sbol:hasLocation" in result.content
    assert "sbol:orientation sbol:inline" in result.content
    assert "a sbol:Constraint" in result.content
    assert "sbol:restriction sbol:precedes" in result.content
    assert "a sbol:Interaction" in result.content
    assert "a sbol:Participation" in result.content
    assert "sbol:hasInteraction" in result.content


def test_sbol3_export_allows_sequence_less_conceptual_design() -> None:
    design = topology_to_design_ir(
        {"verilog": "module c(input A, output GFP); assign GFP = A; endmodule"}
    )

    result = export_sbol3_turtle(design)

    assert result.ok is True
    assert result.status == "ready_with_warnings"
    assert any("sequence-less" in warning for warning in result.warnings)
    assert "a sbol:Component" in result.content


def test_invalid_dna_is_blocked_in_genbank_and_omitted_from_sbol_sequence() -> None:
    design = _complete_design()
    construct_part_id = design.constructs[0].parts[0]
    next(part for part in design.parts if part.id == construct_part_id).sequence = "ATG-INVALID"

    genbank = export_genbank(design)
    sbol = export_sbol3_turtle(design)

    assert genbank.ok is False
    assert genbank.status == "blocked_invalid_sequences"
    assert any("non-IUPAC" in error for error in genbank.errors)
    assert sbol.ok is True
    assert any("non-IUPAC" in warning for warning in sbol.warnings)


def test_genbank_round_trip() -> None:
    design = _complete_design()
    result = export_genbank(design)
    assert result.ok is True

    draft = genbank_to_import_draft(result.content, filename="roundtrip.gb")
    assert len(draft.parts) > 0
    part_types = [p.part_type for p in draft.parts]
    assert "promoter" in part_types
    assert "RBS" in part_types
    assert "CDS" in part_types


def test_genbank_and_sbol3_multi_gene_complex_construct_export() -> None:
    design = topology_to_design_ir(
        {
            "verilog": (
                "module multi_gene(input A, input B, output Y); "
                "wire n1, n2; nor(n1, A, B); not(n2, n1); assign Y = n2; endmodule"
            )
        },
        design_id="multi_gene_complex",
    )
    part_sequences = {
        "promoter": "TTGACAGATACT",
        "RBS": "AGGAGGACAA",
        "CDS": "ATGAAACGGTAA",
        "terminator": "GCCGCCAAAA",
        "sensor": "ATGCCCGGGTAA",
    }
    for part in design.parts:
        part.sequence = part_sequences.get(part.part_type, "ATGCCCCAATAA")
        part.confidence = "test_fixture"
    design.validation_status["sequences"] = "complete"

    gb_result = export_genbank(design)
    sbol_result = export_sbol3_turtle(design)

    assert gb_result.ok is True
    assert gb_result.content.count("LOCUS       ") == len(design.constructs)
    assert len(design.constructs) >= 2

    draft = genbank_to_import_draft(gb_result.content, filename="multi_gene.gb")
    assert len(draft.parts) >= 4
    assert draft.source_type == "GenBank"

    assert sbol_result.ok is True
    assert "@prefix sbol: <http://sbols.org/v3#>" in sbol_result.content
    assert sbol_result.content.count("a sbol:Component") >= 2
