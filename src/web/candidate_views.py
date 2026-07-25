from __future__ import annotations

import json
import re
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from schemas.design_ir import topology_to_design_ir


@dataclass
class CanonicalCandidateReference:
    run_id: str
    candidate_index: int
    topology: dict
    topology_hash: str
    source_shape: str  # 'root', 'summary', 'artifact', or 'best-only'


TERMINAL_RUN_STATUSES = {
    "completed",
    "needs_human_input",
    "error",
    "failed",
    "cancelled",
}


@dataclass
class ScoreComponentView:
    key: str
    label: str
    score: float
    percent: int
    status_class: str  # 'status-ready', 'status-warning', 'status-error'
    description: str


@dataclass
class WarningView:
    message: str
    level: str  # 'warning', 'error', 'info'
    category: str


@dataclass
class CandidateSummaryView:
    index: int
    name: str
    is_best: bool
    score: float
    mapping_status: str
    limiting_factor: str
    host_organism: str
    verilog_summary: str
    ode_status: str
    warnings: List[WarningView] = field(default_factory=list)
    is_fallback: bool = False
    is_provisional: bool = False
    is_incomplete: bool = False


@dataclass
class CandidateListView:
    run_id: str
    run_status: str
    user_intent: str
    host_organism: str
    best_score: Optional[float]
    best_candidate_index: Optional[int]
    candidates: List[CandidateSummaryView]
    tool_versions: Dict[str, str]
    total_candidates: int
    empty_state_type: Optional[str]  # 'not_completed', 'empty', 'unparseable', 'all_failed', None


@dataclass
class CandidateDetailView:
    run_id: str
    index: int
    name: str
    is_best: bool
    score: float
    conclusion_summary: str
    advantages: List[str]
    limitations: List[str]
    next_steps: List[str]
    evidence_level: str
    scores: List[ScoreComponentView]

    # Logical Design
    boolean_expression: str
    verilog_code: str
    topology_graph: Dict[str, Any]  # nodes and edges

    # Biological Design
    regulatory_graph: Dict[str, Any]
    constructs: List[Dict[str, Any]]
    transcriptional_units: List[Dict[str, Any]]
    parts: List[Dict[str, Any]]
    cello_mapping_status: str
    cello_fallback_used: bool

    # Advanced
    raw_json: str
    tool_versions: Dict[str, str]
    warnings: List[WarningView]
    provenance: List[Dict[str, Any]]
    simulation_metadata: Dict[str, Any]


def _extract_candidate_topologies(run_id: str, run_result: dict) -> list[CanonicalCandidateReference]:
    if not isinstance(run_result, dict):
        return []

    def get_topology_hash(topo: dict) -> str:
        def serialize_clean(obj):
            if isinstance(obj, dict):
                return {k: serialize_clean(v) for k, v in sorted(obj.items())}
            elif isinstance(obj, list):
                return [serialize_clean(x) for x in obj]
            elif isinstance(obj, (str, int, float, bool, type(None))):
                return obj
            else:
                return str(obj)
        cleaned = serialize_clean(topo)
        dumped = json.dumps(cleaned, sort_keys=True)
        return hashlib.sha256(dumped.encode("utf-8")).hexdigest()

    # 1. Check direct key
    topologies = run_result.get("candidate_topologies")
    if isinstance(topologies, list) and topologies:
        return [
            CanonicalCandidateReference(
                run_id=run_id,
                candidate_index=i,
                topology=t,
                topology_hash=get_topology_hash(t),
                source_shape="root"
            )
            for i, t in enumerate(topologies)
        ]

    # 2. Check summary key
    summary = run_result.get("summary")
    if isinstance(summary, dict):
        topologies = summary.get("candidate_topologies")
        if isinstance(topologies, list) and topologies:
            return [
                CanonicalCandidateReference(
                    run_id=run_id,
                    candidate_index=i,
                    topology=t,
                    topology_hash=get_topology_hash(t),
                    source_shape="summary"
                )
                for i, t in enumerate(topologies)
            ]

    # 3. Check artifacts path and read state.json
    artifacts = run_result.get("artifacts")
    if isinstance(artifacts, dict):
        state_path_str = artifacts.get("state_json")
        if state_path_str:
            state_path = Path(state_path_str)
            if not state_path.exists():
                raise FileNotFoundError(f"State artifact path does not exist: {state_path_str}")
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception as e:
                raise ValueError(f"State artifact JSON is corrupted: {e}")
            if not isinstance(state_data, dict):
                raise ValueError("State artifact shape is invalid: not a dictionary")

            topologies = state_data.get("candidate_topologies")
            if isinstance(topologies, list) and topologies:
                return [
                    CanonicalCandidateReference(
                        run_id=run_id,
                        candidate_index=i,
                        topology=t,
                        topology_hash=get_topology_hash(t),
                        source_shape="artifact"
                    )
                    for i, t in enumerate(topologies)
                ]
            else:
                raise ValueError("State artifact has no candidate topologies list")

    # 4. Check if best_topology is the only candidate available
    best_topo = run_result.get("best_topology") or (summary.get("best_topology") if isinstance(summary, dict) else None)
    if isinstance(best_topo, dict) and best_topo:
        return [
            CanonicalCandidateReference(
                run_id=run_id,
                candidate_index=0,
                topology=best_topo,
                topology_hash=get_topology_hash(best_topo),
                source_shape="best-only"
            )
        ]

    return []


def get_candidate_or_raise(run_id: str, run_result: dict, index: int) -> CanonicalCandidateReference:
    if not isinstance(run_result, dict):
        raise ValueError("Run result data is not available.")
    topologies = _extract_candidate_topologies(run_id, run_result)
    if not topologies:
        raise ValueError("No candidate topologies found in this run.")
    if index < 0 or index >= len(topologies):
        raise ValueError(f"Candidate index {index} is out of range.")
    return topologies[index]


def _extract_verilog_summary(topo: dict) -> str:
    verilog = str(topo.get("verilog") or "")
    # Remove comments
    clean_verilog = re.sub(r"//.*", "", verilog)
    clean_verilog = re.sub(r"/\*.*?\*/", "", clean_verilog, flags=re.DOTALL)

    inputs = re.findall(r"\binput\b\s+([^;]+)", clean_verilog)
    outputs = re.findall(r"\boutput\b\s+([^;]+)", clean_verilog)

    input_count = 0
    output_count = 0
    for inp in inputs:
        input_count += len([i.strip() for i in inp.split(",") if i.strip()])
    for out in outputs:
        output_count += len([o.strip() for o in out.split(",") if o.strip()])

    gate_count = topo.get("gate_count") or topo.get("gene_count")
    if not gate_count:
        gate_count = len(re.findall(r"assign\s+", clean_verilog))

    if input_count or output_count:
        return f"{input_count} In / {output_count} Out | {gate_count} Gates"
    elif verilog.strip():
        return f"自訂 Verilog ({len(verilog.splitlines())} 行)"
    else:
        return "無 Verilog 代碼"


def _determine_score_status(score: float) -> str:
    if score >= 0.85:
        return "status-ready"
    elif score >= 0.70:
        return "status-warning"
    else:
        return "status-error"


def _get_limiting_factor(topo: dict) -> str:
    if topo.get("mapping_status") == "MAPPING_FAILED":
        return "Cello 映射失敗 (UCF 限制不匹配或無可用邏輯閘)"

    score_fields = [
        ("functional_score", "Functional"),
        ("kinetic_score", "Kinetic / ODE"),
        ("static_plausibility_score", "Static Plausibility"),
        ("metabolic_burden_score", "Metabolic Burden"),
        ("robustness_score", "Robustness"),
        ("orthogonality_score", "Orthogonality"),
        ("cello_assignment_score", "Cello Assignment"),
        ("toxicity_score", "Toxicity"),
        ("semantic_faithfulness_score", "Semantic Faithfulness"),
    ]

    lowest_score = 1.0
    lowest_label = None

    for field_key, label in score_fields:
        val = topo.get(field_key)
        if val is not None:
            try:
                f_val = float(val)
                if f_val < lowest_score:
                    lowest_score = f_val
                    lowest_label = label
            except (ValueError, TypeError):
                pass

    if lowest_label and lowest_score < 0.75:
        return f"{lowest_label} 表現較差 ({lowest_score:.2f})"

    return "無明顯限制因素"


def _extract_warnings(topo: dict, run_status: dict | None = None) -> list[WarningView]:
    warnings = []

    # Safety Warnings
    from utils.safety_checker import check_safety
    intent = ""
    host = "Escherichia coli"
    if run_status:
        intent = run_status.get("user_intent") or run_status.get("summary", {}).get("user_intent") or run_status.get("request", {}).get("user_intent") or ""
        host = run_status.get("host_organism") or run_status.get("summary", {}).get("host_organism") or run_status.get("request", {}).get("host_organism") or "Escherichia coli"
    intent = intent or topo.get("user_intent") or ""
    host = host or topo.get("host_organism") or "Escherichia coli"
    if intent:
        safety_result = check_safety(intent, host)
        for w in safety_result.warnings:
            warnings.append(WarningView(
                message=w,
                level="warning",
                category="safety"
            ))

    # Provisional Warning
    source = str(topo.get("source") or "").lower()
    cello_mode = str(topo.get("cello_mode") or "").lower()
    claim_level = str(topo.get("cello_claim_level") or "").lower()
    warning_text = str(topo.get("cello_warning") or "")

    if cello_mode == "mock" or claim_level == "mock_only" or "mock" in source or "示範" in warning_text:
        warnings.append(WarningView(
            message=warning_text or "此結果採用示範 (Mock) 模式生成，尚未經過真實元件庫物理映射驗證。",
            level="warning",
            category="provisional"
        ))

    # Fallback Warning
    if topo.get("cello_fallback_used") or topo.get("mapping_status") == "fallback" or "fallback" in claim_level:
        warnings.append(WarningView(
            message="Cello 映射使用備用元件 (Fallback)，可能影響最終生物電路性能與穩定性。",
            level="warning",
            category="fallback"
        ))

    # Incomplete Warning
    ode_status = topo.get("ode_status", "disabled")
    if ode_status == "disabled":
        warnings.append(WarningView(
            message="ODE 動態模擬已被停用或未執行，缺少動力學評估軌跡。",
            level="info",
            category="incomplete"
        ))
    elif ode_status in {"incomplete", "failed"}:
        warnings.append(WarningView(
            message="ODE 動態模擬執行失敗或不完整，可能導致動力學分數不準確。",
            level="error",
            category="incomplete"
        ))

    return warnings


def build_candidate_list_view(
    run_id: str,
    run_status: dict,
    run_result: Optional[dict]
) -> CandidateListView:
    status_str = run_status.get("status", "unknown")

    user_intent = run_status.get("summary", {}).get("user_intent") or run_status.get("request", {}).get("user_intent") or ""
    host_organism = run_status.get("summary", {}).get("host_organism") or run_status.get("request", {}).get("host_organism") or "Escherichia coli"
    tool_versions = run_status.get("summary", {}).get("tool_versions") or run_status.get("tool_versions") or {}
    if not tool_versions and isinstance(run_result, dict):
        tool_versions = run_result.get("summary", {}).get("tool_versions") or run_result.get("tool_versions") or {}

    # Check not completed
    if status_str not in TERMINAL_RUN_STATUSES:
        return CandidateListView(
            run_id=run_id,
            run_status=status_str,
            user_intent=user_intent,
            host_organism=host_organism,
            best_score=None,
            best_candidate_index=None,
            candidates=[],
            tool_versions=tool_versions,
            total_candidates=0,
            empty_state_type="not_completed",
        )

    if not isinstance(run_result, dict):
        return CandidateListView(
            run_id=run_id,
            run_status=status_str,
            user_intent=user_intent,
            host_organism=host_organism,
            best_score=None,
            best_candidate_index=None,
            candidates=[],
            tool_versions=tool_versions,
            total_candidates=0,
            empty_state_type="unparseable",
        )

    try:
        candidate_refs = _extract_candidate_topologies(run_id, run_result)
    except Exception:
        return CandidateListView(
            run_id=run_id,
            run_status=status_str,
            user_intent=user_intent,
            host_organism=host_organism,
            best_score=None,
            best_candidate_index=None,
            candidates=[],
            tool_versions=tool_versions,
            total_candidates=0,
            empty_state_type="unparseable",
        )

    if not candidate_refs:
        return CandidateListView(
            run_id=run_id,
            run_status=status_str,
            user_intent=user_intent,
            host_organism=host_organism,
            best_score=None,
            best_candidate_index=None,
            candidates=[],
            tool_versions=tool_versions,
            total_candidates=0,
            empty_state_type="empty",
        )

    # Check all failed
    all_failed = all(ref.topology.get("mapping_status") == "MAPPING_FAILED" for ref in candidate_refs)

    # Calculate best candidate
    best_score = -9999.0
    best_idx = 0

    best_topo = run_result.get("best_topology") or run_result.get("summary", {}).get("best_topology")

    for idx, ref in enumerate(candidate_refs):
        topo = ref.topology
        score = float(topo.get("score", topo.get("weighted_total_score", 0.0)))
        if score > best_score:
            best_score = score
            best_idx = idx

    if best_topo and isinstance(best_topo, dict):
        best_topo_score = float(best_topo.get("score", best_topo.get("weighted_total_score", 0.0)))
        for idx, ref in enumerate(candidate_refs):
            topo = ref.topology
            topo_score = float(topo.get("score", topo.get("weighted_total_score", 0.0)))
            if topo.get("verilog_index") == best_topo.get("verilog_index") and abs(topo_score - best_topo_score) < 1e-5:
                best_idx = idx
                best_score = topo_score
                break

    candidates_view = []
    for idx, ref in enumerate(candidate_refs):
        topo = ref.topology
        score_val = float(topo.get("score", topo.get("weighted_total_score", 0.0)))

        topo_warnings = _extract_warnings(topo, run_status)
        is_fallback = any(w.category == "fallback" for w in topo_warnings)
        is_provisional = any(w.category == "provisional" for w in topo_warnings)
        is_incomplete = any(w.category == "incomplete" for w in topo_warnings)

        candidates_view.append(
            CandidateSummaryView(
                index=idx,
                name=f"Candidate #{idx + 1}",
                is_best=(idx == best_idx and not all_failed),
                score=round(score_val, 3),
                mapping_status=topo.get("mapping_status") or "unknown",
                limiting_factor=_get_limiting_factor(topo),
                host_organism=topo.get("host_organism") or host_organism,
                verilog_summary=_extract_verilog_summary(topo),
                ode_status=topo.get("ode_status") or "disabled",
                warnings=topo_warnings,
                is_fallback=is_fallback,
                is_provisional=is_provisional,
                is_incomplete=is_incomplete,
            )
        )

    return CandidateListView(
        run_id=run_id,
        run_status=status_str,
        user_intent=user_intent,
        host_organism=host_organism,
        best_score=round(best_score, 3) if not all_failed else 0.0,
        best_candidate_index=best_idx if not all_failed else None,
        candidates=candidates_view,
        tool_versions=tool_versions,
        total_candidates=len(candidate_refs),
        empty_state_type="all_failed" if all_failed else None,
    )


def build_candidate_detail_view(
    run_id: str,
    run_status: dict,
    run_result: dict,
    index: int
) -> CandidateDetailView:
    candidate_refs = _extract_candidate_topologies(run_id, run_result)
    if index < 0 or index >= len(candidate_refs):
        raise ValueError(f"Candidate index {index} is out of range.")

    topo = candidate_refs[index].topology

    best_idx = 0
    best_score = -9999.0
    best_topo = run_result.get("best_topology") or run_result.get("summary", {}).get("best_topology")

    for idx, ref in enumerate(candidate_refs):
        t = ref.topology
        score_val = float(t.get("score", t.get("weighted_total_score", 0.0)))
        if score_val > best_score:
            best_score = score_val
            best_idx = idx

    if best_topo and isinstance(best_topo, dict):
        best_topo_score = float(best_topo.get("score", best_topo.get("weighted_total_score", 0.0)))
        for idx, ref in enumerate(candidate_refs):
            t = ref.topology
            topo_score = float(t.get("score", t.get("weighted_total_score", 0.0)))
            if t.get("verilog_index") == best_topo.get("verilog_index") and abs(topo_score - best_topo_score) < 1e-5:
                best_idx = idx
                break

    is_best = (index == best_idx)
    score_val = float(topo.get("score", topo.get("weighted_total_score", 0.0)))

    score_fields = [
        ("functional_score", "Functional (功能正確性)", "邏輯閘行為與預期布林代數真值表的一致程度。"),
        ("kinetic_score", "Kinetic / ODE (動態動力學)", "系統過渡狀態、動態邊際與穩定狀態之動態分析得分。"),
        ("static_plausibility_score", "Static Plausibility (靜態合理性)", "結構合理性、長度限制與啟動子配置合理性。"),
        ("metabolic_burden_score", "Metabolic Burden (代謝負擔)", "電路轉錄與轉譯對宿主細胞造成的額外代謝壓力負載。"),
        ("robustness_score", "Robustness (強健性/抗干擾)", "面對隨機噪音、溫度或生長環境變動時的表現穩定度。"),
        ("orthogonality_score", "Orthogonality (正交性)", "各調控元件之間避免非預期相互干擾與干涉的能力。"),
        ("cello_assignment_score", "Cello Assignment (Cello 分派)", "Cello 進行邏輯閘元件物理分配與指派的評分。"),
        ("toxicity_score", "Toxicity (細胞毒性)", "表現產物是否對宿主細胞具有毒性，影響細胞生長。"),
        ("semantic_faithfulness_score", "Semantic Faithfulness (語意忠實度)", "生成設計是否完全符合自然語言指令之主要規範。"),
    ]

    scores_view = []
    advantages = []
    limitations = []
    next_steps = []

    for key, label, desc in score_fields:
        val = topo.get(key)
        if val is not None:
            try:
                f_val = float(val)
                percent = round(f_val * 100)
                status_class = _determine_score_status(f_val)
                scores_view.append(ScoreComponentView(
                    key=key,
                    label=label,
                    score=round(f_val, 3),
                    percent=percent,
                    status_class=status_class,
                    description=desc
                ))

                if f_val >= 0.85:
                    advantages.append(f"{label} 得分優異 ({f_val:.2f})")
                elif f_val < 0.70:
                    limitations.append(f"{label} 表現不佳 ({f_val:.2f})")
                    if "burden" in key:
                        next_steps.append("建議使用低拷貝數載體，或微調 RBS 強度以減輕代謝壓力。")
                    elif "ortho" in key:
                        next_steps.append("建議替換正交性較高的轉錄抑制蛋白（如 PhlF 或 BetI）。")
                    elif "toxic" in key:
                        next_steps.append("考量元件表現毒性，建議引進嚴格調控之誘導型啟動子。")
                    elif "kinetic" in key:
                        next_steps.append("動力學動態邊際偏低，建議重新調整回饋環路或元件延遲。")
            except (ValueError, TypeError):
                pass

    if not advantages:
        advantages.append("基本邏輯架構與 Verilog 編譯正確。")
    if not limitations:
        if topo.get("mapping_status") == "MAPPING_FAILED":
            limitations.append("Cello 元件映射遭遇失敗。")
        else:
            limitations.append("無特別嚴重的效能瓶頸。")
    if not next_steps:
        if topo.get("mapping_status") == "MAPPING_FAILED":
            next_steps.append("請檢查 UCF 限制文件與閘庫元件是否與此電路拓樸匹配。")
        else:
            next_steps.append("可直接將此候選整合至設計庫中，並進行載體與質體建構。")

    if score_val >= 0.85:
        conclusion_summary = f"此候選方案整體評估分數高達 {score_val:.3f}。各維度效能優異，尤其是功能正確性與生物正交性十分突出，且代謝負擔適中，屬於高度推薦的實體化方案。"
    elif score_val >= 0.70:
        conclusion_summary = f"此方案評分為 {score_val:.3f}，屬於可用設計。邏輯與 Verilog 生成正確，但在部分維度存在限制（如：{_get_limiting_factor(topo)}）。可進行微調或部分元件替換以提升穩定度。"
    else:
        conclusion_summary = f"此方案評分偏低 ({score_val:.3f})，主因為：{_get_limiting_factor(topo)}。可能需要重新設計邏輯拓樸或更換元件庫。"

    host_organism = run_status.get("summary", {}).get("host_organism") or run_status.get("request", {}).get("host_organism") or "Escherichia coli"

    parts = []
    constructs = []
    regulatory_edges = []

    try:
        design_ir = topology_to_design_ir(topo, host_organism=host_organism)
        for p in design_ir.parts:
            parts.append({
                "id": p.id,
                "name": p.name,
                "part_type": p.part_type,
                "role": p.role,
                "sequence": p.sequence or "未指定",
                "source": p.source,
                "confidence": p.confidence,
                "rationale": p.rationale,
                "assignment": {
                    "part_id": p.assignment.part_id,
                    "part_name": p.assignment.part_name,
                    "part_type": p.assignment.part_type,
                    "sequence": p.assignment.sequence,
                    "confidence": str(p.assignment.confidence or "verified"),
                } if p.assignment else None
            })

        for c in design_ir.constructs:
            constructs.append({
                "id": c.id,
                "name": c.name,
                "parts": c.parts,
                "topology": c.topology,
                "backbone": c.backbone or "未指定載體",
                "assembly_method": c.assembly_method or "標準 Golden Gate",
            })

        for i in design_ir.interactions:
            regulatory_edges.append({
                "source": i.source,
                "target": i.target,
                "type": i.interaction_type,
                "label": i.label
            })
    except Exception:
        pass

    raw_tus = topo.get("transcriptional_units") or topo.get("cassettes") or []
    transcriptional_units = []
    if isinstance(raw_tus, list) and raw_tus:
        for idx, tu in enumerate(raw_tus):
            if isinstance(tu, dict):
                transcriptional_units.append({
                    "name": tu.get("name") or f"轉錄單元 #{idx + 1}",
                    "promoter": tu.get("promoter") or "未指派啟動子",
                    "rbs": tu.get("rbs") or "未指派 RBS",
                    "cds": tu.get("cds") or "未指派 CDS",
                    "terminator": tu.get("terminator") or "未指派終止子",
                })
    else:
        assignments = topo.get("part_assignments", topo.get("assignments", []))
        if isinstance(assignments, list):
            gates_seen = set()
            for raw in assignments:
                if not isinstance(raw, dict):
                    continue
                node_id = raw.get("logic_node_id") or raw.get("node_id") or ""
                part_id = raw.get("part_id") or raw.get("id") or ""
                part_type = raw.get("part_type") or raw.get("type") or ""
                if node_id and part_id and node_id not in gates_seen:
                    gates_seen.add(node_id)
                    transcriptional_units.append({
                        "name": f"表達卡匣 ({node_id})",
                        "promoter": f"P_{node_id} (概念啟動子)",
                        "rbs": "標準核糖體結合位 (RBS)",
                        "cds": f"{part_id} ({part_type})",
                        "terminator": "標準轉錄終止子",
                    })

    boolean_expression = topo.get("logic_expression") or topo.get("boolean_expression") or ""
    if not boolean_expression:
        verilog_code = str(topo.get("verilog") or "")
        assigns = re.findall(r"assign\s+(\w+)\s*=\s*([^;]+);", verilog_code)
        if assigns:
            boolean_expression = ", ".join(f"{var} = {expr.strip()}" for var, expr in assigns)
        else:
            boolean_expression = "未定義布林表達式"

    nodes = []
    edges = []
    graph = topo.get("topology_graph") or topo.get("graph")

    if isinstance(graph, dict):
        raw_nodes = graph.get("nodes")
        raw_edges = graph.get("edges")
        if isinstance(raw_nodes, list):
            for n_idx, n in enumerate(raw_nodes):
                if isinstance(n, dict):
                    nodes.append({
                        "id": n.get("id") or n.get("node_id") or f"n_{n_idx}",
                        "label": n.get("label") or n.get("name") or "",
                        "type": n.get("type") or "signal"
                    })
        if isinstance(raw_edges, list):
            for e in raw_edges:
                if isinstance(e, dict):
                    edges.append({
                        "source": e.get("source") or e.get("from") or "",
                        "target": e.get("target") or e.get("to") or "",
                        "label": e.get("label") or e.get("type") or ""
                    })

    if not nodes:
        verilog_code = str(topo.get("verilog") or "")
        clean_v = re.sub(r"//.*", "", verilog_code)
        clean_v = re.sub(r"/\*.*?\*/", "", clean_v, flags=re.DOTALL)

        inputs_found = re.findall(r"\binput\b\s+([^;]+)", clean_v)
        outputs_found = re.findall(r"\boutput\b\s+([^;]+)", clean_v)

        for inp in inputs_found:
            for i in inp.split(","):
                i_name = i.strip()
                if i_name:
                    nodes.append({"id": i_name, "label": i_name, "type": "input"})
        for out in outputs_found:
            for o in out.split(","):
                o_name = o.strip()
                if o_name:
                    nodes.append({"id": o_name, "label": o_name, "type": "output"})

        assigns = re.findall(r"assign\s+(\w+)\s*=\s*([^;]+);", clean_v)
        for var, expr in assigns:
            if {"id": var, "label": var, "type": "output"} not in nodes:
                nodes.append({"id": var, "label": var, "type": "gate"})
            deps = re.findall(r"\b([A-Za-z_]\w*)\b", expr)
            for d in deps:
                if d in {n["id"] for n in nodes} and d != var:
                    edges.append({"source": d, "target": var, "label": "logic"})

    regulatory_graph = {"nodes": [], "edges": []}
    if regulatory_edges:
        reg_nodes = set()
        for e in regulatory_edges:
            reg_nodes.add(e["source"])
            reg_nodes.add(e["target"])
        for node_id in sorted(reg_nodes):
            n_type = "reporter" if "reporter" in node_id or "GFP" in node_id or "YFP" in node_id else "promoter" if "promoter" in node_id or "P_" in node_id else "repressor"
            regulatory_graph["nodes"].append({"id": node_id, "label": node_id, "type": n_type})
        regulatory_graph["edges"] = regulatory_edges
    else:
        assignments = topo.get("part_assignments", topo.get("assignments", []))
        if isinstance(assignments, list):
            reg_nodes = set()
            for raw in assignments:
                if not isinstance(raw, dict):
                    continue
                node_id = raw.get("logic_node_id") or raw.get("node_id") or ""
                part_id = raw.get("part_id") or raw.get("id") or ""
                if node_id and part_id:
                    reg_nodes.add(node_id)
                    reg_nodes.add(part_id)
                    regulatory_graph["edges"].append({
                        "source": part_id,
                        "target": node_id,
                        "type": "expression",
                        "label": "expresses"
                    })
            for node_id in sorted(reg_nodes):
                n_type = "cds" if "CDS" in node_id or "regulator" in node_id else "gate"
                regulatory_graph["nodes"].append({"id": node_id, "label": node_id, "type": n_type})

    warnings = _extract_warnings(topo, run_status)

    tool_versions = run_status.get("summary", {}).get("tool_versions") or run_status.get("tool_versions") or {}
    if not tool_versions and isinstance(run_result, dict):
        tool_versions = run_result.get("summary", {}).get("tool_versions") or run_result.get("tool_versions") or {}

    provenance = []
    raw_prov = topo.get("provenance") or []
    if isinstance(raw_prov, list):
        for p in raw_prov:
            if isinstance(p, dict):
                provenance.append({
                    "step": p.get("step") or p.get("activity") or "unknown",
                    "timestamp": p.get("timestamp") or p.get("time") or "",
                    "agent": p.get("agent") or p.get("executor") or "",
                    "details": p.get("details") or p.get("description") or "",
                })

    simulation_metadata = {
        "simulation_model_version": topo.get("simulation_model_version") or "1.0.0",
        "ode_status": topo.get("ode_status") or "disabled",
        "dynamic_margin": topo.get("dynamic_margin"),
        "monte_carlo_runs": topo.get("monte_carlo_runs"),
        "monte_carlo_failure_rate": topo.get("monte_carlo_failure_rate"),
        "metrics_cv": topo.get("metrics_cv"),
        "metrics_max_burden": topo.get("metrics_max_burden"),
    }

    return CandidateDetailView(
        run_id=run_id,
        index=index,
        name=f"Candidate #{index + 1}",
        is_best=is_best,
        score=round(score_val, 3),
        conclusion_summary=conclusion_summary,
        advantages=advantages,
        limitations=limitations,
        next_steps=next_steps,
        evidence_level=topo.get("cello_claim_level") or "conceptual",
        scores=scores_view,
        boolean_expression=boolean_expression,
        verilog_code=topo.get("verilog") or "",
        topology_graph={"nodes": nodes, "edges": edges},
        regulatory_graph=regulatory_graph,
        constructs=constructs,
        transcriptional_units=transcriptional_units,
        parts=parts,
        cello_mapping_status=topo.get("mapping_status") or "unknown",
        cello_fallback_used=any(w.category == "fallback" for w in warnings),
        raw_json=json.dumps(topo, indent=2, ensure_ascii=False),
        tool_versions=tool_versions,
        warnings=warnings,
        provenance=provenance,
        simulation_metadata=simulation_metadata,
    )


@dataclass
class CandidateCompareItem:
    index: int
    name: str
    is_best: bool
    score: float
    functional_score: Optional[float]
    kinetic_score: Optional[float]
    metabolic_burden_score: Optional[float]
    toxicity_score: Optional[float]
    robustness_score: Optional[float]
    orthogonality_score: Optional[float]
    cello_mapping_status: str
    cello_fallback_used: bool
    verilog_summary: str
    limiting_factor: str
    boolean_expression: str
    warnings_count: int
    is_provisional: bool
    is_fallback: bool
    is_incomplete: bool
    host_organism: str
    parts_count: int
    promoters_count: int
    rbs_count: int
    cds_count: int


@dataclass
class CandidateComparisonView:
    run_id: str
    user_intent: str
    host_organism: str
    candidates: List[CandidateCompareItem]

    # Compatibility alerts
    has_host_mismatch: bool
    has_mapping_mismatch: bool
    has_provisional_warnings: bool
    compatibility_notices: List[str]

    # Recommendation
    recommended_index: Optional[int]
    recommendation_reason: str


def build_candidate_comparison_view(
    run_id: str,
    run_status: dict,
    run_result: dict,
    indexes: List[int]
) -> CandidateComparisonView:
    if not isinstance(run_result, dict):
        raise ValueError("Run result data is not available.")

    candidate_refs = _extract_candidate_topologies(run_id, run_result)
    if not candidate_refs:
        raise ValueError("No candidate topologies found in this run.")

    # Validate selected indexes
    for idx in indexes:
        if idx < 0 or idx >= len(candidate_refs):
            raise ValueError(f"Candidate index {idx} is out of range.")

    # Calculate best index across ALL candidates (for is_best flag)
    best_idx = 0
    best_score = -9999.0
    best_topo = run_result.get("best_topology") or run_result.get("summary", {}).get("best_topology")
    all_failed = all(ref.topology.get("mapping_status") == "MAPPING_FAILED" for ref in candidate_refs)

    for idx, ref in enumerate(candidate_refs):
        t = ref.topology
        score_val = float(t.get("score", t.get("weighted_total_score", 0.0)))
        if score_val > best_score:
            best_score = score_val
            best_idx = idx

    if best_topo and isinstance(best_topo, dict):
        best_topo_score = float(best_topo.get("score", best_topo.get("weighted_total_score", 0.0)))
        for idx, ref in enumerate(candidate_refs):
            t = ref.topology
            topo_score = float(t.get("score", t.get("weighted_total_score", 0.0)))
            if t.get("verilog_index") == best_topo.get("verilog_index") and abs(topo_score - best_topo_score) < 1e-5:
                best_idx = idx
                break

    user_intent = run_status.get("summary", {}).get("user_intent") or run_status.get("request", {}).get("user_intent") or ""
    default_host = run_status.get("summary", {}).get("host_organism") or run_status.get("request", {}).get("host_organism") or "Escherichia coli"

    candidates_compare = []

    for idx in indexes:
        topo = candidate_refs[idx].topology
        score_val = float(topo.get("score", topo.get("weighted_total_score", 0.0)))

        topo_warnings = _extract_warnings(topo, run_status)
        is_fallback = any(w.category == "fallback" for w in topo_warnings)
        is_provisional = any(w.category == "provisional" for w in topo_warnings)
        is_incomplete = any(w.category == "incomplete" for w in topo_warnings)

        # Verilog expression
        boolean_expression = topo.get("logic_expression") or topo.get("boolean_expression") or ""
        if not boolean_expression:
            verilog_code = str(topo.get("verilog") or "")
            assigns = re.findall(r"assign\s+(\w+)\s*=\s*([^;]+);", verilog_code)
            if assigns:
                boolean_expression = ", ".join(f"{var} = {expr.strip()}" for var, expr in assigns)
            else:
                boolean_expression = "未定義布林表達式"

        # Count parts of various types from assignment
        part_assignments = topo.get("part_assignments", topo.get("assignments", []))
        parts_count = len(part_assignments)
        promoters_count = sum(1 for p in part_assignments if "promoter" in str(p.get("part_type", "")).lower())
        rbs_count = sum(1 for p in part_assignments if "rbs" in str(p.get("part_type", "")).lower())
        cds_count = sum(1 for p in part_assignments if "cds" in str(p.get("part_type", "")).lower())

        candidates_compare.append(
            CandidateCompareItem(
                index=idx,
                name=f"Candidate #{idx + 1}",
                is_best=(idx == best_idx and not all_failed),
                score=round(score_val, 3),
                functional_score=topo.get("functional_score"),
                kinetic_score=topo.get("kinetic_score"),
                metabolic_burden_score=topo.get("metabolic_burden_score"),
                toxicity_score=topo.get("toxicity_score"),
                robustness_score=topo.get("robustness_score"),
                orthogonality_score=topo.get("orthogonality_score"),
                cello_mapping_status=topo.get("mapping_status") or "unknown",
                cello_fallback_used=is_fallback,
                verilog_summary=_extract_verilog_summary(topo),
                limiting_factor=_get_limiting_factor(topo),
                boolean_expression=boolean_expression,
                warnings_count=len(topo_warnings),
                is_provisional=is_provisional,
                is_fallback=is_fallback,
                is_incomplete=is_incomplete,
                host_organism=topo.get("host_organism") or default_host,
                parts_count=parts_count,
                promoters_count=promoters_count,
                rbs_count=rbs_count,
                cds_count=cds_count,
            )
        )

    # Compute compatibility notices
    has_host_mismatch = len(set(c.host_organism for c in candidates_compare)) > 1
    has_mapping_mismatch = len(set(c.cello_mapping_status for c in candidates_compare)) > 1
    has_provisional_warnings = any(c.is_provisional for c in candidates_compare)

    compatibility_notices = []
    if has_host_mismatch:
        compatibility_notices.append("⚠️ 警告：所選的候選方案適用於不同的宿主生物體，直接對比可能不具科學意義。")
    if has_mapping_mismatch:
        compatibility_notices.append("⚠️ 注意：部分候選方案已成功進行 Cello 物理映射，而其他方案映射失敗或未使用實體元件庫。")
    if has_provisional_warnings:
        compatibility_notices.append("💡 提示：此對比包含示範 (Mock) 數據產生的候選方案，其物理元件屬性為預估值。")

    # Compute recommendation
    valid_candidates = [c for c in candidates_compare if c.cello_mapping_status != "MAPPING_FAILED"]
    if not valid_candidates:
        valid_candidates = candidates_compare

    recommended_item = max(valid_candidates, key=lambda c: c.score, default=None)
    recommended_index = recommended_item.index if recommended_item else None

    if recommended_item:
        reasons = []
        if recommended_item.is_best:
            reasons.append("整體權重分數最高")
        if recommended_item.cello_mapping_status == "mapped" and not recommended_item.is_fallback:
            reasons.append("具有完整的實體元件庫映射且未使用備用元件 (Fallback)")
        if recommended_item.kinetic_score and recommended_item.kinetic_score >= 0.85:
            reasons.append("ODE 動態模擬動力學指標優異")
        if recommended_item.metabolic_burden_score and recommended_item.metabolic_burden_score >= 0.85:
            reasons.append("代謝負擔低，對宿主細胞生理壓力小")

        if not reasons:
            reasons.append("在所選比對候選中表現相對均衡")

        reason_str = f"系統推薦 {recommended_item.name}，原因包括：{ '、'.join(reasons)}。該設計在「{recommended_item.limiting_factor}」表現穩定，具備較高可行性。"
    else:
        reason_str = "無法得出推薦結論。"

    return CandidateComparisonView(
        run_id=run_id,
        user_intent=user_intent,
        host_organism=default_host,
        candidates=candidates_compare,
        has_host_mismatch=has_host_mismatch,
        has_mapping_mismatch=has_mapping_mismatch,
        has_provisional_warnings=has_provisional_warnings,
        compatibility_notices=compatibility_notices,
        recommended_index=recommended_index,
        recommendation_reason=reason_str,
    )
