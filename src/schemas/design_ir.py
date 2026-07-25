from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any

from utils.scalar_values import optional_float as _optional_float, optional_trimmed_text as _optional_string


@dataclass
class ProvenanceRecord:
    id: str
    source_type: str
    source_uri: str | None = None
    source_version: str | None = None
    generated_by: str | None = None
    generated_at: str | None = None
    artifact_manifest_path: str | None = None
    license_expression: str | None = None
    rights_uri: str | None = None
    license_status: str = "unknown"
    attribution_required: bool = False
    permitted_uses: list[str] = field(default_factory=list)
    prohibited_uses: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PartAssignment:
    logic_node_id: str
    part_id: str
    part_name: str
    part_type: str | None = None
    library_id: str | None = None
    sequence: str | None = None
    evidence_source: str | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DesignRevision:
    revision_id: str
    parent_revision_id: str | None = None
    revision_number: int = 1
    created_at: str | None = None
    created_by: str = "system"
    change_type: str = "generated"
    summary: str = "Initial design generation"
    changes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class BiologicalPart:
    id: str
    name: str
    part_type: str
    role: str
    sequence: str | None = None
    source: str = "conceptual"
    confidence: str = "conceptual"
    host_compatibility: list[str] = field(default_factory=list)
    upstream: list[str] = field(default_factory=list)
    downstream: list[str] = field(default_factory=list)
    rationale: str = ""
    sequence_format: str = "DNA"
    provenance_ids: list[str] = field(default_factory=list)
    assignment: PartAssignment | None = None


@dataclass
class RegulatoryInteraction:
    source: str
    target: str
    interaction_type: str
    label: str = ""


@dataclass
class GeneticConstruct:
    id: str
    name: str
    parts: list[str]
    topology: str = "linear"
    backbone: str | None = None
    assembly_method: str | None = None
    validation_status: dict[str, str] = field(default_factory=dict)


@dataclass
class DesignIR:
    design_id: str
    name: str
    inputs: list[str]
    outputs: list[str]
    logic_expression: str
    parts: list[BiologicalPart]
    interactions: list[RegulatoryInteraction]
    constructs: list[GeneticConstruct]
    validation_status: dict[str, str]
    warnings: list[str] = field(default_factory=list)
    provenance: list[ProvenanceRecord] = field(default_factory=list)
    assignments: list[PartAssignment] = field(default_factory=list)
    revision: DesignRevision = field(
        default_factory=lambda: DesignRevision(revision_id="revision_1")
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def design_ir_from_dict(payload: dict[str, Any]) -> DesignIR:
    assignments = [
        PartAssignment(**item)
        for item in payload.get("assignments", [])
        if isinstance(item, dict)
    ]
    provenance = [
        ProvenanceRecord(**item)
        for item in payload.get("provenance", [])
        if isinstance(item, dict)
    ]
    parts = []
    for item in payload.get("parts", []):
        if not isinstance(item, dict):
            continue
        part_payload = dict(item)
        assignment = part_payload.get("assignment")
        if isinstance(assignment, dict):
            part_payload["assignment"] = PartAssignment(**assignment)
        parts.append(BiologicalPart(**part_payload))
    return DesignIR(
        design_id=str(payload.get("design_id", "candidate")),
        name=str(payload.get("name", "Genetic circuit design")),
        inputs=list(payload.get("inputs", [])),
        outputs=list(payload.get("outputs", [])),
        logic_expression=str(payload.get("logic_expression", "")),
        parts=parts,
        interactions=[
            RegulatoryInteraction(**item)
            for item in payload.get("interactions", [])
            if isinstance(item, dict)
        ],
        constructs=[
            GeneticConstruct(**item)
            for item in payload.get("constructs", [])
            if isinstance(item, dict)
        ],
        validation_status=dict(payload.get("validation_status", {})),
        warnings=list(payload.get("warnings", [])),
        provenance=provenance,
        assignments=assignments,
        revision=DesignRevision(**payload.get("revision", {"revision_id": "revision_1"})),
    )


def topology_to_design_ir(
    topology: dict[str, Any],
    *,
    host_organism: str = "Escherichia coli",
    design_id: str = "candidate",
    user_intent: str = "",
) -> DesignIR:
    verilog = str(topology.get("verilog", "") or "")
    code = _strip_comments(verilog)
    inputs, outputs, _ = _extract_signals(code)
    logic_assignments = _extract_assignments(code)
    gates = _extract_primitive_gates(code)
    gates.extend(_extract_assignment_gates(logic_assignments))
    logic_expression = _logic_summary(logic_assignments, gates, outputs)

    parts: list[BiologicalPart] = []
    interactions: list[RegulatoryInteraction] = []
    constructs: list[GeneticConstruct] = []
    part_assignments = _part_assignments_from_topology(topology)
    provenance = _provenance_from_topology(topology)
    revision = _revision_from_topology(topology)

    for signal in sorted(inputs):
        sensor_id = f"sensor_{signal}"
        promoter_id = f"promoter_{signal}"
        parts.extend(
            [
                BiologicalPart(
                    id=sensor_id,
                    name=f"{signal} input sensor",
                    part_type="sensor",
                    role=f"Detects the environmental or molecular input {signal}.",
                    host_compatibility=[host_organism],
                    rationale="Created from a Verilog input; the exact sensor must be selected from a characterized library.",
                ),
                BiologicalPart(
                    id=promoter_id,
                    name=f"P_{signal}",
                    part_type="promoter",
                    role=f"Converts input {signal} into a regulatory signal.",
                    host_compatibility=[host_organism],
                    upstream=[sensor_id],
                    rationale="Conceptual input promoter generated from the logic interface.",
                ),
            ]
        )
        interactions.append(
            RegulatoryInteraction(sensor_id, promoter_id, "activation", f"{signal} activates its input promoter")
        )

    gate_outputs: dict[str, str] = {}
    for index, gate in enumerate(gates, start=1):
        gate_type, output_signal, gate_inputs = gate
        regulator_id = f"regulator_{index}_{output_signal}"
        promoter_id = f"logic_promoter_{index}_{output_signal}"
        gate_outputs[output_signal] = regulator_id
        parts.extend(
            [
                BiologicalPart(
                    id=promoter_id,
                    name=f"P_logic_{index}",
                    part_type="promoter",
                    role=f"Implements the {gate_type.upper()} decision for {', '.join(gate_inputs)}.",
                    host_compatibility=[host_organism],
                    rationale="A conceptual regulatory promoter representing a Boolean gate.",
                ),
                BiologicalPart(
                    id=f"rbs_{regulator_id}",
                    name=f"RBS_{index}",
                    part_type="RBS",
                    role="Controls translation initiation for the gate regulator.",
                    host_compatibility=[host_organism],
                    rationale="Placeholder RBS; strength has not been selected.",
                ),
                BiologicalPart(
                    id=regulator_id,
                    name=f"{gate_type.upper()} regulator {index}",
                    part_type="CDS",
                    role=f"Carries the regulatory output signal {output_signal}.",
                    host_compatibility=[host_organism],
                    rationale="Conceptual regulator CDS; no characterized sequence is assigned.",
                ),
                BiologicalPart(
                    id=f"term_{regulator_id}",
                    name=f"T_{index}",
                    part_type="terminator",
                    role="Terminates transcription of the gate transcriptional unit.",
                    host_compatibility=[host_organism],
                    rationale="Placeholder terminator for construct visualization.",
                ),
            ]
        )
        for gate_input in gate_inputs:
            source = gate_outputs.get(gate_input, f"promoter_{gate_input}")
            interaction = "repression" if gate_type.lower() in {"not", "nand", "nor"} else "activation"
            interactions.append(
                RegulatoryInteraction(source, promoter_id, interaction, f"{gate_input} enters {gate_type.upper()}")
            )
        interactions.append(
            RegulatoryInteraction(promoter_id, regulator_id, "expression", f"Produces {output_signal}")
        )
        constructs.append(
            GeneticConstruct(
                id=f"tu_gate_{index}",
                name=f"{gate_type.upper()} gate transcriptional unit",
                parts=[promoter_id, f"rbs_{regulator_id}", regulator_id, f"term_{regulator_id}"],
                validation_status=_construct_validation_status(topology),
            )
        )

    for index, output in enumerate(sorted(outputs), start=1):
        promoter_id = f"output_promoter_{output}"
        cds_id = f"output_cds_{output}"
        source_signal = _output_source(output, logic_assignments, gates)
        source_part = gate_outputs.get(source_signal, f"promoter_{source_signal}")
        parts.extend(
            [
                BiologicalPart(
                    id=promoter_id,
                    name=f"P_{output}",
                    part_type="promoter",
                    role=f"Drives the final output {output}.",
                    host_compatibility=[host_organism],
                    rationale="Conceptual output promoter derived from the Verilog output.",
                ),
                BiologicalPart(
                    id=f"rbs_{output}",
                    name=f"RBS_{output}",
                    part_type="RBS",
                    role=f"Controls translation of output {output}.",
                    host_compatibility=[host_organism],
                    rationale="Placeholder RBS; strength has not been selected.",
                ),
                BiologicalPart(
                    id=cds_id,
                    name=output,
                    part_type="CDS",
                    role=f"Produces the requested output signal {output}.",
                    host_compatibility=[host_organism],
                    rationale="Output CDS identity or sequence must be confirmed by the user.",
                ),
                BiologicalPart(
                    id=f"term_{output}",
                    name=f"T_{output}",
                    part_type="terminator",
                    role="Terminates the output transcriptional unit.",
                    host_compatibility=[host_organism],
                    rationale="Placeholder terminator for construct visualization.",
                ),
            ]
        )
        interactions.extend(
            [
                RegulatoryInteraction(source_part, promoter_id, "activation", f"Logic controls {output}"),
                RegulatoryInteraction(promoter_id, cds_id, "expression", f"Expresses {output}"),
            ]
        )
        constructs.append(
            GeneticConstruct(
                id=f"tu_output_{index}",
                name=f"{output} output transcriptional unit",
                parts=[promoter_id, f"rbs_{output}", cds_id, f"term_{output}"],
                validation_status=_construct_validation_status(topology),
            )
        )

    _link_part_neighbors(parts, constructs)
    _apply_part_assignments(parts, part_assignments, provenance)
    from utils.safety_checker import check_safety
    safety_result = check_safety(user_intent or str(topology.get("user_intent", "")), host_organism)

    return DesignIR(
        design_id=design_id,
        name=f"Conceptual design for {', '.join(sorted(outputs)) or 'circuit output'}",
        inputs=sorted(inputs),
        outputs=sorted(outputs),
        logic_expression=logic_expression,
        parts=parts,
        interactions=interactions,
        constructs=constructs,
        validation_status=_design_validation_status(topology, parts),
        warnings=_design_warnings(topology, parts) + safety_result.warnings,
        provenance=provenance,
        assignments=part_assignments,
        revision=revision,
    )


def _part_assignments_from_topology(topology: dict[str, Any]) -> list[PartAssignment]:
    raw_assignments = topology.get("part_assignments", topology.get("assignments", []))
    if not isinstance(raw_assignments, list):
        return []
    assignments: list[PartAssignment] = []
    for index, raw in enumerate(raw_assignments):
        if not isinstance(raw, dict):
            continue
        logic_node_id = str(
            raw.get("logic_node_id")
            or raw.get("node_id")
            or raw.get("gate_id")
            or ""
        ).strip()
        part_id = str(raw.get("part_id") or raw.get("id") or "").strip()
        part_name = str(raw.get("part_name") or raw.get("name") or part_id).strip()
        if not logic_node_id or not part_id:
            continue
        confidence = _optional_float(raw.get("confidence", raw.get("score")))
        known_keys = {
            "logic_node_id", "node_id", "gate_id", "part_id", "id", "part_name",
            "name", "part_type", "type", "library_id", "library", "sequence",
            "evidence_source", "source", "confidence", "score",
        }
        assignments.append(
            PartAssignment(
                logic_node_id=logic_node_id,
                part_id=part_id,
                part_name=part_name or f"Assigned part {index + 1}",
                part_type=_optional_string(raw.get("part_type", raw.get("type"))),
                library_id=_optional_string(raw.get("library_id", raw.get("library"))),
                sequence=_normalized_sequence(raw.get("sequence")),
                evidence_source=_optional_string(raw.get("evidence_source", raw.get("source"))),
                confidence=confidence,
                metadata={key: value for key, value in raw.items() if key not in known_keys},
            )
        )
    return assignments


def _provenance_from_topology(topology: dict[str, Any]) -> list[ProvenanceRecord]:
    manifest = topology.get("cello_artifact_manifest")
    manifest_path = topology.get("cello_artifact_manifest_path")
    source = str(topology.get("source", "design_ir_converter"))
    metadata = {
        "cello_mode": topology.get("cello_mode"),
        "mapping_status": topology.get("mapping_status"),
        "ucf_path": topology.get("ucf_path"),
    }
    if isinstance(manifest, dict):
        metadata["artifact_count"] = len(manifest.get("files", []))
        generated_at = _optional_string(manifest.get("created_at"))
        run_id = str(manifest.get("run_id", "cello_run"))
    else:
        generated_at = None
        run_id = "topology_source"
    return [
        ProvenanceRecord(
            id=f"provenance_{run_id}",
            source_type="cello_artifact" if manifest_path or manifest else "computational_design",
            source_uri=_optional_string(topology.get("source_uri")),
            source_version=_optional_string(topology.get("cello_version")),
            generated_by=source,
            generated_at=generated_at,
            artifact_manifest_path=_optional_string(manifest_path),
            metadata={key: value for key, value in metadata.items() if value is not None},
        )
    ]


def _revision_from_topology(topology: dict[str, Any]) -> DesignRevision:
    raw = topology.get("design_revision")
    if not isinstance(raw, dict):
        raw = {}
    revision_number = raw.get("revision_number", 1)
    try:
        revision_number = max(1, int(revision_number))
    except (TypeError, ValueError):
        revision_number = 1
    return DesignRevision(
        revision_id=str(raw.get("revision_id") or f"revision_{revision_number}"),
        parent_revision_id=_optional_string(raw.get("parent_revision_id")),
        revision_number=revision_number,
        created_at=_optional_string(raw.get("created_at")),
        created_by=str(raw.get("created_by") or "system"),
        change_type=str(raw.get("change_type") or "generated"),
        summary=str(raw.get("summary") or "Initial design generation"),
        changes=list(raw.get("changes", [])) if isinstance(raw.get("changes", []), list) else [],
    )


def _apply_part_assignments(
    parts: list[BiologicalPart],
    assignments: list[PartAssignment],
    provenance: list[ProvenanceRecord],
) -> None:
    part_map = {part.id: part for part in parts}
    provenance_ids = [record.id for record in provenance]
    for assignment in assignments:
        part = part_map.get(assignment.logic_node_id)
        if part is None:
            continue
        part.name = assignment.part_name
        part.source = assignment.library_id or assignment.evidence_source or "external_assignment"
        part.confidence = (
            f"{assignment.confidence:.3f}"
            if assignment.confidence is not None
            else "externally_mapped"
        )
        part.sequence = assignment.sequence
        part.provenance_ids = list(provenance_ids)
        part.assignment = assignment
        if assignment.part_type:
            part.part_type = assignment.part_type
        part.rationale = (
            f"Mapped from logic node {assignment.logic_node_id} to library part "
            f"{assignment.part_id}."
        )


def _normalized_sequence(value: Any) -> str | None:
    if value is None:
        return None
    sequence = re.sub(r"\s+", "", str(value)).upper()
    return sequence or None


def _strip_comments(verilog: str) -> str:
    return re.sub(r"//.*", "", re.sub(r"/\*.*?\*/", "", verilog, flags=re.DOTALL))


def _extract_signals(code: str) -> tuple[set[str], set[str], set[str]]:
    found: dict[str, set[str]] = {"input": set(), "output": set(), "wire": set()}
    for keyword in found:
        for match in re.finditer(rf"\b{keyword}\b\s*(?:\[[^\]]+\]\s*)?([^;);]+)", code, re.IGNORECASE):
            segment = re.split(r"\b(?:input|output|wire|module|endmodule)\b", match.group(1), flags=re.IGNORECASE)[0]
            for raw_name in segment.split(","):
                name = _signal_name(raw_name)
                if name:
                    found[keyword].add(name)
        for match in re.finditer(rf"\b{keyword}\b\s*(?:\[[^\]]+\]\s*)?([A-Za-z_]\w*)", code, re.IGNORECASE):
            found[keyword].add(match.group(1))
    return found["input"], found["output"], found["wire"]


def _extract_assignments(code: str) -> list[tuple[str, str]]:
    return [
        (_signal_name(lhs), rhs.strip())
        for lhs, rhs in re.findall(r"\bassign\s+([^=;]+?)\s*=\s*([^;]+?)\s*;", code, re.IGNORECASE | re.DOTALL)
        if _signal_name(lhs)
    ]


def _extract_primitive_gates(code: str) -> list[tuple[str, str, list[str]]]:
    gates: list[tuple[str, str, list[str]]] = []
    pattern = r"\b(and|or|not|nand|nor|xor|xnor)\s*\(([^;]+?)\)\s*;"
    for gate, body in re.findall(pattern, code, re.IGNORECASE | re.DOTALL):
        signals = [_signal_name(item) for item in body.split(",")]
        signals = [item for item in signals if item]
        if len(signals) >= 2:
            gates.append((gate.lower(), signals[0], signals[1:]))
    return gates


def _extract_assignment_gates(
    assignments: list[tuple[str, str]],
) -> list[tuple[str, str, list[str]]]:
    gates: list[tuple[str, str, list[str]]] = []
    counter = [0]
    for output, expression in assignments:
        _expand_expression_gates(expression, output, gates, counter)
    return gates


def _expand_expression_gates(
    expression: str,
    target: str,
    gates: list[tuple[str, str, list[str]]],
    counter: list[int],
) -> str:
    expression = _strip_outer_parentheses(expression.strip())
    for operator, gate_name in (("|", "or"), ("^", "xor"), ("&", "and")):
        parts = _split_top_level(expression, operator)
        if len(parts) > 1:
            inputs = [
                _expression_input_signal(part, gates, counter)
                for part in parts
            ]
            gates.append((gate_name, target, inputs))
            return target
    if expression.startswith(("~", "!")):
        source = _expression_input_signal(expression[1:], gates, counter)
        gates.append(("not", target, [source]))
        return target
    return _signal_name(expression)


def _expression_input_signal(
    expression: str,
    gates: list[tuple[str, str, list[str]]],
    counter: list[int],
) -> str:
    expression = _strip_outer_parentheses(expression.strip())
    if any(operator in expression for operator in ("&", "|", "^")) or expression.startswith(("~", "!")):
        counter[0] += 1
        temporary = f"expr_{counter[0]}"
        return _expand_expression_gates(expression, temporary, gates, counter)
    return _signal_name(expression)


def _split_top_level(expression: str, operator: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for character in expression:
        if character == "(":
            depth += 1
        elif character == ")":
            depth = max(0, depth - 1)
        if character == operator and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    if current:
        parts.append("".join(current).strip())
    return [part for part in parts if part]


def _strip_outer_parentheses(value: str) -> str:
    value = value.strip()
    while value.startswith("(") and value.endswith(")"):
        depth = 0
        wraps_entire_expression = True
        for index, character in enumerate(value):
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0 and index != len(value) - 1:
                    wraps_entire_expression = False
                    break
        if not wraps_entire_expression:
            break
        value = value[1:-1].strip()
    return value


def _signal_name(value: str) -> str:
    value = re.sub(r"\b(?:input|output|wire|reg)\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\[[^\]]+\]", "", value)
    match = re.search(r"[A-Za-z_]\w*", value.strip())
    return match.group(0) if match else ""


def _logic_summary(
    assignments: list[tuple[str, str]],
    gates: list[tuple[str, str, list[str]]],
    outputs: set[str],
) -> str:
    assignment_map = dict(assignments)
    summaries = [f"{output} = {assignment_map[output]}" for output in sorted(outputs) if output in assignment_map]
    if summaries:
        return "; ".join(summaries)
    gate_map = {output: (gate, inputs) for gate, output, inputs in gates}
    for output in sorted(outputs):
        if output in gate_map:
            gate, inputs = gate_map[output]
            summaries.append(f"{output} = {gate.upper()}({', '.join(inputs)})")
    return "; ".join(summaries) or "Logic expression unavailable"


def _output_source(
    output: str,
    assignments: list[tuple[str, str]],
    gates: list[tuple[str, str, list[str]]],
) -> str:
    if any(gate_output == output for _, gate_output, _ in gates):
        return output
    assignment_map = dict(assignments)
    if output in assignment_map:
        tokens = re.findall(r"[A-Za-z_]\w*", assignment_map[output])
        return tokens[-1] if tokens else output
    return output


def _construct_validation_status(topology: dict[str, Any]) -> dict[str, str]:
    mapped = str(topology.get("mapping_status", "")).lower() == "mapped"
    assignments = _part_assignments_from_topology(topology)
    sequence_count = sum(1 for assignment in assignments if assignment.sequence)
    return {
        "part_assignment": "mapped" if mapped else "conceptual",
        "sequence": "available" if assignments and sequence_count == len(assignments) else "partial" if sequence_count else "missing",
        "backbone": "missing",
        "assembly": "not_checked",
    }


def _design_validation_status(
    topology: dict[str, Any],
    parts: list[BiologicalPart],
) -> dict[str, str]:
    mode = str(topology.get("cello_mode", "")).lower()
    mapping = str(topology.get("mapping_status", "")).lower()
    sequence_count = sum(1 for part in parts if part.sequence)
    sequence_status = (
        "complete"
        if parts and sequence_count == len(parts)
        else "partial"
        if sequence_count
        else "missing"
    )
    return {
        "logic": "available" if topology.get("verilog") else "missing",
        "regulatory_model": "conceptual" if parts else "missing",
        "part_mapping": "external_mapping" if mode == "external" and mapping == "mapped" else "conceptual",
        "sequences": sequence_status,
        "assembly_ready": "no",
    }


def _design_warnings(topology: dict[str, Any], parts: list[BiologicalPart]) -> list[str]:
    warnings = []
    if not parts:
        warnings.append("No biological parts could be inferred from the Verilog candidate.")
    if str(topology.get("cello_mode", "")).lower() != "external":
        warnings.append("Parts are conceptual placeholders, not experimentally characterized assignments.")
    elif not _part_assignments_from_topology(topology):
        warnings.append("External Cello completed, but no part assignments have been parsed into DesignIR yet.")
    warnings.extend(
        [
            "DNA sequences, backbone, and cloning junctions have not been selected.",
            "Restriction sites, host-specific constraints, and assembly compatibility have not been checked.",
        ]
    )
    return warnings


def _link_part_neighbors(parts: list[BiologicalPart], constructs: list[GeneticConstruct]) -> None:
    part_map = {part.id: part for part in parts}
    for construct in constructs:
        for index, part_id in enumerate(construct.parts):
            part = part_map.get(part_id)
            if part is None:
                continue
            if index > 0:
                part.upstream.append(construct.parts[index - 1])
            if index + 1 < len(construct.parts):
                part.downstream.append(construct.parts[index + 1])
