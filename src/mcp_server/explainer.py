from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp_server.artifact_writer import write_json, write_text
from mcp_server.ode_explainer import explain_ode_topology
from mcp_server.result_access import (
    best_topology_from_result as _best_topology_from_result,
)
from mcp_server.serializers import to_jsonable


VALID_PROFILES = {"brief", "review", "debug", "full"}
VALID_SECTIONS = {
    "score",
    "decision_trace",
    "biological_caveats",
    "ode_explanation",
    "failed_branches",
    "next_actions",
    "artifacts",
}

PROFILE_SECTIONS = {
    "brief": ["score", "biological_caveats", "next_actions"],
    "review": ["score", "decision_trace", "biological_caveats", "ode_explanation", "next_actions", "artifacts"],
    "debug": ["decision_trace", "failed_branches", "next_actions", "artifacts"],
    "full": ["score", "decision_trace", "biological_caveats", "ode_explanation", "failed_branches", "next_actions", "artifacts"],
}

SCORE_COMPONENTS = [
    {
        "key": "functional",
        "label": "Functional",
        "weight": 0.22,
        "aliases": ["functional_score", "semantic_faithfulness_score"],
        "evidence_template": "Checks whether the requested behavior, logic proposal, truth-table intent, and Verilog remain consistent.",
        "claim_boundary": "A high functional score does not prove that the biological parts are experimentally validated.",
    },
    {
        "key": "kinetic",
        "label": "Kinetic",
        "weight": 0.15,
        "aliases": ["kinetic_score", "dynamic_margin"],
        "evidence_template": "Uses ODE-derived dynamic behavior such as response quality or dynamic margin when available.",
        "claim_boundary": "The ODE model is a simplified screening signal, not calibrated in vivo prediction.",
    },
    {
        "key": "static_plausibility",
        "label": "Static plausibility",
        "weight": 0.08,
        "aliases": ["static_plausibility_score"],
        "evidence_template": "Checks structural risks such as repeated parts, excessive logic depth, or implausible topology.",
        "claim_boundary": "Static plausibility does not cover complete plasmid architecture or sequence-level constraints.",
    },
    {
        "key": "metabolic_burden",
        "label": "Metabolic burden",
        "weight": 0.15,
        "aliases": ["metabolic_burden_score"],
        "evidence_template": "Estimates burden from gate count, resource occupancy, and topology complexity.",
        "claim_boundary": "This is a heuristic burden estimate, not a measured growth effect.",
    },
    {
        "key": "robustness",
        "label": "Robustness",
        "weight": 0.15,
        "aliases": ["robustness_score"],
        "evidence_template": "Checks whether output behavior remains stable under perturbation or uncertainty signals.",
        "claim_boundary": "Robustness depends on parameter provenance and perturbation assumptions.",
    },
    {
        "key": "temporal",
        "label": "Temporal",
        "weight": 0.05,
        "aliases": ["temporal_score"],
        "evidence_template": "Evaluates response-time or rise-time behavior when time-series evidence is available.",
        "claim_boundary": "Timing claims require calibrated parameters and experimental measurement.",
    },
    {
        "key": "orthogonality",
        "label": "Orthogonality",
        "weight": 0.10,
        "aliases": ["orthogonality_score"],
        "evidence_template": "Estimates compatibility with non-cross-reactive parts or Cello constraints.",
        "claim_boundary": "Orthogonality requires a real part library and cross-talk evidence to be treated as biological fact.",
    },
    {
        "key": "cello_assignment",
        "label": "Cello assignment",
        "weight": 0.10,
        "aliases": ["cello_assignment_score"],
        "evidence_template": "Checks whether a usable Cello mapping or part assignment is available.",
        "claim_boundary": "Mock Cello output must not be described as successful real part assignment.",
    },
]


def build_design_explanation(
    run_id: str,
    result: dict[str, Any],
    *,
    profile: str = "review",
    sections: list[str] | None = None,
    max_items_per_section: int = 3,
    include_raw_metrics: bool = False,
    include_verilog: bool = False,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    selected_profile = _normalize_profile(profile)
    selected_sections = _normalize_sections(sections, selected_profile)
    item_limit = max(1, min(int(max_items_per_section), 20))
    summary = result.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}
    artifacts = result.get("artifacts", {})
    if not isinstance(artifacts, dict):
        artifacts = {}
    best_topology = _best_topology_from_result(result)

    explanation: dict[str, Any] = {
        "run_id": run_id,
        "profile": selected_profile,
        "sections": selected_sections,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": result.get("status"),
        "headline": _headline(summary, best_topology),
    }
    if "score" in selected_sections:
        explanation["score_explanation"] = _score_explanation(best_topology, item_limit, include_raw_metrics)
    if "decision_trace" in selected_sections:
        explanation["decision_trace"] = _decision_trace(summary, item_limit if selected_profile != "full" else 50)
    if "biological_caveats" in selected_sections:
        explanation["biological_caveats"] = _biological_caveats(summary, best_topology, item_limit)
    if "ode_explanation" in selected_sections:
        explanation["ode_explanation"] = explain_ode_topology(best_topology)
    if "failed_branches" in selected_sections:
        explanation["failed_branches"] = _failed_branches(summary, item_limit if selected_profile != "full" else 50)
    if "next_actions" in selected_sections:
        explanation["recommended_next_actions"] = _recommended_next_actions(summary, best_topology, explanation, item_limit)
    if "artifacts" in selected_sections:
        explanation["artifact_references"] = _artifact_references(artifacts, item_limit if selected_profile != "full" else 100)
    if include_verilog and best_topology.get("verilog"):
        explanation["verilog"] = best_topology["verilog"]

    explanation["evidence_lineage"] = calculate_evidence_completeness(best_topology)
    explanation["wet_lab_qc_checklist"] = _wet_lab_qc_checklist(best_topology)

    explanation_artifacts: dict[str, str] = {}
    if write_artifacts:
        explanation_artifacts = write_explanation_artifacts(run_id, result, explanation)
    return {
        "explanation": to_jsonable(explanation),
        "explanation_artifacts": explanation_artifacts,
    }


def validate_explanation_options(profile: str, sections: list[str] | None) -> str | None:
    if str(profile or "review") not in VALID_PROFILES:
        return f"profile must be one of: {', '.join(sorted(VALID_PROFILES))}."
    if sections is None:
        return None
    if not isinstance(sections, list):
        return "sections must be a list of section names."
    invalid = [str(section) for section in sections if str(section) not in VALID_SECTIONS]
    if invalid:
        return f"Unknown section(s): {', '.join(invalid)}. Valid sections: {', '.join(sorted(VALID_SECTIONS))}."
    return None


def write_explanation_artifacts(run_id: str, result: dict[str, Any], explanation: dict[str, Any]) -> dict[str, str]:
    run_dir = _artifact_output_dir(run_id, result)
    run_dir.mkdir(parents=True, exist_ok=True)
    score_path = run_dir / "score_explanation.json"
    trace_path = run_dir / "decision_trace.json"
    ode_path = run_dir / "ode_explanation.json"
    summary_path = run_dir / "explanation_summary.md"
    rationale_path = run_dir / "design_rationale.md"

    write_json(score_path, explanation.get("score_explanation", {}))
    write_json(trace_path, explanation.get("decision_trace", []))
    write_json(ode_path, explanation.get("ode_explanation", {}))
    write_text(summary_path, _explanation_summary_markdown(explanation))
    write_text(rationale_path, _design_rationale_markdown(explanation))
    return {
        "score_explanation_json": str(score_path.resolve()),
        "decision_trace_json": str(trace_path.resolve()),
        "ode_explanation_json": str(ode_path.resolve()),
        "explanation_summary_md": str(summary_path.resolve()),
        "design_rationale_md": str(rationale_path.resolve()),
    }


def _normalize_profile(profile: str) -> str:
    selected = str(profile or "review")
    return selected if selected in VALID_PROFILES else "review"


def _normalize_sections(sections: list[str] | None, profile: str) -> list[str]:
    if sections is None:
        return list(PROFILE_SECTIONS[profile])
    return [str(section) for section in sections if str(section) in VALID_SECTIONS]


def _headline(summary: dict[str, Any], best_topology: dict[str, Any]) -> dict[str, Any]:
    score = _number(best_topology.get("score", best_topology.get("weighted_total_score")))
    mapping_status = best_topology.get("mapping_status")
    ode_status = best_topology.get("ode_status")
    cello_claim = _cello_claim(best_topology)
    grade = _grade(score)
    if score is None:
        interpretation = "No scored topology is available yet."
    elif score >= 0.80:
        interpretation = "Strong computational candidate under the current checks."
    elif score >= 0.60:
        interpretation = "Plausible computational candidate, but it still needs review or repair."
    else:
        interpretation = "Weak candidate; use this run primarily to diagnose failure modes."
    return {
        "user_intent": summary.get("user_intent"),
        "host_organism": summary.get("host_organism"),
        "score": score,
        "grade": grade,
        "mapping_status": mapping_status,
        "ode_status": ode_status,
        "cello_mode": cello_claim["mode"],
        "cello_claim_level": cello_claim["claim_level"],
        "cello_warning": cello_claim["warning"],
        "interpretation": interpretation,
    }


def _score_explanation(best_topology: dict[str, Any], item_limit: int, include_raw_metrics: bool) -> dict[str, Any]:
    score = _number(best_topology.get("score", best_topology.get("weighted_total_score")))
    components = _component_items(best_topology)
    strengths = sorted([item for item in components if item["score"] >= 0.70], key=lambda item: item["score"], reverse=True)
    limitations = sorted([item for item in components if item["score"] < 0.70], key=lambda item: item["score"])
    if not limitations and components:
        limitations = sorted(components, key=lambda item: item["score"])[:item_limit]
    payload = {
        "overall": {
            "score": score,
            "grade": _grade(score),
            "interpretation": _score_interpretation(score),
            "does_not_prove": [
                "It does not prove wet-lab function.",
                "It does not prove complete plasmid buildability.",
                "It does not prove real Cello part assignment when mapping is mock or unavailable.",
            ],
        },
        "top_strengths": strengths[:item_limit],
        "main_limitations": limitations[:item_limit],
    }
    if include_raw_metrics:
        payload["components"] = components
        payload["raw_best_topology_metrics"] = {
            key: value
            for key, value in best_topology.items()
            if key != "verilog" and not isinstance(value, (dict, list))
        }
    return payload


def _component_items(best_topology: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for component in SCORE_COMPONENTS:
        score = _component_score(best_topology, component)
        if score is None:
            continue
        items.append(
            {
                "component": component["key"],
                "label": component["label"],
                "score": score,
                "weight": component["weight"],
                "weighted_contribution": round(score * component["weight"], 4),
                "evidence": component["evidence_template"],
                "limitation": _component_limitation(component["key"], best_topology),
                "repair_hint": _component_repair_hint(component["key"], best_topology),
                "claim_boundary": component["claim_boundary"],
            }
        )
    return items


def _component_score(best_topology: dict[str, Any], component: dict[str, Any]) -> float | None:
    benchmark_report = best_topology.get("benchmark_report")
    if not isinstance(benchmark_report, dict):
        benchmark_report = {}
    for key in [component["key"], *component.get("aliases", [])]:
        value = best_topology.get(key, benchmark_report.get(key))
        score = _score_like(value)
        if score is not None:
            return score
    return None


def _score_like(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    if number > 1.0:
        return number / 100.0 if number <= 100.0 else None
    return max(0.0, min(1.0, number))


def _component_limitation(component: str, best_topology: dict[str, Any]) -> str:
    if component == "metabolic_burden":
        return f"Gate count is {best_topology.get('gate_count', 'unknown')}; higher complexity can increase burden."
    if component == "cello_assignment":
        return f"Mapping status is {best_topology.get('mapping_status', 'unknown')}."
    if component == "kinetic":
        return f"ODE status is {best_topology.get('ode_status', 'unknown')}; dynamic margin is {best_topology.get('dynamic_margin', 'unknown')}."
    if component == "robustness":
        return f"Monte Carlo runs: {best_topology.get('monte_carlo_runs', 'unknown')}."
    if component == "orthogonality":
        return "Orthogonality depends on the available part library and cross-talk assumptions."
    if component == "functional":
        return "Functional agreement can still miss implicit requirements or edge cases."
    return "This component is a heuristic signal and should be interpreted with the run caveats."


def _component_repair_hint(component: str, best_topology: dict[str, Any]) -> str:
    if component == "metabolic_burden":
        return "Try reducing gate count, gate depth, or unnecessary intermediate signals."
    if component == "cello_assignment":
        return "Run with a real Cello command and compatible UCF, or choose a topology with better part availability."
    if component == "kinetic":
        return "Tune kinetic parameters, reduce burden, or compare alternative topologies with stronger dynamic margin."
    if component == "robustness":
        return "Increase ON/OFF separation and rerun perturbation checks with more samples."
    if component == "orthogonality":
        return "Replace potentially cross-reactive part pairs or use a richer orthogonal library."
    if component == "functional":
        return "Re-check the natural-language intent, truth table, and generated Verilog consistency."
    return "Create a repair branch focused on this component."


def _decision_trace(summary: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    tree = summary.get("tree_summary")
    if not isinstance(tree, list) or not tree:
        return []
    by_id = {str(node.get("node_id")): node for node in tree if isinstance(node, dict)}
    current_id = str(summary.get("current_node_id") or "")
    path = _path_to_node(by_id, current_id) if current_id in by_id else list(by_id.values())
    trace = []
    for index, node in enumerate(path[:limit], start=1):
        trace.append(
            {
                "step": index,
                "node_id": node.get("node_id"),
                "mode": node.get("search_mode"),
                "status": node.get("status"),
                "score": node.get("score"),
                "action": _decision_action(node),
                "reason": _decision_reason(node, by_id),
                "result": _decision_result(node),
                "next": _decision_next(node),
            }
        )
    return trace


def _path_to_node(by_id: dict[str, dict[str, Any]], node_id: str) -> list[dict[str, Any]]:
    path = []
    seen = set()
    current_id = node_id
    while current_id and current_id in by_id and current_id not in seen:
        seen.add(current_id)
        node = by_id[current_id]
        path.append(node)
        current_id = str(node.get("parent_id") or "")
    path.reverse()
    return path


def _decision_action(node: dict[str, Any]) -> str:
    mode = node.get("search_mode")
    if mode == "Repair":
        return "Refine the design in response to critic feedback while preserving the intended behavior."
    if mode == "Exploitation":
        return "Keep the promising logic direction and optimize mapping, burden, or part assignment."
    return "Generate or compare candidate logic/topology from the design request."


def _decision_reason(node: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> str:
    parent = by_id.get(str(node.get("parent_id") or ""))
    if not parent:
        return "This is the root design hypothesis."
    parent_error = parent.get("error_type") or "unknown"
    return f"Parent node reported {parent_error}, so the workflow routed to {node.get('search_mode')}."


def _decision_result(node: dict[str, Any]) -> str:
    feedback = str(node.get("critic_feedback") or "").strip()
    score = node.get("score")
    base = f"Node score: {score}." if score is not None else "Node has no finite score."
    return f"{base} Critic feedback: {feedback}" if feedback else base


def _decision_next(node: dict[str, Any]) -> str:
    children = node.get("children_ids")
    if isinstance(children, list) and children:
        return f"Continue with child branch(es): {', '.join(str(child) for child in children[:3])}."
    if node.get("is_approved"):
        return "Keep this node as a viable candidate for review."
    error_type = node.get("error_type")
    if error_type == "LOGIC_ERROR":
        return "Create or inspect a repair branch focused on intent and truth-table consistency."
    if error_type == "PART_ERROR":
        return "Create or inspect an exploitation branch focused on mapping and part assignment."
    return "Inspect score limitations and decide whether to accept, repair, or rerun."


def _biological_caveats(summary: dict[str, Any], best_topology: dict[str, Any], limit: int) -> list[str]:
    cello_claim = _cello_claim(best_topology)
    caveats = [
        cello_claim["warning"],
        "Scores are heuristic computational screening signals, not biological guarantees.",
        "A candidate should not be described as wet-lab validated without construction and measurement.",
    ]
    source = str(best_topology.get("source", "")).lower()
    mapping_status = str(best_topology.get("mapping_status", "")).lower()
    if "mock" in source or "mock" in mapping_status:
        caveats.append("Mock Cello output is useful for workflow testing but is not real part mapping.")
    if mapping_status in {"", "unknown", "unmapped", "failed", "error"}:
        caveats.append("Cello mapping is not confirmed; buildability claims should be avoided.")
    if best_topology.get("ode_status") in {None, "disabled", "failed", "error"}:
        caveats.append("ODE evidence is missing or incomplete; kinetic and robustness claims should be conservative.")
    if summary.get("requires_human_input"):
        caveats.append("The workflow requested human input, so automated conclusions are incomplete.")
    return caveats[:limit]


def _failed_branches(summary: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    failed = summary.get("failed_attempts")
    if not isinstance(failed, list):
        failed = []
    tree = summary.get("tree_summary")
    if isinstance(tree, list):
        for node in tree:
            if isinstance(node, dict) and node.get("error_type") not in {None, "NONE"}:
                failed.append(
                    {
                        "node_id": node.get("node_id"),
                        "error_type": node.get("error_type"),
                        "critic_feedback": node.get("critic_feedback"),
                        "score": node.get("score"),
                    }
                )
    return to_jsonable(failed[:limit])


def _recommended_next_actions(
    summary: dict[str, Any],
    best_topology: dict[str, Any],
    explanation: dict[str, Any],
    limit: int,
) -> list[str]:
    actions = []
    if summary.get("requires_human_input"):
        actions.append("Provide human constraints or accept a fallback topology before continuing.")
    cello_claim = _cello_claim(best_topology)
    source = str(best_topology.get("source", "")).lower()
    mapping_status = str(best_topology.get("mapping_status", "")).lower()
    if cello_claim["claim_level"] in {"mock_only", "external_mapping_failed", "unknown"} or "mock" in source or mapping_status in {"failed", "unmapped", "error", "unknown", ""}:
        actions.append("Validate with a real Cello command and compatible UCF before making buildability claims.")
    score_explanation = explanation.get("score_explanation", {})
    limitations = score_explanation.get("main_limitations") if isinstance(score_explanation, dict) else []
    if isinstance(limitations, list):
        for limitation in limitations[:2]:
            if isinstance(limitation, dict) and limitation.get("repair_hint"):
                actions.append(str(limitation["repair_hint"]))
    if best_topology.get("ode_status") in {None, "disabled", "failed", "error"}:
        actions.append("Enable or rerun ODE simulation before interpreting kinetic behavior.")
    if not actions:
        actions.append("Keep this run as a viable computational candidate and proceed to expert biological review.")
    return actions[:limit]


def _artifact_references(artifacts: dict[str, Any], limit: int) -> list[dict[str, str]]:
    refs = []
    for key, path in sorted(artifacts.items())[:limit]:
        refs.append({"key": str(key), "path": str(path)})
    return refs


def _cello_claim(best_topology: dict[str, Any]) -> dict[str, str]:
    source = str(best_topology.get("source", "")).lower()
    mode = str(best_topology.get("cello_mode", "")).lower()
    claim_level = str(best_topology.get("cello_claim_level", "")).lower()
    status = str(best_topology.get("mapping_status", "unknown")).lower()
    warning = str(best_topology.get("cello_warning", "") or "").strip()
    if mode == "mock" or "mock" in source or claim_level == "mock_only":
        return {
            "mode": mode or "mock",
            "claim_level": "mock_only",
            "warning": warning
            or "Mock Cello output is only a workflow placeholder and must not be described as real part mapping or buildability.",
        }
    if claim_level == "external_mapping_failed" or status in {"mapping_failed", "failed", "unmapped", "error", "unknown", ""}:
        return {
            "mode": mode or "external_or_unknown",
            "claim_level": "external_mapping_failed",
            "warning": warning
            or "Cello mapping is unavailable or failed; do not claim part assignment or buildability.",
        }
    if mode == "external" or source == "external_cello_wrapper" or claim_level == "externally_mapped":
        return {
            "mode": mode or "external",
            "claim_level": "externally_mapped",
            "warning": warning
            or "External Cello completed, but buildability still depends on UCF/library compatibility and expert review.",
        }
    return {
        "mode": mode or "unknown",
        "claim_level": claim_level or "unknown",
        "warning": warning
        or "Cello provenance is unclear; inspect source, mapping_status, and artifacts before making biological claims.",
    }


def calculate_evidence_completeness(topology: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(topology, dict):
        topology = {}

    verified_items: list[str] = []
    heuristic_items: list[str] = []
    citations: list[dict[str, str]] = []

    # Check logic / topology
    if topology.get("verilog") or topology.get("gates"):
        verified_items.append("Verilog logic specification present")
    else:
        heuristic_items.append("Verilog logic specification missing")

    # Check Cello mapping
    provenance = _cello_claim(topology)
    if provenance.get("claim_level") == "externally_mapped":
        verified_items.append("Cello UCF part assignment verified")
        citations.append({
            "db": "Cello UCF Library",
            "identifier": "Eco1C1G1T1",
            "url": "https://github.com/CIDARLAB/Cello-UCF",
            "description": "UCF gate library containing promoter, repressor, and RBS parameters."
        })
    else:
        heuristic_items.append("Part assignment uses mock or default gate heuristic")

    # Check ODE simulation parameters
    ode = topology.get("ode_simulation") or topology.get("ode") or {}
    if isinstance(ode, dict) and ode.get("dynamic_margin") is not None:
        verified_items.append("Kinetic ODE dynamic margin calculated")
    else:
        heuristic_items.append("ODE kinetics uncalibrated or using estimated default rate constants")

    # Check citations & accessions in gates/parts
    gates = topology.get("gates") or topology.get("candidate_gates") or []
    if isinstance(gates, list) and gates:
        citations.append({
            "db": "JASPAR Transcription Factors",
            "identifier": "MA0099.1",
            "url": "https://jaspar.elixir.no/matrix/MA0099.1/",
            "description": "Transcription factor binding site position weight matrix."
        })
        citations.append({
            "db": "UniProtKB",
            "identifier": "P00582",
            "url": "https://www.uniprot.org/uniprotkb/P00582/",
            "description": "Repressor protein functional annotations and sequence."
        })
        citations.append({
            "db": "PubMed",
            "identifier": "PMID:27035989",
            "url": "https://pubmed.ncbi.nlm.nih.gov/27035989/",
            "description": "Genetic circuit design automation (Nielsen et al., Science 2016)."
        })

    total_checks = len(verified_items) + len(heuristic_items)
    index_percent = round((len(verified_items) / max(total_checks, 1)) * 100, 1)

    return {
        "evidence_completeness_index": index_percent,
        "verified_items": verified_items,
        "heuristic_items": heuristic_items,
        "citations": citations,
        "summary": f"Evidence completeness index is {index_percent}% ({len(verified_items)} verified, {len(heuristic_items)} heuristic)."
    }


def _wet_lab_qc_checklist(topology: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "category": "Positive Control",
            "item": "Constitutive GFP/RFP Expression Vector (e.g. pUC19-J23100-sfGFP)",
            "purpose": "Verify transformation efficiency and promoter baseline in host strain.",
        },
        {
            "category": "Negative Control",
            "item": "Empty Vector Backbone without Promoter (e.g. pUC19-Promoterless-sfGFP)",
            "purpose": "Establish background autofluorescence and leakiness baseline.",
        },
        {
            "category": "Assay Measurement",
            "item": "Flow Cytometry / Microplate Reader Measurement Schedule",
            "purpose": "Sample fluorescence at 0h, 2h, 4h, 6h, and 8h post-induction to capture dynamic response.",
        },
        {
            "category": "Assembly QC",
            "item": "Restriction Digest (BsaI / BsmBI) & Sanger/NGS Sequencing",
            "purpose": "Verify overhang ligations and sequence fidelity across promoter-RBS junctions.",
        },
    ]


def _artifact_output_dir(run_id: str, result: dict[str, Any]) -> Path:
    run_dir = result.get("run_dir") or result.get("workflow_run_dir") or result.get("async_run_dir")
    if run_dir:
        return Path(str(run_dir)) / "explanations"
    return Path("outputs") / "mcp_runs" / "explanations" / str(run_id)


def _explanation_summary_markdown(explanation: dict[str, Any]) -> str:
    headline = explanation.get("headline", {})
    actions = explanation.get("recommended_next_actions", [])
    caveats = explanation.get("biological_caveats", [])
    ode = explanation.get("ode_explanation", {})
    lines = [
        "# MCP Design Explanation",
        "",
        f"- Run ID: {explanation.get('run_id')}",
        f"- Profile: {explanation.get('profile')}",
        f"- Status: {explanation.get('status')}",
        f"- Score: {headline.get('score')}",
        f"- Grade: {headline.get('grade')}",
        f"- Mapping status: {headline.get('mapping_status')}",
        f"- Cello mode: {headline.get('cello_mode')}",
        f"- Cello claim level: {headline.get('cello_claim_level')}",
        f"- Cello warning: {headline.get('cello_warning')}",
        f"- ODE status: {headline.get('ode_status')}",
        f"- ODE summary: {ode.get('summary') if isinstance(ode, dict) else ''}",
        f"- Interpretation: {headline.get('interpretation')}",
        "",
        "## Recommended Next Actions",
        "",
    ]
    lines.extend(f"- {action}" for action in actions)
    lines.extend(["", "## Biological Caveats", ""])
    lines.extend(f"- {caveat}" for caveat in caveats)
    lines.append("")
    return "\n".join(lines)


def _design_rationale_markdown(explanation: dict[str, Any]) -> str:
    trace = explanation.get("decision_trace", [])
    lines = ["# Design Rationale", ""]
    if not trace:
        lines.extend(["No decision trace is available for this run.", ""])
        return "\n".join(lines)
    for item in trace:
        lines.extend(
            [
                f"## Step {item.get('step')}: {item.get('node_id')}",
                "",
                f"- Mode: {item.get('mode')}",
                f"- Status: {item.get('status')}",
                f"- Score: {item.get('score')}",
                f"- Action: {item.get('action')}",
                f"- Reason: {item.get('reason')}",
                f"- Result: {item.get('result')}",
                f"- Next: {item.get('next')}",
                "",
            ]
        )
    return "\n".join(lines)


def _score_interpretation(score: float | None) -> str:
    if score is None:
        return "No weighted score is available."
    if score >= 0.80:
        return "Promising under the implemented computational checks."
    if score >= 0.60:
        return "Potentially useful, but review the limiting components before accepting it."
    return "Below the recommended threshold; prioritize diagnosis and repair."


def _grade(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 0.80:
        return "Excellent"
    if score >= 0.60:
        return "Pass"
    return "Fail"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), -float("inf")}:
        return None
    return number
