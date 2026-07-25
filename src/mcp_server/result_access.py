"""Canonical read-only accessors for persisted MCP run-result payloads."""

from __future__ import annotations

from typing import Any


def best_topology_from_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return the direct topology or the persisted summary fallback."""
    direct = result.get("best_topology")
    if isinstance(direct, dict) and direct:
        return direct
    summary = result.get("summary", {})
    if isinstance(summary, dict):
        best_topology = summary.get("best_topology")
        if isinstance(best_topology, dict):
            return best_topology
    return {}
