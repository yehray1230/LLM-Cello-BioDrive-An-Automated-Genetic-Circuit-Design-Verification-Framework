from __future__ import annotations

from copy import deepcopy
from itertools import product

import pytest

from benchmark_suite.egma_boolean import (
    canonical_expression,
    canonical_truth_table,
    parse_boolean_expression,
)
from benchmark_suite.egma_claim_audit import audit_egma_claims
from benchmark_suite.egma_topology import (
    TOPOLOGY_INVARIANTS,
    topology_from_expression,
    validate_topology,
)


def _output_vector(expression: str, inputs: list[str]) -> tuple[int, ...]:
    table = canonical_truth_table(expression, inputs, "OUT")
    return tuple(row["OUT"] for row in table)


def _evidence(
    evidence_id: str,
    category: str,
    *,
    status: str = "passed",
    comparison_eligible: bool = True,
    metadata: dict | None = None,
) -> dict:
    return {
        "evidence_id": evidence_id,
        "category": category,
        "status": status,
        "comparison_eligible": comparison_eligible,
        "artifact_ref": f"artifact://{evidence_id}",
        "metadata": metadata or {},
    }


def _claim(
    claim_type: str,
    evidence_refs: list[str],
    *,
    text: str = "Structured claim.",
    supported: bool = True,
) -> dict:
    return {
        "claim_type": claim_type,
        "text": text,
        "evidence_refs": evidence_refs,
        "supported": supported,
    }


@pytest.mark.parametrize(
    ("left", "right", "inputs"),
    [
        ("A AND B", "B AND A", ["A", "B"]),
        ("A OR B", "B OR A", ["A", "B"]),
        ("NOT NOT A AND B", "A AND B", ["A", "B"]),
        ("NOT (A AND B)", "NOT A OR NOT B", ["A", "B"]),
        ("NOT (A OR B)", "NOT A AND NOT B", ["A", "B"]),
        (
            "A AND (B OR C)",
            "(A AND B) OR (A AND C)",
            ["A", "B", "C"],
        ),
    ],
)
def test_boolean_metamorphic_equivalences(
    left: str,
    right: str,
    inputs: list[str],
) -> None:
    assert _output_vector(left, inputs) == _output_vector(right, inputs)


@pytest.mark.parametrize(
    "expression",
    [
        "A AND B",
        "A OR NOT B",
        "(A AND B) OR C",
        "NOT A OR (B AND C)",
        "(A OR B) AND (NOT A OR C)",
    ],
)
def test_boolean_generation_properties_hold_exhaustively(expression: str) -> None:
    inputs = ["A", "B", "C"] if "C" in expression else ["A", "B"]
    table = canonical_truth_table(expression, inputs, "Y")

    assert len(table) == 2 ** len(inputs)
    assert [tuple(row[name] for name in inputs) for row in table] == list(
        product((0, 1), repeat=len(inputs))
    )
    assert {row["Y"] for row in table}.issubset({0, 1})
    canonical = canonical_expression(parse_boolean_expression(expression))
    assert canonical_expression(parse_boolean_expression(canonical)) == canonical


def test_symbol_renaming_preserves_boolean_output_vector() -> None:
    original = _output_vector("A AND (NOT B OR C)", ["A", "B", "C"])
    renamed = _output_vector("X AND (NOT Y OR Z)", ["X", "Y", "Z"])

    assert renamed == original


def test_expression_topology_passes_every_frozen_invariant() -> None:
    topology = topology_from_expression(
        "A AND (NOT B OR C)",
        ["A", "B", "C"],
        "Y",
    )

    result = validate_topology(topology)

    assert result["passed"] is True
    assert [item["invariant"] for item in result["results"]] == list(
        TOPOLOGY_INVARIANTS
    )
    assert all(item["passed"] for item in result["results"])


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        ("duplicate_node", "NODE_IDS_NOT_UNIQUE"),
        ("missing_endpoint", "EDGE_ENDPOINT_MISSING"),
        ("unsupported_operator", "UNSUPPORTED_NODE_KIND_OR_OPERATOR"),
        ("bad_arity", "GATE_ARITY_MISMATCH"),
        ("cycle", "COMBINATIONAL_CYCLE_DETECTED"),
        ("dangling_gate", "DANGLING_GATE"),
        ("two_output_drivers", "OUTPUT_DRIVER_COUNT_MISMATCH"),
    ],
)
def test_topology_checker_fails_closed_on_adversarial_graphs(
    mutation: str,
    reason_code: str,
) -> None:
    topology = topology_from_expression("A AND NOT B", ["A", "B"], "Y")
    graph = deepcopy(topology)
    if mutation == "duplicate_node":
        graph["nodes"].append(dict(graph["nodes"][0]))
    elif mutation == "missing_endpoint":
        graph["edges"].append({"source": "missing", "target": "Y"})
    elif mutation == "unsupported_operator":
        next(node for node in graph["nodes"] if node["kind"] == "gate")[
            "operator"
        ] = "XOR"
    elif mutation == "bad_arity":
        gate = next(
            node["id"]
            for node in graph["nodes"]
            if node.get("operator") == "AND"
        )
        graph["edges"] = [
            edge
            for edge in graph["edges"]
            if not (edge["target"] == gate and edge["source"] == "A")
        ]
    elif mutation == "cycle":
        gate = next(node["id"] for node in graph["nodes"] if node["kind"] == "gate")
        graph["edges"].append({"source": gate, "target": gate})
    elif mutation == "dangling_gate":
        graph["nodes"].append({"id": "unused", "kind": "gate", "operator": "NOT"})
        graph["edges"].append({"source": "A", "target": "unused"})
    elif mutation == "two_output_drivers":
        graph["edges"].append({"source": "A", "target": "Y"})

    result = validate_topology(graph)

    assert result["passed"] is False
    assert reason_code in result["reason_codes"]


def test_topology_checker_rejects_unknown_invariant_name() -> None:
    topology = topology_from_expression("A AND B", ["A", "B"], "Y")

    result = validate_topology(topology, ["not_frozen"])

    assert result["passed"] is False
    assert result["reason_codes"] == [
        "UNKNOWN_TOPOLOGY_INVARIANT:not_frozen"
    ]


def test_formal_claim_requires_all_formal_evidence_categories() -> None:
    evidence = [
        _evidence("contract", "output_contract_check"),
        _evidence("syntax", "formal_syntax_check"),
        _evidence("topology", "topology_check"),
        _evidence("truth", "truth_table_check"),
    ]
    claim = _claim(
        "formal_functional_success",
        [item["evidence_id"] for item in evidence],
    )

    audit = audit_egma_claims([claim], evidence, "A computational result.")

    assert audit["unsupported_claim"] is False
    assert audit["decisions"][0]["supported"] is True


def test_failed_or_ineligible_evidence_cannot_support_a_claim() -> None:
    evidence = [
        _evidence(
            "simulation",
            "simulation_trace",
            status="failed",
            comparison_eligible=False,
        )
    ]
    claim = _claim("simulation_completed", ["simulation"])

    audit = audit_egma_claims([claim], evidence, "Simulation completed.")

    assert audit["unsupported_claim"] is True
    assert "REQUIRED_EVIDENCE_CATEGORY_MISSING_OR_FAILED" in audit["decisions"][0][
        "reason_codes"
    ]


def test_mock_mapping_does_not_support_external_cello_claim() -> None:
    evidence = [
        _evidence(
            "mapping",
            "external_cello_mapping",
            metadata={"mapping_mode": "mock", "buildable": True},
        )
    ]
    claim = _claim("external_cello_mapping", ["mapping"])

    audit = audit_egma_claims([claim], evidence, "Cello-mapped design.")

    assert audit["unsupported_claim"] is True
    assert "EXTERNAL_CELLO_MAPPING_NOT_PROVEN" in audit["decisions"][0][
        "reason_codes"
    ]


def test_wet_lab_and_quantitative_in_vivo_claims_fail_by_protocol_boundary() -> None:
    claims = [
        _claim("wet_lab_ready", []),
        _claim("quantitative_in_vivo_prediction", []),
    ]

    audit = audit_egma_claims(
        claims,
        [],
        "This design is wet-lab ready and predicts quantitative in vivo behavior.",
    )

    assert audit["unsupported_count"] == 2
    assert all(
        "CLAIM_OUTSIDE_PROTOCOL_SUPPORT_BOUNDARY" in decision["reason_codes"]
        for decision in audit["decisions"]
    )


def test_unstructured_summary_claim_fails_closed() -> None:
    audit = audit_egma_claims(
        [],
        [],
        "The circuit was experimentally validated in E. coli.",
    )

    assert audit["status"] == "fail_closed"
    assert audit["detected_summary_claim_types"] == ["experimental_validation"]
    assert audit["decisions"][0]["source"] == "unstructured_summary"
    assert audit["decisions"][0]["reason_codes"] == [
        "SUMMARY_CLAIM_NOT_STRUCTURED"
    ]


def test_comparative_claim_requires_complete_frozen_paired_analysis() -> None:
    evidence = [
        _evidence(
            "comparison",
            "frozen_comparative_analysis",
            metadata={
                "comparison_eligible": True,
                "paired_coverage_complete": False,
            },
        )
    ]
    claim = _claim("comparative_superiority", ["comparison"])

    audit = audit_egma_claims([claim], evidence, "The full system outperformed S2.")

    assert audit["unsupported_claim"] is True
    assert "FROZEN_COMPARISON_NOT_PROVEN" in audit["decisions"][0]["reason_codes"]
