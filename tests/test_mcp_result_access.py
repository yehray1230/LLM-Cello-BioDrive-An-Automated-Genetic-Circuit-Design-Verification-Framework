from __future__ import annotations

from mcp_server.explainer import (
    _best_topology_from_result as explainer_best_topology,
)
from mcp_server.result_access import best_topology_from_result
from mcp_server.service import (
    _best_topology_from_result as service_best_topology,
)


def test_mcp_consumers_share_the_canonical_result_accessor() -> None:
    assert explainer_best_topology is best_topology_from_result
    assert service_best_topology is best_topology_from_result


def test_best_topology_accessor_preserves_direct_precedence_and_identity() -> None:
    direct = {"score": 0.9}
    summary_topology = {"score": 0.4}
    result = {
        "best_topology": direct,
        "summary": {"best_topology": summary_topology},
    }

    assert best_topology_from_result(result) is direct


def test_best_topology_accessor_falls_back_only_to_summary_shape() -> None:
    summary_topology = {"score": 0.4}
    result = {
        "best_topology": {},
        "summary": {"best_topology": summary_topology},
        "data": {"best_topology": {"score": 1.0}},
    }

    assert best_topology_from_result(result) is summary_topology
    assert best_topology_from_result(
        {"data": {"best_topology": {"score": 1.0}}}
    ) == {}
    assert best_topology_from_result({"summary": "invalid"}) == {}
