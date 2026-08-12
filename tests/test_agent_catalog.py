from __future__ import annotations

from pathlib import Path

import catalog.agent_catalog as agent_catalog
from catalog.agent_catalog import build_agent_registry, load_agent_catalog


EXPECTED_AGENT_IDS = {
    "pm-agent",
    "builder-agent",
    "translator-agent",
    "data-miner-agent",
    "critic-agent",
    "consolidator-agent",
    "skill-extractor-agent",
}


def test_agent_catalog_loads_registered_agents() -> None:
    entries = load_agent_catalog()
    ids = {entry.id for entry in entries}

    assert ids == EXPECTED_AGENT_IDS
    assert all(entry.schema_version == "agent-metadata-v1" for entry in entries)
    assert all(entry.inputs for entry in entries)
    assert all(entry.outputs for entry in entries)
    assert all(entry.requires_expert_review for entry in entries)


def test_agent_registry_is_ui_ready() -> None:
    registry = build_agent_registry()

    assert registry["schema_version"] == "agent-registry-v1"
    assert registry["agent_count"] == len(EXPECTED_AGENT_IDS)
    assert [agent["id"] for agent in registry["agents"]] == sorted(EXPECTED_AGENT_IDS)


def test_agent_registry_validates_local_entrypoints_without_runtime_import(
    monkeypatch,
) -> None:
    def reject_runtime_import(module_name: str):
        raise AssertionError(f"unexpected runtime import: {module_name}")

    monkeypatch.setattr(agent_catalog.importlib, "import_module", reject_runtime_import)

    registry = build_agent_registry()

    assert registry["agent_count"] == len(EXPECTED_AGENT_IDS)


def test_every_runtime_agent_has_catalog_metadata() -> None:
    agents_dir = Path(__file__).resolve().parent.parent / "src" / "agents"
    runtime_agent_files = {
        path.stem.replace("_", "-")
        for path in agents_dir.glob("*_agent.py")
    }
    registered_modules = {
        entry.module.rsplit(".", 1)[-1].replace("_", "-")
        for entry in load_agent_catalog()
    }

    assert runtime_agent_files == registered_modules
