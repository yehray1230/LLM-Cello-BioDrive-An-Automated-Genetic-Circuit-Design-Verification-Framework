"""Canonical formatting primitives shared by GenBank exporters."""

from __future__ import annotations

import re
import textwrap

from schemas.design_ir import BiologicalPart, DesignIR


def incomplete_constructs(
    design: DesignIR,
    part_map: dict[str, BiologicalPart],
) -> dict[str, list[str]]:
    """Return missing-sequence part IDs in construct and part order."""
    missing: dict[str, list[str]] = {}
    for construct in design.constructs:
        absent = [
            part_id
            for part_id in construct.parts
            if part_id not in part_map or not part_map[part_id].sequence
        ]
        if absent:
            missing[construct.id] = absent
    return missing


def origin_lines(sequence: str) -> list[str]:
    """Format a sequence as GenBank ORIGIN lines."""
    lines = []
    lower = sequence.lower()
    for start in range(0, len(lower), 60):
        chunk = lower[start : start + 60]
        groups = " ".join(textwrap.wrap(chunk, 10))
        lines.append(f"{start + 1:>9} {groups}")
    return lines


def locus_token(value: str) -> str:
    """Normalize a value for the existing GenBank locus/filename contract."""
    token = re.sub(r"[^A-Za-z0-9_]", "_", value)
    return token.strip("_") or "DESIGN"


def single_line(value: str) -> str:
    """Collapse arbitrary whitespace without changing visible token order."""
    return " ".join(str(value).split())


def qualifier(value: str) -> str:
    """Format the value for the existing quoted GenBank qualifier contract."""
    return single_line(value).replace("\\", "\\\\").replace('"', "'")
