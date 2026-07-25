from __future__ import annotations

import csv
from html import escape
from io import StringIO
import json
from pathlib import Path
from typing import Any


def write_assembly_deliverables(
    output_dir: Path,
    payload: dict[str, Any],
) -> dict[str, dict[str, str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, dict[str, str]] = {}
    artifacts["genbank"] = _write_text(
        output_dir,
        "assembled_plasmid.gb",
        str(payload["assembly"]["genbank"]),
        "application/genbank",
    )
    artifacts["json"] = _write_text(
        output_dir,
        "assembly_package.json",
        json.dumps(payload, indent=2, ensure_ascii=True),
        "application/json",
    )
    artifacts["csv"] = _write_text(
        output_dir,
        "fragments_primers.csv",
        _primer_csv(payload),
        "text/csv",
    )
    artifacts["report"] = _write_text(
        output_dir,
        "assembly_report.md",
        _report_markdown(payload),
        "text/markdown",
    )

    # Generate an academic PDF or explicitly report the print-HTML fallback.
    from exporters.pdf_exporter import export_report_artifact

    pdf_path = output_dir / "assembly_report.pdf"
    report_html = f"<html><body><pre>{escape(_report_markdown(payload))}</pre></body></html>"
    report_result = export_report_artifact(report_html, pdf_path)
    if report_result.generation_status == "failed":
        raise RuntimeError(
            "Report exporter failed without producing PDF or print-HTML fallback "
            f"({report_result.fallback_reason}; {report_result.error_type})."
        )
    artifacts["pdf_report"] = report_result.to_manifest_entry()
    artifacts["opentrons"] = _write_text(
        output_dir,
        "opentrons_protocol.py",
        _opentrons_protocol(payload),
        "text/x-python",
    )
    artifacts["echo"] = _write_text(
        output_dir,
        "echo_transfer_list.csv",
        _echo_transfer_list(payload),
        "text/csv",
    )
    map_path = output_dir / "plasmid_map.png"
    if _write_plasmid_map(str(payload["assembly"]["genbank"]), map_path):
        artifacts["plasmid_map"] = {
            "filename": map_path.name,
            "media_type": "image/png",
        }
    return artifacts


def _primer_csv(payload: dict[str, Any]) -> str:
    stream = StringIO(newline="")
    columns = [
        "fragment_id",
        "fragment_name",
        "source_type",
        "preparation",
        "template_length",
        "product_length",
        "primer_id",
        "direction",
        "sequence",
        "annealing_sequence",
        "adapter_sequence",
        "length",
        "annealing_length",
        "tm_c",
        "gc_percent",
        "hairpin_tm_c",
        "homodimer_tm_c",
        "heterodimer_tm_c",
        "warnings",
    ]
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for fragment in payload["primers"]["fragment_primer_sets"]:
        primers = [
            fragment.get("forward_primer"),
            fragment.get("reverse_primer"),
        ]
        if not any(primers):
            primers = [None]
        for primer in primers:
            primer = primer or {}
            warnings = list(fragment.get("warnings") or [])
            warnings.extend(primer.get("warnings") or [])
            writer.writerow(
                {
                    "fragment_id": fragment["fragment_id"],
                    "fragment_name": fragment["fragment_name"],
                    "source_type": fragment["source_type"],
                    "preparation": fragment["preparation"],
                    "template_length": fragment["template_length"],
                    "product_length": fragment["product_length"],
                    "primer_id": primer.get("primer_id", ""),
                    "direction": primer.get("direction", ""),
                    "sequence": primer.get("sequence", ""),
                    "annealing_sequence": primer.get("annealing_sequence", ""),
                    "adapter_sequence": primer.get("adapter_sequence", ""),
                    "length": primer.get("length", ""),
                    "annealing_length": primer.get("annealing_length", ""),
                    "tm_c": primer.get("tm", ""),
                    "gc_percent": primer.get("gc_percent", ""),
                    "hairpin_tm_c": primer.get("hairpin_tm", ""),
                    "homodimer_tm_c": primer.get("homodimer_tm", ""),
                    "heterodimer_tm_c": fragment.get("heterodimer_tm", ""),
                    "warnings": "; ".join(
                        str(item.get("code") or "")
                        for item in warnings
                        if isinstance(item, dict)
                    ),
                }
            )
    return stream.getvalue()


def _report_markdown(payload: dict[str, Any]) -> str:
    plan = payload["plan"]
    primers = payload["primers"]
    readiness = payload["readiness"]
    lines = [
        f"# Assembly report: {payload['deliverable_id']}",
        "",
        f"- Design: `{plan['design_id']}`",
        f"- Plasmid: `{plan['plasmid_id']}`",
        f"- Backbone: `{plan['backbone_id']}@{plan['backbone_version']}`",
        f"- Method: `{plan['method']}`",
        f"- Plan status: `{plan['status']}`",
        f"- Primer status: `{primers['status']}`",
        f"- Readiness: `{readiness['readiness_status']}`",
        f"- Target length: `{plan['target_length']} bp`",
        f"- Target checksum: `{plan['target_checksum']}`",
        "",
        "## Fragments and primers",
        "",
        "| Fragment | Preparation | Template | Product | Primer pair |",
        "|---|---:|---:|---:|---|",
    ]
    for fragment in primers["fragment_primer_sets"]:
        forward = fragment.get("forward_primer")
        reverse = fragment.get("reverse_primer")
        pair = (
            f"{forward['primer_id']}, {reverse['primer_id']}"
            if forward and reverse
            else "Not required"
        )
        lines.append(
            "| {fragment_name} | {preparation} | {template_length} bp | "
            "{product_length} bp | {pair} |".format(pair=pair, **fragment)
        )
    issues = list(plan.get("issues") or [])
    for fragment in primers["fragment_primer_sets"]:
        issues.extend(fragment.get("warnings") or [])
        for key in ("forward_primer", "reverse_primer"):
            primer = fragment.get(key) or {}
            issues.extend(primer.get("warnings") or [])
    lines.extend(["", "## Warnings and blockers", ""])
    if not issues:
        lines.append("No warnings or blockers.")
    else:
        for issue in issues:
            lines.append(
                f"- **{issue.get('severity', 'warning')} / "
                f"{issue.get('code', 'UNKNOWN')}**: {issue.get('message', '')}"
            )
    lines.extend(
        [
            "",
            "## Tool versions",
            "",
            *[
                f"- {name}: `{tool_version}`"
                for name, tool_version in {
                    **plan.get("tool_versions", {}),
                    **primers.get("tool_versions", {}),
                }.items()
            ],
            "",
            "> Computational planning output. Experimental review and protocol "
            "validation remain required before laboratory use.",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_text(
    output_dir: Path,
    filename: str,
    content: str,
    media_type: str,
) -> dict[str, str]:
    path = output_dir / filename
    path.write_text(content, encoding="utf-8", newline="")
    return {"filename": filename, "media_type": media_type}


def _write_plasmid_map(genbank: str, output_path: Path) -> bool:
    try:
        import matplotlib

        matplotlib.use("Agg")
        from dna_features_viewer import BiopythonTranslator
        from Bio import SeqIO

        record = SeqIO.read(StringIO(genbank), "genbank")
        graphic_record = BiopythonTranslator().translate_record(record)
        axis, _ = graphic_record.plot(figure_width=12)
        axis.figure.savefig(output_path, bbox_inches="tight", dpi=150)
        import matplotlib.pyplot as plt

        plt.close(axis.figure)
        return True
    except Exception:
        # Plasmid-map rendering is optional and must not block core artifacts.
        return False


def _opentrons_protocol(payload: dict[str, Any]) -> str:
    plan = payload.get("plan") or {}
    fragments = plan.get("fragments") or []
    design_id = plan.get("design_id") or "design"
    method = plan.get("method") or "gibson"

    protocol_code = f"""# Opentrons OT-2 Protocol generated for genetic circuit assembly
from opentrons import protocol_api

metadata = {{
    'protocolName': '{method.capitalize()} Assembly Setup for {design_id}',
    'author': 'Antigravity AI Assistant',
    'description': 'Automated PCR/Assembly setup for design {design_id}',
    'apiLevel': '2.14'
}}

def run(protocol: protocol_api.ProtocolContext):
    # Labware
    tiprack_20 = protocol.load_labware('opentrons_96_tiprack_20ul', '1')
    p20 = protocol.load_instrument('p20_single_gen2', 'right', tip_racks=[tiprack_20])

    # Plates
    dna_plate = protocol.load_labware('nest_96_wellplate_100ul_pcr_full_skirt', '2')
    assembly_plate = protocol.load_labware('nest_96_wellplate_100ul_pcr_full_skirt', '3')
    reagents = protocol.load_labware('nest_12_reservoir_15ml', '4')

    # Reagent Locations
    assembly_mix = reagents.wells_by_name()['A1']
    water = reagents.wells_by_name()['A2']

    # DNA fragment transfers (2 uL each into assembly well A1)
"""
    transfer_lines = []
    well_idx = 1
    for i, frag in enumerate(fragments):
        name = frag.get("name") or frag.get("fragment_id") or f"Fragment_{i+1}"
        transfer_lines.append(f"    # Transfer {name}")
        transfer_lines.append("    p20.pick_up_tip()")
        transfer_lines.append(f"    p20.transfer(2.0, dna_plate.wells_by_name()['A{well_idx}'], assembly_plate.wells_by_name()['A1'], new_tip='never')")
        transfer_lines.append("    p20.drop_tip()")
        well_idx += 1

    transfer_lines.append("    # Transfer Assembly Master Mix")
    transfer_lines.append("    p20.pick_up_tip()")
    transfer_lines.append("    p20.transfer(10.0, assembly_mix, assembly_plate.wells_by_name()['A1'], mix_after=(3, 10), new_tip='never')")
    transfer_lines.append("    p20.drop_tip()")

    protocol_code += "\n".join(transfer_lines)
    protocol_code += "\n"
    return protocol_code


def _echo_transfer_list(payload: dict[str, Any]) -> str:
    plan = payload.get("plan") or {}
    fragments = plan.get("fragments") or []
    lines = ["Source Plate Name,Source Well,Destination Plate Name,Destination Well,Transfer Volume"]
    well_idx = 1
    for i, frag in enumerate(fragments):
        lines.append(f"DNA_Plate,A{well_idx},Assembly_Plate,A1,2000")
        well_idx += 1
    lines.append("Reagent_Plate,A1,Assembly_Plate,A1,10000")
    return "\n".join(lines)

