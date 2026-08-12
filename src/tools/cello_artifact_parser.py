from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from tools.part_library import PartLibrary
from utils.scalar_values import optional_float as _optional_float


@dataclass
class CelloParseResult:
    parser: str
    parser_version: str
    source_files: list[str] = field(default_factory=list)
    assignments: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    assignment_provenance: str | None = None


class CelloV2JsonParser:
    """Parse Cello v2 JSON circuit/assignment artifacts into DesignIR assignments."""

    name = "cello_v2_json"
    version = "1.0"
    filename_tokens = ("assignment", "logic_circuit", "circuit", "netlist")

    def __init__(self, part_library: PartLibrary | None = None):
        self.part_library = part_library or PartLibrary.demo()

    def parse_directory(
        self,
        artifact_dir: str | Path,
        *,
        placements_only: bool = False,
    ) -> CelloParseResult:
        root = Path(artifact_dir)
        result = CelloParseResult(parser=self.name, parser_version=self.version)
        if not root.exists():
            result.warnings.append(f"Cello artifact directory does not exist: {root}")
            return result

        candidates = [
            path
            for path in root.rglob("*.json")
            if path.name != "artifact_manifest.json"
            and any(token in path.name.lower() for token in self.filename_tokens)
        ]
        for path in sorted(candidates):
            try:
                payload, normalization = load_cello_json_payload(path)
            except (OSError, json.JSONDecodeError) as exc:
                result.warnings.append(f"Could not parse {path.name}: {exc}")
                continue
            if normalization != "strict_json":
                result.warnings.append(
                    f"{path.name}: {normalization}; raw artifact preserved unchanged"
                )
            parsed = _parse_payload(
                payload,
                source_file=path,
                part_library=self.part_library,
                placements_only=placements_only,
            )
            if parsed:
                result.source_files.append(str(path.resolve()))
                result.assignments.extend(parsed)

        result.assignments = _deduplicate_assignments(result.assignments)
        if not result.assignments:
            result.warnings.append(
                "No supported Cello v2 gate assignments were found in JSON artifacts."
            )
        elif placements_only:
            result.assignment_provenance = "output_netlist_placements"
        return result


def load_cello_json_payload(path: str | Path) -> tuple[Any, str]:
    raw = Path(path).read_text(encoding="utf-8")
    try:
        return json.loads(raw), "strict_json"
    except json.JSONDecodeError:
        normalized = strip_json_trailing_commas(raw)
        return json.loads(normalized), "cello_trailing_commas_removed"


def strip_json_trailing_commas(text: str) -> str:
    """Remove only commas immediately before ] or } while preserving strings."""
    output: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "]}":
                index += 1
                continue
        output.append(char)
        index += 1
    return "".join(output)


def _parse_payload(
    payload: Any,
    *,
    source_file: Path,
    part_library: PartLibrary,
    placements_only: bool = False,
) -> list[dict[str, Any]]:
    records = _assignment_records(payload, placements_only=placements_only)
    assignments: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        logic_node_id = _logic_node_id(record, index)
        gate_type = str(
            record.get("gate_type") or record.get("type") or record.get("logic") or ""
        ).upper()
        part_refs = _part_references(record)
        if not part_refs:
            direct_id = (
                record.get("part_id") or record.get("group") or record.get("name")
            )
            if direct_id:
                part_refs = [{"part_id": direct_id}]

        for part_index, part_ref in enumerate(part_refs, start=1):
            part_id = str(
                part_ref.get("part_id")
                or part_ref.get("id")
                or part_ref.get("name")
                or ""
            ).strip()
            if not part_id:
                continue
            library_part = part_library.get(part_id)
            assignments.append(
                {
                    "logic_node_id": _part_logic_node_id(
                        logic_node_id,
                        part_ref,
                        part_index,
                    ),
                    "part_id": part_id,
                    "part_name": (
                        library_part.name
                        if library_part
                        else str(
                            part_ref.get("part_name") or part_ref.get("name") or part_id
                        )
                    ),
                    "part_type": (
                        library_part.part_type
                        if library_part
                        else part_ref.get("part_type") or part_ref.get("type")
                    ),
                    "library_id": part_library.library_id,
                    "library_version": part_library.version,
                    "sequence": library_part.sequence
                    if library_part
                    else part_ref.get("sequence"),
                    "sequence_status": (
                        library_part.sequence_status
                        if library_part
                        else "artifact_supplied"
                    ),
                    "evidence_source": str(source_file.resolve()),
                    "confidence": _optional_float(
                        part_ref.get("confidence", record.get("score"))
                    ),
                    "gate_type": gate_type or None,
                    "raw_gate_name": record.get("name") or record.get("gate_name"),
                    "assignment_provenance": (
                        "output_netlist_placements" if placements_only else None
                    ),
                }
            )
    return assignments


def _assignment_records(
    payload: Any,
    *,
    placements_only: bool = False,
) -> list[dict[str, Any]]:
    if placements_only:
        if not isinstance(payload, dict) or "placements" not in payload:
            return []
        return _placement_assignment_records(payload.get("placements"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    if "placements" in payload:
        return _placement_assignment_records(payload.get("placements"))
    for key in ("assignments", "logic_gates", "gates", "nodes"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    for key in ("circuit", "logic_circuit", "netlist", "design"):
        nested = payload.get(key)
        records = _assignment_records(nested)
        if records:
            return records
    if any(
        key in payload for key in ("part_id", "group", "gate_name", "logic_node_id")
    ):
        return [payload]
    return []


def _placement_assignment_records(placements: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        node = str(value.get("node") or "").strip()
        parts = value.get("parts")
        if node and isinstance(parts, list) and parts:
            records.append(
                {
                    "logic_node_id": node,
                    "name": value.get("name"),
                    "parts": [
                        (
                            {**part, "logic_node_id": node}
                            if isinstance(part, dict)
                            else {"part_id": str(part), "logic_node_id": node}
                        )
                        for part in parts
                    ],
                }
            )
        for nested in value.values():
            if isinstance(nested, (dict, list)):
                visit(nested)

    visit(placements)
    return records


def _part_references(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw_parts = record.get("parts", record.get("components", []))
    if isinstance(raw_parts, list):
        return [
            item if isinstance(item, dict) else {"part_id": str(item)}
            for item in raw_parts
        ]
    if isinstance(raw_parts, dict):
        return [
            value | {"role": key}
            if isinstance(value, dict)
            else {"part_id": value, "role": key}
            for key, value in raw_parts.items()
        ]
    return []


def _logic_node_id(record: dict[str, Any], index: int) -> str:
    explicit = record.get("logic_node_id") or record.get("node_id")
    if explicit:
        return str(explicit)
    gate_index = record.get("gate_index", record.get("index", index))
    output = str(record.get("output") or record.get("output_signal") or "").strip()
    part_role = str(record.get("part_role") or "").strip()
    if part_role and output:
        return f"{part_role}_{gate_index}_{output}"
    if output:
        return f"regulator_{gate_index}_{output}"
    return f"regulator_{gate_index}_gate"


def _part_logic_node_id(
    base_id: str,
    part_ref: dict[str, Any],
    part_index: int,
) -> str:
    explicit = part_ref.get("logic_node_id") or part_ref.get("node_id")
    if explicit:
        return str(explicit)
    role = str(part_ref.get("role") or part_ref.get("part_type") or "").lower()
    if role in {"promoter", "rbs", "cds", "terminator"}:
        if base_id.startswith("regulator_"):
            suffix = base_id.removeprefix("regulator_")
            return {
                "promoter": f"logic_promoter_{suffix}",
                "rbs": f"rbs_{base_id}",
                "cds": base_id,
                "terminator": f"term_{base_id}",
            }[role]
    return base_id if part_index == 1 else f"{base_id}_part_{part_index}"


def _deduplicate_assignments(assignments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for assignment in assignments:
        key = (str(assignment.get("logic_node_id")), str(assignment.get("part_id")))
        selected[key] = assignment
    return list(selected.values())


def parse_ucf_gate_parameters(
    ucf_path: str | Path | None,
) -> dict[str, dict[str, float]]:
    if not ucf_path:
        return {}
    path = Path(ucf_path)
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, list):
        return {}
    gate_params = {}
    for obj in payload:
        if not isinstance(obj, dict) or obj.get("collection") != "gates":
            continue
        gate_name = obj.get("name")
        regulator = obj.get("regulator")
        promoter = obj.get("promoter")
        response = obj.get("response_function", {})
        if isinstance(response, dict):
            params = response.get("parameters", {})
            if isinstance(params, dict):
                ymin = params.get("ymin")
                ymax = params.get("ymax")
                K = params.get("K")
                n = params.get("n")
                entry = {
                    "ymin": float(ymin) if ymin is not None else None,
                    "ymax": float(ymax) if ymax is not None else None,
                    "K": float(K) if K is not None else None,
                    "n": float(n) if n is not None else None,
                }
                if gate_name:
                    gate_params[str(gate_name)] = entry
                if regulator:
                    gate_params[str(regulator)] = entry
                if promoter:
                    gate_params[str(promoter)] = entry
    return gate_params
