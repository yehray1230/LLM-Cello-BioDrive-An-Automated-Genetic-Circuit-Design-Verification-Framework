from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from typing import Any, Mapping

from benchmark_suite.egma_boolean import BooleanNode, parse_boolean_expression


TOPOLOGY_INVARIANT_VERSION = "egma-topology-invariants-v1"
TOPOLOGY_INVARIANTS = (
    "unique_node_ids",
    "unique_edges",
    "edge_endpoints_exist",
    "declared_inputs_exact",
    "one_declared_output",
    "supported_operators_only",
    "gate_arity_valid",
    "acyclic_combinational",
    "all_declared_inputs_reach_output",
    "all_gates_reach_output",
    "output_has_single_driver",
)
TOPOLOGY_INVARIANT_SET = frozenset(TOPOLOGY_INVARIANTS)
_NODE_KINDS = frozenset({"input", "gate", "output"})
_GATE_ARITY = {"NOT": 1, "AND": 2, "OR": 2}


@dataclass(frozen=True)
class TopologyInvariantResult:
    invariant: str
    passed: bool
    reason_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "invariant": self.invariant,
            "passed": self.passed,
            "reason_codes": list(self.reason_codes),
        }


def topology_from_expression(
    expression: str,
    input_symbols: list[str],
    output_symbol: str,
) -> dict[str, Any]:
    """Build the canonical EGMA signal-flow graph for a Boolean expression."""

    ast = parse_boolean_expression(expression)
    nodes: list[dict[str, str]] = [
        {"id": symbol, "kind": "input"} for symbol in input_symbols
    ]
    edges: list[dict[str, str]] = []
    gate_index = 0

    def build(node: BooleanNode) -> str:
        nonlocal gate_index
        if node.kind == "SYMBOL":
            if node.value is None:
                raise ValueError("Symbol node has no value.")
            return node.value
        sources: list[str] = []
        if node.left is not None:
            sources.append(build(node.left))
        if node.right is not None:
            sources.append(build(node.right))
        gate_index += 1
        gate_id = f"g{gate_index:03d}"
        nodes.append({"id": gate_id, "kind": "gate", "operator": node.kind})
        edges.extend({"source": source, "target": gate_id} for source in sources)
        return gate_id

    root = build(ast)
    nodes.append({"id": output_symbol, "kind": "output"})
    edges.append({"source": root, "target": output_symbol})
    return {
        "schema_version": TOPOLOGY_INVARIANT_VERSION,
        "declared_inputs": list(input_symbols),
        "declared_output": output_symbol,
        "nodes": nodes,
        "edges": edges,
    }


def validate_topology(
    topology: Mapping[str, Any],
    required_invariants: list[str] | tuple[str, ...] = TOPOLOGY_INVARIANTS,
) -> dict[str, Any]:
    unknown = sorted(set(required_invariants) - TOPOLOGY_INVARIANT_SET)
    if unknown:
        return {
            "schema_version": TOPOLOGY_INVARIANT_VERSION,
            "passed": False,
            "results": [],
            "reason_codes": [
                f"UNKNOWN_TOPOLOGY_INVARIANT:{name}" for name in unknown
            ],
        }

    nodes = topology.get("nodes")
    edges = topology.get("edges")
    declared_inputs = topology.get("declared_inputs")
    declared_output = topology.get("declared_output")
    structural_errors: list[str] = []
    if not isinstance(nodes, list) or any(not isinstance(node, Mapping) for node in nodes):
        structural_errors.append("NODES_MUST_BE_OBJECT_ARRAY")
        nodes = []
    if not isinstance(edges, list) or any(not isinstance(edge, Mapping) for edge in edges):
        structural_errors.append("EDGES_MUST_BE_OBJECT_ARRAY")
        edges = []
    if (
        not isinstance(declared_inputs, list)
        or any(not isinstance(item, str) for item in declared_inputs)
    ):
        structural_errors.append("DECLARED_INPUTS_MUST_BE_STRING_ARRAY")
        declared_inputs = []
    if not isinstance(declared_output, str) or not declared_output:
        structural_errors.append("DECLARED_OUTPUT_REQUIRED")
        declared_output = ""

    node_ids = [str(node.get("id") or "") for node in nodes]
    node_by_id = {
        str(node.get("id")): node for node in nodes if str(node.get("id") or "")
    }
    edge_pairs = [
        (str(edge.get("source") or ""), str(edge.get("target") or ""))
        for edge in edges
    ]
    incoming: dict[str, list[str]] = {node_id: [] for node_id in node_by_id}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in node_by_id}
    for source, target in edge_pairs:
        if source in outgoing and target in incoming:
            outgoing[source].append(target)
            incoming[target].append(source)

    def result(name: str, passed: bool, *reasons: str) -> TopologyInvariantResult:
        if structural_errors:
            return TopologyInvariantResult(
                name,
                False,
                tuple([*structural_errors, *reasons]),
            )
        return TopologyInvariantResult(name, passed, tuple(reasons if not passed else ()))

    unique_node_ids = bool(node_ids) and all(node_ids) and len(node_ids) == len(
        set(node_ids)
    )
    unique_edges = len(edge_pairs) == len(set(edge_pairs)) and all(
        source and target and source != target for source, target in edge_pairs
    )
    endpoints_exist = all(
        source in node_by_id and target in node_by_id for source, target in edge_pairs
    )
    input_nodes = {
        node_id
        for node_id, node in node_by_id.items()
        if node.get("kind") == "input"
    }
    declared_inputs_exact = (
        len(declared_inputs) in {2, 3}
        and len(declared_inputs) == len(set(declared_inputs))
        and input_nodes == set(declared_inputs)
    )
    output_nodes = [
        node_id
        for node_id, node in node_by_id.items()
        if node.get("kind") == "output"
    ]
    one_declared_output = output_nodes == [declared_output]
    node_kinds_valid = all(node.get("kind") in _NODE_KINDS for node in nodes)
    gate_nodes = {
        node_id: node
        for node_id, node in node_by_id.items()
        if node.get("kind") == "gate"
    }
    supported_operators = node_kinds_valid and all(
        node.get("operator") in _GATE_ARITY for node in gate_nodes.values()
    )
    gate_arity_valid = supported_operators and all(
        len(incoming.get(node_id, [])) == _GATE_ARITY[str(node.get("operator"))]
        for node_id, node in gate_nodes.items()
    )
    acyclic = endpoints_exist and _is_acyclic(node_by_id, edge_pairs)

    reaches_output = _reverse_reachable(declared_output, incoming)
    all_inputs_reach = bool(declared_inputs) and set(declared_inputs).issubset(
        reaches_output
    )
    all_gates_reach = set(gate_nodes).issubset(reaches_output)
    output_single_driver = (
        declared_output in incoming and len(incoming[declared_output]) == 1
    )

    computed = {
        "unique_node_ids": result(
            "unique_node_ids", unique_node_ids, "NODE_IDS_NOT_UNIQUE"
        ),
        "unique_edges": result("unique_edges", unique_edges, "EDGES_NOT_UNIQUE"),
        "edge_endpoints_exist": result(
            "edge_endpoints_exist",
            endpoints_exist,
            "EDGE_ENDPOINT_MISSING",
        ),
        "declared_inputs_exact": result(
            "declared_inputs_exact",
            declared_inputs_exact,
            "DECLARED_INPUT_SET_MISMATCH",
        ),
        "one_declared_output": result(
            "one_declared_output",
            one_declared_output,
            "DECLARED_OUTPUT_MISMATCH",
        ),
        "supported_operators_only": result(
            "supported_operators_only",
            supported_operators,
            "UNSUPPORTED_NODE_KIND_OR_OPERATOR",
        ),
        "gate_arity_valid": result(
            "gate_arity_valid",
            gate_arity_valid,
            "GATE_ARITY_MISMATCH",
        ),
        "acyclic_combinational": result(
            "acyclic_combinational",
            acyclic,
            "COMBINATIONAL_CYCLE_DETECTED",
        ),
        "all_declared_inputs_reach_output": result(
            "all_declared_inputs_reach_output",
            all_inputs_reach,
            "DECLARED_INPUT_NOT_REACHING_OUTPUT",
        ),
        "all_gates_reach_output": result(
            "all_gates_reach_output",
            all_gates_reach,
            "DANGLING_GATE",
        ),
        "output_has_single_driver": result(
            "output_has_single_driver",
            output_single_driver,
            "OUTPUT_DRIVER_COUNT_MISMATCH",
        ),
    }
    selected = [computed[name] for name in required_invariants]
    reasons = sorted(
        {reason for item in selected for reason in item.reason_codes}
    )
    return {
        "schema_version": TOPOLOGY_INVARIANT_VERSION,
        "passed": all(item.passed for item in selected),
        "results": [item.to_dict() for item in selected],
        "reason_codes": reasons,
        "graph_summary": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "input_count": len(input_nodes),
            "gate_count": len(gate_nodes),
            "output_count": len(output_nodes),
            "operator_counts": dict(
                sorted(
                    Counter(
                        str(node.get("operator")) for node in gate_nodes.values()
                    ).items()
                )
            ),
        },
    }


def _is_acyclic(
    node_by_id: Mapping[str, Mapping[str, Any]],
    edge_pairs: list[tuple[str, str]],
) -> bool:
    indegree = {node_id: 0 for node_id in node_by_id}
    outgoing = {node_id: [] for node_id in node_by_id}
    for source, target in edge_pairs:
        if source not in outgoing or target not in indegree:
            return False
        outgoing[source].append(target)
        indegree[target] += 1
    queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    visited = 0
    while queue:
        node_id = queue.popleft()
        visited += 1
        for target in sorted(outgoing[node_id]):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return visited == len(node_by_id)


def _reverse_reachable(
    output: str,
    incoming: Mapping[str, list[str]],
) -> set[str]:
    if output not in incoming:
        return set()
    reachable: set[str] = set()
    stack = [output]
    while stack:
        node_id = stack.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        stack.extend(incoming.get(node_id, []))
    return reachable
