from __future__ import annotations

from datetime import date

from exporters.export_result import ExportResult
from exporters.genbank_formatting import (
    incomplete_constructs as _incomplete_constructs,
    locus_token as _locus_token,
    origin_lines as _origin_lines,
    qualifier as _qualifier,
    single_line as _single_line,
)
from exporters.sequence_utils import is_valid_iupac_dna
from schemas.design_ir import BiologicalPart, DesignIR, GeneticConstruct


FEATURE_TYPES = {
    "promoter": "promoter",
    "rbs": "RBS",
    "cds": "CDS",
    "terminator": "terminator",
    "sensor": "misc_feature",
    "backbone": "misc_feature",
    "scar": "misc_feature",
    "linker": "misc_feature",
    "insulator": "misc_feature",
    "operator": "protein_bind",
    "notes": "misc_feature",
}


def export_genbank(
    design: DesignIR,
    *,
    record_date: date | None = None,
) -> ExportResult:
    part_map = {part.id: part for part in design.parts}
    incomplete = _incomplete_constructs(design, part_map)
    if incomplete:
        errors = [
            f"{construct_id} is missing sequences for: {', '.join(part_ids)}"
            for construct_id, part_ids in incomplete.items()
        ]
        return ExportResult(
            ok=False,
            format="GenBank",
            filename=f"{_locus_token(design.design_id)}.gb",
            media_type="text/x-genbank",
            content="",
            status="blocked_missing_sequences",
            errors=errors,
            warnings=[
                "GenBank export requires a complete sequence for every part in every exported construct."
            ],
        )

    construct_part_ids = {
        part_id
        for construct in design.constructs
        for part_id in construct.parts
    }
    invalid = {
        part.id: part.sequence or ""
        for part in design.parts
        if part.id in construct_part_ids
        and part.sequence
        and not is_valid_iupac_dna(part.sequence)
    }
    if invalid:
        return ExportResult(
            ok=False,
            format="GenBank",
            filename=f"{_locus_token(design.design_id)}.gb",
            media_type="text/x-genbank",
            content="",
            status="blocked_invalid_sequences",
            errors=[
                f"{part_id} contains non-IUPAC DNA characters."
                for part_id in sorted(invalid)
            ],
        )

    resolved_record_date = record_date or date.today()
    records = [
        _construct_record(design, construct, part_map, resolved_record_date)
        for construct in design.constructs
    ]
    if not records:
        return ExportResult(
            ok=False,
            format="GenBank",
            filename=f"{_locus_token(design.design_id)}.gb",
            media_type="text/x-genbank",
            content="",
            status="blocked_no_constructs",
            errors=["Design has no constructs to export."],
        )
    return ExportResult(
        ok=True,
        format="GenBank",
        filename=f"{_locus_token(design.design_id)}_{_locus_token(design.revision.revision_id)}.gb",
        media_type="text/x-genbank",
        content="\n".join(records),
        status="ready",
    )


def _construct_record(
    design: DesignIR,
    construct: GeneticConstruct,
    part_map: dict[str, BiologicalPart],
    record_date: date,
) -> str:
    parts = [part_map[part_id] for part_id in construct.parts]
    sequence = "".join(part.sequence or "" for part in parts).upper()
    locus = _locus_token(f"{design.design_id}_{construct.id}")[:16]
    lines = [
        f"LOCUS       {locus:<16}{len(sequence):>11} bp    DNA     linear   SYN {record_date.strftime('%d-%b-%Y').upper()}",
        f"DEFINITION  {_single_line(construct.name)}.",
        f"ACCESSION   {locus}",
        f"VERSION     {locus}.{design.revision.revision_number}",
        "KEYWORDS    synthetic biology; genetic circuit.",
        "SOURCE      synthetic DNA construct",
        "  ORGANISM  synthetic DNA construct",
        "FEATURES             Location/Qualifiers",
        f"     source          1..{len(sequence)}",
        '                     /organism="synthetic DNA construct"',
        f'                     /design_id="{_qualifier(design.design_id)}"',
        f'                     /revision="{_qualifier(design.revision.revision_id)}"',
    ]
    offset = 1
    for part in parts:
        end = offset + len(part.sequence or "") - 1
        feature_type = FEATURE_TYPES.get(part.part_type.lower(), "misc_feature")
        lines.extend(
            [
                f"     {feature_type:<16}{offset}..{end}",
                f'                     /label="{_qualifier(part.name)}"',
                f'                     /part_id="{_qualifier(part.id)}"',
                f'                     /note="{_qualifier(part.role)}"',
                f'                     /evidence="{_qualifier(part.confidence)}"',
                f'                     /source_library="{_qualifier(part.source)}"',
            ]
        )
        if part.assignment:
            lines.append(
                f'                     /assigned_part_id="{_qualifier(part.assignment.part_id)}"'
            )
        lines.extend(
            f'                     /provenance="{_qualifier(provenance_id)}"'
            for provenance_id in part.provenance_ids
        )
        offset = end + 1
    lines.append("ORIGIN")
    lines.extend(_origin_lines(sequence))
    lines.append("//")
    return "\n".join(lines) + "\n"
