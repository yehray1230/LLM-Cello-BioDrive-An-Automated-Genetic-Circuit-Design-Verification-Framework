from __future__ import annotations

from datetime import date

from exporters.export_result import ExportResult
from exporters.genbank_formatting import (
    incomplete_constructs as _incomplete_constructs,
    locus_token as _locus_token,
    origin_lines as _origin_lines,
    qualifier as _qualifier,
)
from exporters.sequence_utils import is_valid_iupac_dna
from schemas.design_ir import DesignIR

FEATURE_TYPES = {
    "promoter": "promoter",
    "rbs": "RBS",
    "cds": "CDS",
    "terminator": "terminator",
    "sensor": "misc_feature",
    "rep_origin": "rep_origin",
    "misc_feature": "misc_feature",
}

BACKBONE_TEMPLATES = {
    "pUC19 (High copy, AmpR)": {
        "sequence": (
            "GCGCCCAATACGCAAACCGCCTCTCCCCGCGCGTTGGCCGATTCATTAATGCAGCTGGCACGACAGGTTTCCCGACTGGAAAGCGGGCAGTGAGCGCAAC"
            "GCAATTAATGTGAGTTAGCTCACTCATTAGGCACCCCAGGCTTTACACTTTATGCTTCCGGCTCGTATGTTGTGTGGAATTGTGAGCGGATAACAATTTC"
            "ACACAGGAAACAGCTATGACCATGATTACGCCAAGCTTGATGGGTCAGCAACCACCGTGGGCTCGCTGAGCACCACCACCACCACCACCACTGAGATCCG"
            "GCTGCTAACAAAGCCCGAAAGGAAGCTGAGTTGGCTGCTGCCACCGCTGAGCAATAACTAGCATAACCCCTTGGGGCCTCTAAACGGGTCTTGAGGGGTT"
            "TTTTGCTGAAAGGAGGAACTATATCCGGATATCCACAGGACGGGTGTGGTCGCCATGATCGCGTAGTCGATAGTGGCTCCAAGAGCCTGCGAAGTGATGC"
            "GTGAGGGTGACCTAAGCATTACATTATTATGCAATGTGAGTTAACTCACCTTAGACCACTTTTCAATATCATGCGTGGTGTGCACAAATGGCAGTGACTA"
            "ACGTCAGTGACCCAAGTCACTTAGCACTTGACCTAAGCATTAGCGACTAACGTCAGTGACCCAAGTCACTTAGCACTTGACCTAAGCATTAGCGACTAAC"
            "GACGGGTGTGGTCGCCATGATCGCGTAGTCGATAGTGGCTCCAAGAGCCTGCGAAGTGATGCGTGAGGGTGACCTAAGCATTACATTATTATGCAATGTG"
            "AGTTAACTCACCTTAGACCACTTTTCAATATCATGCGTGGTGTGCACAAATGGCAGTGACTAACGTCAGTGACCCAAGTCACTTAGCACTTGACCTAAGC"
            "ATTAGCGACTAACGTCAGTGACCCAAGTCACTTAGCACTTGACCTAAGCATTAGCGACTAACGACGGGTGTGGTCGCCATGATCGCGTAGTCGATAGTGG"
            "CTCCAAGAGCCTGCGAAGTGATGCGTGAGGGTGACCTAAGCATTACATTATTATGCAATGTGAGTTAACTCACCTTAGACCACTTTTCAATATCATGCGT"
            "GGTGTGCACAAATGGCAGTGACTAACGTCAGTGACCCAAGTCACTTAGCACTTGACCTAAGCATTAGCGACTAACGTCAGTGACCCAAGTCACTTAGCAC"
            "TTGACCTAAGCATTAGCGACTAACGACGGGTGTGGTCGCCATGATCGCGTAGTCGATAGTGGCTCCAAGAGCCTGCGAAGTGATGCGTGAGGGTGACCTA"
            "AGCATTACATTATTATGCAATGTGAGTTAACTCACCTTAGACCACTTTTCAATATCATGCGTGGTGTGCACAAATGGCAGTGACTAACGTCAGTGACCCA"
        ),
        "features": [
            {"name": "pUC ori", "type": "rep_origin", "start": 10, "end": 400, "role": "Origin of replication (high copy number)"},
            {"name": "AmpR promoter", "type": "promoter", "start": 450, "end": 520, "role": "Promoter driving ampicillin resistance gene"},
            {"name": "AmpR CDS", "type": "CDS", "start": 521, "end": 1100, "role": "Ampicillin resistance gene (beta-lactamase)"},
            {"name": "AmpR terminator", "type": "terminator", "start": 1101, "end": 1250, "role": "Terminator for ampicillin resistance gene"}
        ]
    },
    "p15A (Medium copy, KanR)": {
        "sequence": (
            "GTTCTGCCTCTGTGCCTGAAACCGCAAACCGCCTCTCCCCGCGCGTTGGCCGATTCATTAATGCAGCTGGCACGACAGGTTTCCCGACTGGAAAGCGGGC"
            "AGTGAGCGCAACGCAATTAATGTGAGTTAGCTCACTCATTAGGCACCCCAGGCTTTACACTTTATGCTTCCGGCTCGTATGTTGTGTGGAATTGTGAGCG"
            "GATAACAATTTCACACAGGAAACAGCTATGACCATGATTACGCCAAGCTTGATGGGTCAGCAACCACCGTGGGCTCGCTGAGCACCACCACCACCACCAC"
            "CACTGAGATCCGGCTGCTAACAAAGCCCGAAAGGAAGCTGAGTTGGCTGCTGCCACCGCTGAGCAATAACTAGCATAACCCCTTGGGGCCTCTAAACGGG"
            "TCTTGAGGGGTTTTTTGCTGAAAGGAGGAACTATATCCGGATATCCACAGGACGGGTGTGGTCGCCATGATCGCGTAGTCGATAGTGGCTCCAAGAGCCT"
            "GCGAAGTGATGCGTGAGGGTGACCTAAGCATTACATTATTATGCAATGTGAGTTAACTCACCTTAGACCACTTTTCAATATCATGCGTGGTGTGCACAAA"
            "TGGCAGTGACTAACGTCAGTGACCCAAGTCACTTAGCACTTGACCTAAGCATTAGCGACTAACGTCAGTGACCCAAGTCACTTAGCACTTGACCTAAGCA"
            "TTAGCGACTAACGACGGGTGTGGTCGCCATGATCGCGTAGTCGATAGTGGCTCCAAGAGCCTGCGAAGTGATGCGTGAGGGTGACCTAAGCATTACATTA"
            "TTATGCAATGTGAGTTAACTCACCTTAGACCACTTTTCAATATCATGCGTGGTGTGCACAAATGGCAGTGACTAACGTCAGTGACCCAAGTCACTTAGCA"
            "CTTGACCTAAGCATTAGCGACTAACGTCAGTGACCCAAGTCACTTAGCACTTGACCTAAGCATTAGCGACTAACGACGGGTGTGGTCGCCATGATCGCGT"
            "AGTCGATAGTGGCTCCAAGAGCCTGCGAAGTGATGCGTGAGGGTGACCTAAGCATTACATTATTATGCAATGTGAGTTAACTCACCTTAGACCACTTTTC"
            "AATATCATGCGTGGTGTGCACAAATGGCAGTGACTAACGTCAGTGACCCAAGTCACTTAGCACTTGACCTAAGCATTAGCGACTAACGTCAGTGACCCAA"
            "GTCACTTAGCACTTGACCTAAGCATTAGCGACTAACGACGGGTGTGGTCGCCATGATCGCGTAGTCGATAGTGGCTCCAAGAGCCTGCGAAGTGATGCGT"
            "GAGGGTGACCTAAGCATTACATTATTATGCAATGTGAGTTAACTCACCTTAGACCACTTTTCAATATCATGCGTGGTGTGCACAAATGGCAGTGACTAAC"
        ),
        "features": [
            {"name": "p15A ori", "type": "rep_origin", "start": 10, "end": 380, "role": "Origin of replication (medium copy number)"},
            {"name": "KanR promoter", "type": "promoter", "start": 430, "end": 500, "role": "Promoter driving aminoglycoside phosphotransferase"},
            {"name": "KanR CDS", "type": "CDS", "start": 501, "end": 1150, "role": "Kanamycin resistance gene"},
            {"name": "KanR terminator", "type": "terminator", "start": 1151, "end": 1300, "role": "Terminator for kanamycin resistance gene"}
        ]
    }
}

RESTRICTION_SITES = {
    "BsaI": ["GGTCTC", "GAGACC"],
    "BsmBI": ["CGTCTC", "GAGACG"],
    "EcoRI": ["GAATTC"],
    "XbaI": ["TCTAGA"],
    "SpeI": ["ACTAGT"],
    "PstI": ["CTGCAG"],
}

def export_plasmid_genbank(design: DesignIR, backbone_name: str) -> ExportResult:
    # 1. 驗證骨架名稱
    if backbone_name not in BACKBONE_TEMPLATES:
        return ExportResult(
            ok=False,
            format="GenBank (Plasmid)",
            filename=f"{_locus_token(design.design_id)}_plasmid.gb",
            media_type="text/x-genbank",
            content="",
            status="blocked_invalid_backbone",
            errors=[f"Unsupported backbone template: {backbone_name}"],
        )

    part_map = {part.id: part for part in design.parts}

    # 2. 驗證是否遺漏序列
    incomplete = _incomplete_constructs(design, part_map)
    if incomplete:
        errors = [
            f"{construct_id} is missing sequences for: {', '.join(part_ids)}"
            for construct_id, part_ids in incomplete.items()
        ]
        return ExportResult(
            ok=False,
            format="GenBank (Plasmid)",
            filename=f"{_locus_token(design.design_id)}_plasmid.gb",
            media_type="text/x-genbank",
            content="",
            status="blocked_missing_sequences",
            errors=errors,
            warnings=[
                "GenBank export requires a complete sequence for every part in every exported construct."
            ],
        )

    # 3. 驗證序列是否為合法 IUPAC DNA
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
            format="GenBank (Plasmid)",
            filename=f"{_locus_token(design.design_id)}_plasmid.gb",
            media_type="text/x-genbank",
            content="",
            status="blocked_invalid_sequences",
            errors=[
                f"{part_id} contains non-IUPAC DNA characters."
                for part_id in sorted(invalid)
            ],
        )

    if not design.constructs:
        return ExportResult(
            ok=False,
            format="GenBank (Plasmid)",
            filename=f"{_locus_token(design.design_id)}_plasmid.gb",
            media_type="text/x-genbank",
            content="",
            status="blocked_no_constructs",
            errors=["Design has no constructs to export."],
        )

    # 4. 開始拼接質體
    template = BACKBONE_TEMPLATES[backbone_name]
    full_sequence_parts = []
    mapped_features = []
    current_offset = 1

    # 拼接基因盒並做坐標映射
    for i, construct in enumerate(design.constructs):
        # 如果不是第一個基因盒，加入 Linker/Spacer
        if i > 0:
            linker_seq = "TTAATTAAGCGGCCGC"  # PacI / NotI linker
            full_sequence_parts.append(linker_seq)
            mapped_features.append({
                "name": f"Linker_{i}",
                "type": "misc_feature",
                "start": current_offset,
                "end": current_offset + len(linker_seq) - 1,
                "role": "Inter-construct linker spacer",
                "is_circuit_part": False
            })
            current_offset += len(linker_seq)

        for part_id in construct.parts:
            part = part_map[part_id]
            seq = (part.sequence or "").upper()
            part_len = len(seq)
            full_sequence_parts.append(seq)

            mapped_features.append({
                "name": part.name,
                "type": part.part_type,
                "start": current_offset,
                "end": current_offset + part_len - 1,
                "role": part.role,
                "part_id": part.id,
                "source": part.source,
                "confidence": part.confidence,
                "assignment": part.assignment,
                "provenance_ids": part.provenance_ids,
                "is_circuit_part": True
            })
            current_offset += part_len

    # 5. 拼接骨架序列與骨架 Feature
    backbone_seq = template["sequence"].upper()
    backbone_len = len(backbone_seq)
    full_sequence_parts.append(backbone_seq)

    for feat in template["features"]:
        mapped_features.append({
            "name": feat["name"],
            "type": feat["type"],
            "start": feat["start"] + current_offset - 1,
            "end": feat["end"] + current_offset - 1,
            "role": feat["role"],
            "is_circuit_part": False
        })
    current_offset += backbone_len

    full_sequence = "".join(full_sequence_parts)

    # 6. 掃描限制酶切位點衝突
    warnings = []
    for enzyme, sites in RESTRICTION_SITES.items():
        found_positions = []
        for site in sites:
            start_pos = 0
            while True:
                pos = full_sequence.find(site, start_pos)
                if pos == -1:
                    break
                found_positions.append(pos + 1)
                start_pos = pos + 1
        if found_positions:
            warnings.append(
                f"Restriction site conflict: Found {enzyme} site at position(s): {', '.join(map(str, sorted(found_positions)))}"
            )

    # 7. 生成單一環狀 GenBank 格式文字
    content = _generate_genbank_text(design, full_sequence, mapped_features, backbone_name)

    return ExportResult(
        ok=True,
        format="GenBank (Plasmid)",
        filename=f"{_locus_token(design.design_id)}_{_locus_token(design.revision.revision_id)}_plasmid.gb",
        media_type="text/x-genbank",
        content=content,
        status="ready",
        warnings=warnings,
    )


def _generate_genbank_text(
    design: DesignIR,
    sequence: str,
    features: list[dict],
    backbone_name: str
) -> str:
    locus = _locus_token(f"{design.design_id}_plasmid")[:16]
    lines = [
        f"LOCUS       {locus:<16}{len(sequence):>11} bp    DNA     circular   SYN {date.today().strftime('%d-%b-%Y').upper()}",
        f"DEFINITION  Synthetic plasmid containing genetic logic circuit, assembled on {backbone_name}.",
        f"ACCESSION   {locus}",
        f"VERSION     {locus}.{design.revision.revision_number}",
        "KEYWORDS    synthetic biology; genetic circuit; circular plasmid.",
        "SOURCE      synthetic circular DNA vector",
        "  ORGANISM  synthetic circular DNA vector",
        "FEATURES             Location/Qualifiers",
        f"     source          1..{len(sequence)}",
        '                     /organism="synthetic plasmid"',
        f'                     /design_id="{_qualifier(design.design_id)}"',
        f'                     /revision="{_qualifier(design.revision.revision_id)}"',
        f'                     /backbone="{_qualifier(backbone_name)}"',
    ]

    for feat in features:
        feature_type = FEATURE_TYPES.get(feat["type"].lower(), "misc_feature")
        lines.extend(
            [
                f"     {feature_type:<16}{feat['start']}..{feat['end']}",
                f'                     /label="{_qualifier(feat["name"])}"',
                f'                     /note="{_qualifier(feat["role"])}"',
            ]
        )
        if feat.get("is_circuit_part"):
            lines.extend(
                [
                    f'                     /part_id="{_qualifier(feat["part_id"])}"',
                    f'                     /evidence="{_qualifier(feat["confidence"])}"',
                    f'                     /source_library="{_qualifier(feat["source"])}"',
                ]
            )
            assignment = feat.get("assignment")
            if assignment:
                lines.append(
                    f'                     /assigned_part_id="{_qualifier(assignment.part_id)}"'
                )
            for provenance_id in feat.get("provenance_ids", []):
                lines.append(
                    f'                     /provenance="{_qualifier(provenance_id)}"'
                )

    lines.append("ORIGIN")
    lines.extend(_origin_lines(sequence))
    lines.append("//")
    return "\n".join(lines) + "\n"
