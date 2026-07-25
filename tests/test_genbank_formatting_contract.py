from __future__ import annotations

from exporters.genbank_exporter import (
    _incomplete_constructs as linear_incomplete_constructs,
    _locus_token as linear_locus_token,
    _origin_lines as linear_origin_lines,
    _qualifier as linear_qualifier,
)
from exporters.genbank_formatting import (
    incomplete_constructs,
    locus_token,
    origin_lines,
    qualifier,
    single_line,
)
from exporters.plasmid_assembler import (
    _incomplete_constructs as plasmid_incomplete_constructs,
    _locus_token as plasmid_locus_token,
    _origin_lines as plasmid_origin_lines,
    _qualifier as plasmid_qualifier,
)
from schemas.design_ir import topology_to_design_ir


def test_genbank_exporters_share_canonical_formatting_helpers() -> None:
    assert linear_incomplete_constructs is incomplete_constructs
    assert plasmid_incomplete_constructs is incomplete_constructs
    assert linear_origin_lines is origin_lines
    assert plasmid_origin_lines is origin_lines
    assert linear_locus_token is locus_token
    assert plasmid_locus_token is locus_token
    assert linear_qualifier is qualifier
    assert plasmid_qualifier is qualifier


def test_genbank_origin_lines_preserve_exact_grouping_and_offsets() -> None:
    sequence = ("A" * 60) + "CGTAC"

    assert origin_lines(sequence) == [
        "        1 aaaaaaaaaa aaaaaaaaaa aaaaaaaaaa aaaaaaaaaa aaaaaaaaaa aaaaaaaaaa",
        "       61 cgtac",
    ]


def test_genbank_tokens_and_qualifiers_preserve_exact_escaping() -> None:
    assert locus_token(" __a b/c__") == "a_b_c"
    assert locus_token(" / ") == "DESIGN"
    assert single_line(" A\n  B\tC ") == "A B C"
    assert qualifier(' A\\B "C"\nD ') == "A\\\\B 'C' D"


def test_incomplete_constructs_preserves_construct_and_part_order() -> None:
    design = topology_to_design_ir(
        {"verilog": "module c(input A, output GFP); assign GFP = A; endmodule"}
    )
    part_map = {part.id: part for part in design.parts}

    expected = {
        construct.id: list(construct.parts)
        for construct in design.constructs
        if construct.parts
    }
    assert incomplete_constructs(design, part_map) == expected
