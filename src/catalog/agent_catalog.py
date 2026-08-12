from __future__ import annotations

from dataclasses import asdict, dataclass, field
import ast
import importlib
import json
from pathlib import Path
from typing import Any


CATALOG_SCHEMA_VERSION = "agent-metadata-v1"
DEFAULT_CATALOG_ROOT = (
    Path("src/catalog/agents")
    if Path("src/catalog/agents").exists()
    else Path("catalog/agents")
)
REQUIRED_FIELDS = {
    "id",
    "name",
    "schema_version",
    "module",
    "entrypoint",
    "role",
    "summary",
    "inputs",
    "outputs",
    "claim_level",
    "requires_expert_review",
}
VALID_CLAIM_LEVELS = {
    "planning_only",
    "computational_candidate",
    "cello_compatible_candidate",
    "simulation_screening",
    "workflow_memory",
}


class AgentCatalogError(ValueError):
    """Raised when an agent catalog entry is malformed."""


@dataclass(frozen=True)
class AgentMetadata:
    id: str
    name: str
    schema_version: str
    module: str
    entrypoint: str
    role: str
    summary: str
    inputs: list[str]
    outputs: list[str]
    tools: list[str] = field(default_factory=list)
    downstream_agents: list[str] = field(default_factory=list)
    claim_level: str = "computational_candidate"
    requires_expert_review: bool = True
    risk_notes: list[str] = field(default_factory=list)
    failure_modes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_agent_catalog(root: str | Path = DEFAULT_CATALOG_ROOT) -> list[AgentMetadata]:
    root_path = Path(root)
    if not root_path.exists():
        return []

    entries: list[AgentMetadata] = []
    for metadata_path in sorted(root_path.glob("*/metadata.yaml")):
        payload = _read_metadata_yaml(metadata_path)
        metadata = validate_agent_metadata(payload, metadata_path=metadata_path)
        entries.append(metadata)

    ids = [entry.id for entry in entries]
    duplicates = sorted({agent_id for agent_id in ids if ids.count(agent_id) > 1})
    if duplicates:
        raise AgentCatalogError(f"Duplicate agent ids: {', '.join(duplicates)}")
    return entries


def build_agent_registry(root: str | Path = DEFAULT_CATALOG_ROOT) -> dict[str, Any]:
    entries = load_agent_catalog(root)
    return {
        "schema_version": "agent-registry-v1",
        "agent_count": len(entries),
        "agents": [entry.to_dict() for entry in entries],
    }


def validate_agent_metadata(
    payload: dict[str, Any],
    *,
    metadata_path: str | Path | None = None,
) -> AgentMetadata:
    missing = sorted(REQUIRED_FIELDS - set(payload))
    if missing:
        raise AgentCatalogError(
            f"{_label(metadata_path)} missing required fields: {', '.join(missing)}"
        )

    if payload["schema_version"] != CATALOG_SCHEMA_VERSION:
        raise AgentCatalogError(
            f"{_label(metadata_path)} unsupported schema_version: "
            f"{payload['schema_version']!r}"
        )

    claim_level = str(payload["claim_level"])
    if claim_level not in VALID_CLAIM_LEVELS:
        raise AgentCatalogError(
            f"{_label(metadata_path)} invalid claim_level: {claim_level!r}"
        )

    for field_name in ("inputs", "outputs"):
        if not _is_string_list(payload[field_name]):
            raise AgentCatalogError(
                f"{_label(metadata_path)} {field_name} must be a list of strings"
            )

    for field_name in ("tools", "downstream_agents", "risk_notes", "failure_modes", "tags"):
        if field_name in payload and not _is_string_list(payload[field_name]):
            raise AgentCatalogError(
                f"{_label(metadata_path)} {field_name} must be a list of strings"
            )

    module_name = str(payload["module"])
    entrypoint = str(payload["entrypoint"])
    _validate_entrypoint(module_name, entrypoint, metadata_path)

    return AgentMetadata(
        id=str(payload["id"]),
        name=str(payload["name"]),
        schema_version=str(payload["schema_version"]),
        module=module_name,
        entrypoint=entrypoint,
        role=str(payload["role"]),
        summary=str(payload["summary"]),
        inputs=list(payload["inputs"]),
        outputs=list(payload["outputs"]),
        tools=list(payload.get("tools", [])),
        downstream_agents=list(payload.get("downstream_agents", [])),
        claim_level=claim_level,
        requires_expert_review=_as_bool(payload["requires_expert_review"]),
        risk_notes=list(payload.get("risk_notes", [])),
        failure_modes=list(payload.get("failure_modes", [])),
        tags=list(payload.get("tags", [])),
        source_path=_source_path(metadata_path),
    )


def _read_metadata_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    return _parse_simple_yaml(text, path)


def _parse_simple_yaml(text: str, path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    current_key: str | None = None

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.startswith("  - "):
            if current_key is None:
                raise AgentCatalogError(f"{path}:{line_number} list item without key")
            payload.setdefault(current_key, []).append(_parse_scalar(line[4:].strip()))
            continue
        if line.startswith(" "):
            raise AgentCatalogError(f"{path}:{line_number} unsupported indentation")
        if ":" not in line:
            raise AgentCatalogError(f"{path}:{line_number} expected key: value")

        key, raw_value = line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            raise AgentCatalogError(f"{path}:{line_number} empty key")
        if raw_value == "":
            payload[key] = []
            current_key = key
        else:
            payload[key] = _parse_scalar(raw_value)
            current_key = None

    return payload


def _parse_scalar(value: str) -> Any:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if value.startswith("[") or value.startswith("{"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _validate_entrypoint(
    module_name: str,
    entrypoint: str,
    metadata_path: str | Path | None,
) -> None:
    relative_module_path = Path(*module_name.split(".")).with_suffix(".py")
    repository_root = Path(__file__).resolve().parents[2]
    source_root = repository_root / "src"
    module_candidates = (
        Path.cwd() / relative_module_path,
        repository_root / relative_module_path,
        source_root / relative_module_path,
    )
    for module_path in dict.fromkeys(module_candidates):
        if module_path.is_file():
            _validate_entrypoint_ast(module_path, entrypoint, metadata_path)
            return

    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise AgentCatalogError(
            f"{_label(metadata_path)} cannot import module {module_name!r}: {exc}"
        ) from exc
    if not hasattr(module, entrypoint):
        raise AgentCatalogError(
            f"{_label(metadata_path)} module {module_name!r} has no entrypoint "
            f"{entrypoint!r}"
        )


def _validate_entrypoint_ast(
    module_path: Path,
    entrypoint: str,
    metadata_path: str | Path | None,
) -> None:
    try:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        raise AgentCatalogError(
            f"{_label(metadata_path)} cannot parse module {module_path}: {exc}"
        ) from exc

    names = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
    }
    if entrypoint not in names:
        raise AgentCatalogError(
            f"{_label(metadata_path)} module {module_path} has no entrypoint "
            f"{entrypoint!r}"
        )


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    raise AgentCatalogError(f"Expected boolean value, got {value!r}")


def _label(path: str | Path | None) -> str:
    return str(path) if path else "agent metadata"


def _source_path(path: str | Path | None) -> str | None:
    if path is None:
        return None
    return Path(path).as_posix()
