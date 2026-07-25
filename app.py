from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import json
import math
import re
import uuid
from dataclasses import asdict
from typing import Any

try:
    import streamlit as st
except Exception:  # pragma: no cover
    st = None

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None

from schemas.design_diff import compare_designs
from schemas.design_ir import DesignIR, topology_to_design_ir
from schemas.design_operations import replace_part_immutable, validate_replacement
from schemas.state import DesignState, SearchNode
from schemas.workflow_evidence import (
    is_valid_ode_trace as _valid_ode_trace,
    project_ode_trace_rows as _ode_trace_rows,
)
from agents.pm_agent import call_pm_agent
from utils import llm_utils
from mcp_server.ode_explainer import explain_ode_topology
from tools.part_library import PartLibrary
from exporters.bom_exporter import export_bom_csv
from exporters.genbank_exporter import export_genbank
from exporters.sbol3_exporter import export_sbol3_turtle
from exporters.plasmid_assembler import export_plasmid_genbank
from ui.external_import import (
    ensure_external_import_state,
    render_external_import_sidebar,
    render_external_import_workspace,
)


MODE_COLORS = {
    "Exploration": "#2563eb",
    "Repair": "#d97706",
    "Exploitation": "#059669",
}

MODE_LABELS = {
    "Exploration": "探索",
    "Repair": "修正",
    "Exploitation": "最佳化",
}

STATUS_COLORS = {
    "Pending": "#64748b",
    "Evaluated": "#2563eb",
    "Pass": "#059669",
    "Dead_End": "#dc2626",
    "Needs_Human_Input": "#ea580c",
}

STATUS_LABELS = {
    "Pending": "待處理",
    "Evaluated": "已評估",
    "Pass": "通過",
    "Dead_End": "無可行分支",
    "Needs_Human_Input": "需要人工輸入",
}

ERROR_COLORS = {
    "NONE": "#059669",
    "LOGIC_ERROR": "#d97706",
    "PART_ERROR": "#7c3aed",
    "BOTH": "#dc2626",
}

ERROR_LABELS = {
    "NONE": "無",
    "LOGIC_ERROR": "邏輯問題",
    "PART_ERROR": "元件問題",
    "BOTH": "邏輯與元件問題",
}

SCORE_COMPONENTS = [
    {
        "key": "functional",
        "label": "Functional",
        "weight": 0.22,
        "aliases": ["functional_score", "semantic_faithfulness_score"],
        "evidence": "檢查需求、truth table、布林邏輯與 Verilog 是否一致。",
        "caveat": "功能一致不代表 promoter、repressor 或宿主條件已被實驗驗證。",
    },
    {
        "key": "kinetic",
        "label": "Kinetic",
        "weight": 0.15,
        "aliases": ["kinetic_score", "dynamic_margin"],
        "evidence": "根據 ODE 動態訊號、dynamic margin 或輸出反應品質估計。",
        "caveat": "ODE 是簡化模型，參數來源與宿主情境會影響可信度。",
    },
    {
        "key": "static_plausibility",
        "label": "Static plausibility",
        "weight": 0.08,
        "aliases": ["static_plausibility_score"],
        "evidence": "檢查拓樸結構、重複元件、邏輯深度與明顯結構風險。",
        "caveat": "結構合理仍不等於序列層級或 cloning strategy 已完成。",
    },
    {
        "key": "metabolic_burden",
        "label": "Metabolic burden",
        "weight": 0.15,
        "aliases": ["metabolic_burden_score"],
        "evidence": "以 gate count、資源佔用與調控複雜度估計負擔。",
        "caveat": "這是早期篩選訊號，不是實測 growth burden。",
    },
    {
        "key": "robustness",
        "label": "Robustness",
        "weight": 0.15,
        "aliases": ["robustness_score"],
        "evidence": "檢查參數擾動或雜訊條件下輸出是否穩定。",
        "caveat": "若未執行 Monte Carlo 或缺少參數分布，可信度較有限。",
    },
    {
        "key": "temporal",
        "label": "Temporal",
        "weight": 0.05,
        "aliases": ["temporal_score"],
        "evidence": "檢查反應時間、rise time 或時間序列表現。",
        "caveat": "時間尺度仍需以實驗量測或文獻參數校準。",
    },
    {
        "key": "orthogonality",
        "label": "Orthogonality",
        "weight": 0.10,
        "aliases": ["orthogonality_score"],
        "evidence": "估計元件是否可能具有低交互干擾或符合 Cello 約束。",
        "caveat": "正交性通常需要實際 part library 與交互作用資料支持。",
    },
    {
        "key": "cello_assignment",
        "label": "Cello assignment",
        "weight": 0.10,
        "aliases": ["cello_assignment_score"],
        "evidence": "檢查是否取得 Cello mapping 或可用 part assignment。",
        "caveat": "mock mapping 只能代表流程可跑，不能視為真實可建構結果。",
    },
]


def main() -> None:
    if st is None:
        print("尚未安裝 Streamlit。請先安裝 requirements，然後執行 `streamlit run app.py`。")
        return

    st.set_page_config(page_title="基因電路設計器", layout="wide")
    _inject_styles()
    _ensure_session_state()
    st.warning(
        "注意：此 Streamlit 介面已進入維護模式（Legacy / Maintenance-only）。"
        "所有新功能與更新僅在正式的 HTML 前端（http://127.0.0.1:8000/web）提供，推薦使用正式工作台。"
    )
    _render_tutorial()

    state = st.session_state.design_state
    _render_sidebar(state)

    # Detect user intent changes and trigger state reset
    current_intent = state.user_intent.strip()
    last_intent = st.session_state.get("last_seen_intent", "").strip()
    if current_intent != last_intent:
        state.pm_stage = "elicitation"
        state.structured_spec = {}
        state.pm_chat_history = []
        state.pending_proposal = {}
        state.tree_nodes = {}
        state.active_frontier = []
        state.best_topology = None
        state.is_completed = False
        state.requires_human_input = False
        state.iteration_count = 0
        state.last_error = None
        st.session_state.last_seen_intent = current_intent
        st.session_state.pm_show_custom_input = False


    st.markdown(
        """
        <div class="app-header">
            <div>
                <h1>基因電路設計器</h1>
                <p>將自然語言需求轉換為 Cello 相容基因電路，整合樹狀搜尋、邏輯設計知識與評審回饋修正。</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    _render_status_strip(state)
    _render_human_loop_panel(state)
    render_external_import_workspace(st, _generated_designs_for_comparison(state))

    if getattr(state, "pm_stage", "elicitation") == "elicitation":
        _render_pm_elicitation_dashboard(state)
    else:
        work_col, inspector_col = st.columns([1.45, 1], gap="large")
        with work_col:
            _render_pipeline(state)
            _render_chart_overview(state)
            _render_tree_workspace(state)

        with inspector_col:
            _render_inspector(state)



def _generated_designs_for_comparison(state: DesignState) -> list[DesignIR]:
    designs = []
    for index, topology in enumerate(state.candidate_topologies, start=1):
        designs.append(
            topology_to_design_ir(
                topology,
                host_organism=state.host_organism,
                design_id=f"generated_{index}",
            )
        )
    return designs


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
            .block-container {
                padding-top: 1.6rem;
                padding-bottom: 2rem;
            }
            .app-header {
                border-bottom: 1px solid #e2e8f0;
                margin-bottom: 1rem;
                padding-bottom: 0.75rem;
            }
            .app-header h1 {
                color: #0f172a;
                font-size: 2rem;
                font-weight: 760;
                letter-spacing: 0;
                margin: 0;
            }
            .app-header p {
                color: #475569;
                font-size: 0.95rem;
                margin: 0.25rem 0 0 0;
            }
            .section-title {
                color: #0f172a;
                font-size: 1rem;
                font-weight: 720;
                margin: 1rem 0 0.45rem 0;
            }
            .metric-card {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                min-height: 88px;
                padding: 0.85rem 0.95rem;
            }
            .metric-label {
                color: #64748b;
                font-size: 0.72rem;
                font-weight: 700;
                letter-spacing: 0.03em;
                text-transform: uppercase;
            }
            .metric-value {
                color: #0f172a;
                font-size: 1.25rem;
                font-weight: 760;
                line-height: 1.25;
                margin-top: 0.3rem;
                overflow-wrap: anywhere;
            }
            .pill {
                border-radius: 999px;
                color: #ffffff;
                display: inline-block;
                font-size: 0.75rem;
                font-weight: 720;
                line-height: 1;
                padding: 0.38rem 0.58rem;
                white-space: nowrap;
            }
            .step-grid {
                display: grid;
                gap: 0.45rem;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                margin-bottom: 0.8rem;
            }
            .step {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                min-height: 70px;
                padding: 0.65rem;
            }
            .step.active {
                border-color: #2563eb;
                box-shadow: inset 0 0 0 1px #2563eb;
            }
            .step.done {
                border-color: #059669;
            }
            .step-label {
                color: #0f172a;
                font-size: 0.82rem;
                font-weight: 720;
            }
            .step-caption {
                color: #64748b;
                font-size: 0.74rem;
                margin-top: 0.2rem;
            }
            .empty-state {
                background: #f8fafc;
                border: 1px dashed #cbd5e1;
                border-radius: 8px;
                color: #475569;
                padding: 1rem;
            }
            .node-card {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                margin-bottom: 0.55rem;
                padding: 0.75rem;
            }
            .node-title {
                color: #0f172a;
                font-size: 0.9rem;
                font-weight: 760;
                overflow-wrap: anywhere;
            }
            .node-meta {
                color: #64748b;
                font-size: 0.75rem;
                margin-top: 0.25rem;
            }
            .topology-card {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                margin-bottom: 0.85rem;
                padding: 0.85rem;
            }
            .topology-card.best {
                border-color: #059669;
                box-shadow: inset 0 0 0 1px #059669;
            }
            .topology-title {
                color: #0f172a;
                font-size: 0.95rem;
                font-weight: 760;
            }
            .topology-subtitle {
                color: #64748b;
                font-size: 0.75rem;
                margin-top: 0.2rem;
            }
            .topology-metrics {
                display: grid;
                gap: 0.4rem;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                margin-top: 0.7rem;
            }
            .topology-metric {
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                min-height: 58px;
                padding: 0.5rem;
            }
            .topology-metric-label {
                color: #64748b;
                font-size: 0.68rem;
                font-weight: 700;
                text-transform: uppercase;
            }
            .topology-metric-value {
                color: #0f172a;
                font-size: 0.88rem;
                font-weight: 760;
                margin-top: 0.18rem;
                overflow-wrap: anywhere;
            }
            .code-panel {
                background: #0f172a;
                border-radius: 8px;
                color: #e2e8f0;
                font-family: Consolas, monospace;
                font-size: 0.8rem;
                overflow-x: auto;
                padding: 0.8rem;
                white-space: pre;
            }
            .hitl-panel {
                background: #fff7ed;
                border: 1px solid #fed7aa;
                border-radius: 8px;
                margin: 0.9rem 0 1rem 0;
                padding: 0.95rem;
            }
            .hitl-panel h2 {
                color: #9a3412;
                font-size: 1rem;
                font-weight: 760;
                letter-spacing: 0;
                margin: 0 0 0.35rem 0;
            }
            .hitl-panel p {
                color: #7c2d12;
                font-size: 0.88rem;
                margin: 0.25rem 0;
            }
            .hitl-meta {
                color: #9a3412;
                font-size: 0.78rem;
                font-weight: 700;
                margin-top: 0.55rem;
            }
            .explain-grid {
                display: grid;
                gap: 0.65rem;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                margin: 0.55rem 0 0.8rem 0;
            }
            .explain-card {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                min-height: 92px;
                padding: 0.85rem;
            }
            .explain-card.warning {
                background: #fffbeb;
                border-color: #fde68a;
            }
            .explain-card.success {
                background: #f0fdf4;
                border-color: #bbf7d0;
            }
            .explain-label {
                color: #64748b;
                font-size: 0.72rem;
                font-weight: 760;
                text-transform: uppercase;
            }
            .explain-value {
                color: #0f172a;
                font-size: 0.96rem;
                font-weight: 760;
                margin-top: 0.28rem;
            }
            .explain-text {
                color: #475569;
                font-size: 0.82rem;
                margin-top: 0.3rem;
            }
            .score-row {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                margin-bottom: 0.55rem;
                padding: 0.75rem;
            }
            .score-row-head {
                align-items: center;
                display: flex;
                gap: 0.55rem;
                justify-content: space-between;
            }
            .score-row-title {
                color: #0f172a;
                font-size: 0.9rem;
                font-weight: 760;
            }
            .score-row-score {
                color: #0f172a;
                font-size: 0.82rem;
                font-weight: 760;
                white-space: nowrap;
            }
            .score-bar {
                background: #e2e8f0;
                border-radius: 999px;
                height: 0.45rem;
                margin-top: 0.5rem;
                overflow: hidden;
            }
            .score-bar-fill {
                background: #2563eb;
                height: 100%;
            }
            .timeline-item {
                border-left: 3px solid #cbd5e1;
                margin: 0 0 0.65rem 0.3rem;
                padding: 0.05rem 0 0.55rem 0.8rem;
            }
            .timeline-item.current {
                border-left-color: #2563eb;
            }
            .timeline-title {
                color: #0f172a;
                font-size: 0.9rem;
                font-weight: 760;
            }
            .timeline-meta {
                color: #64748b;
                font-size: 0.76rem;
                margin-top: 0.18rem;
            }
            .timeline-body {
                color: #334155;
                font-size: 0.82rem;
                margin-top: 0.32rem;
            }
            .cello-claim {
                border-radius: 8px;
                font-size: 0.84rem;
                margin: 0.65rem 0;
                padding: 0.72rem 0.82rem;
            }
            .cello-claim.mock {
                background: #fff7ed;
                border: 1px solid #fed7aa;
                color: #7c2d12;
            }
            .cello-claim.failed {
                background: #fef2f2;
                border: 1px solid #fecaca;
                color: #7f1d1d;
            }
            .cello-claim.real {
                background: #ecfdf5;
                border: 1px solid #a7f3d0;
                color: #064e3b;
            }
            .cello-claim.unknown {
                background: #f8fafc;
                border: 1px solid #cbd5e1;
                color: #334155;
            }
            .cello-claim-title {
                font-weight: 760;
                margin-bottom: 0.2rem;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _ensure_session_state() -> None:
    if "design_state" not in st.session_state:
        st.session_state.design_state = DesignState()
    if "selected_node_id" not in st.session_state:
        st.session_state.selected_node_id = None
    if "ui_options" not in st.session_state:
        st.session_state.ui_options = {
            "enable_skill_context": True,
            "enable_rag": True,
            "enable_ode": True,
            "enable_tree_search": True,
            "enable_cache": True,
            "cello_command": "",
            "ucf_path": "",
            "part_library_path": "",
            "sensor_path": "",
            "device_path": "",
        }
    if "llm_config" not in st.session_state:
        st.session_state.llm_config = {
            "provider": "OpenAI",
            "model_name": "gpt-5.4-mini",
            "api_base": "",
            "api_key": "",
        }
    if "run_message" not in st.session_state:
        st.session_state.run_message = None
    if "show_tutorial" not in st.session_state:
        st.session_state.show_tutorial = False
    ensure_external_import_state(st.session_state)


def _render_tutorial() -> None:
    if not st.session_state.get("show_tutorial", False):
        return

    if hasattr(st, "dialog"):
        @st.dialog("📖 系統使用導覽")
        def tutorial_dialog():
            st.markdown(
                """
                ### 歡迎使用基因電路設計器！
                這是一個將自然語言轉換為基因電路的自動化工具。以下是簡單的使用步驟：

                1. **輸入需求**：在左側的「設計需求」框中，用自然語言描述想要的基因電路功能（例如：A 和 B 同時存在時輸出 Y）。
                2. **設定參數**：選擇宿主生物、調整計算預算，並開關邏輯設計知識、ODE 模擬等功能。
                3. **執行生成**：
                   - **示範模式**：點擊「執行示範迭代」或「執行示範搜尋」，體驗系統流程。
                   - **自備金鑰**：於「自備金鑰模型設定」輸入 API Key 後，點擊「執行自備金鑰工作流程」。
                4. **檢視與分析**：在右側「結果檢視器」切換分頁，查看邏輯提案、Verilog、拓樸與評審回饋。
                5. **下載狀態**：點擊左側底部的「匯出狀態 JSON」保存您的設計進度。
                """
            )
            if st.button("開始使用", use_container_width=True):
                st.session_state.show_tutorial = False
                st.rerun()
        tutorial_dialog()
    else:
        st.info(
            "**📖 系統使用導覽**\n\n"
            "歡迎使用基因電路設計器！以下是簡單的使用步驟：\n\n"
            "1. **輸入需求**：在左側的「設計需求」框中，用自然語言描述想要的基因電路功能。\n"
            "2. **設定參數**：選擇宿主生物、調整計算預算，並開關邏輯設計知識、ODE 模擬等功能。\n"
            "3. **執行生成**：點擊「執行示範迭代」體驗系統流程，或於設定 API Key 後「執行自備金鑰工作流程」。\n"
            "4. **檢視與分析**：在右側「結果檢視器」切換分頁，查看邏輯提案、Verilog、拓樸與評審回饋。\n"
            "5. **下載狀態**：點擊左側底部的「匯出狀態 JSON」保存您的設計進度。"
        )
        if st.button("我知道了", key="close_tutorial_inline"):
            st.session_state.show_tutorial = False
            st.rerun()


def _render_sidebar(state: DesignState) -> None:
    with st.sidebar:
        st.header("設計控制")

        st.button(
            "📖 使用導覽",
            use_container_width=True,
            on_click=lambda: st.session_state.update(show_tutorial=True),
        )

        state.user_intent = st.text_area(
            "設計需求",
            value=state.user_intent,
            height=140,
            placeholder="範例：設計一個只有在 A 高、B 低時才啟動 GFP 的基因電路。",
        )
        host_options = ["Escherichia coli", "Saccharomyces cerevisiae", "Bacillus subtilis", "自訂"]
        state.host_organism = st.selectbox(
            "宿主生物",
            host_options,
            index=_safe_index(host_options, state.host_organism),
        )
        if state.host_organism == "自訂":
            state.host_organism = st.text_input("自訂宿主", value="自訂宿主")

        state.compute_budget = st.slider("計算預算", min_value=1, max_value=20, value=state.compute_budget)

        st.subheader("工作流程選項")
        options = st.session_state.ui_options
        if "enable_skill_context" not in options:
            options["enable_skill_context"] = options.get("enable_rag", True)
        options["enable_skill_context"] = st.toggle("邏輯設計知識", value=options["enable_skill_context"])
        options["enable_rag"] = options["enable_skill_context"]
        options["enable_ode"] = st.toggle("ODE 模擬", value=options["enable_ode"])
        options["enable_tree_search"] = st.toggle("多代理樹狀搜尋", value=options["enable_tree_search"])
        options["enable_cache"] = st.toggle("快取", value=options["enable_cache"])

        _render_byok_controls()
        _render_cello_controls()
        render_external_import_sidebar(st)

        st.subheader("執行")
        if st.button("執行示範迭代", type="primary", use_container_width=True):
            if not state.user_intent.strip():
                st.session_state.run_message = ("warning", "請先輸入設計需求再執行工作流程。")
            else:
                _run_demo_iteration(state)
            st.rerun()

        if st.button("執行示範搜尋", use_container_width=True):
            if not state.user_intent.strip():
                st.session_state.run_message = ("warning", "請先輸入設計需求再執行工作流程。")
            else:
                if not state.tree_nodes:
                    _run_demo_iteration(state)
                while state.active_frontier and not state.is_completed and state.used_budget < state.compute_budget:
                    _run_demo_iteration(state)
            st.rerun()

        if st.button("執行自備金鑰工作流程", use_container_width=True):
            _run_byok_workflow(state)
            st.rerun()

        if st.button("重設", use_container_width=True):
            st.session_state.design_state = DesignState()
            st.session_state.selected_node_id = None
            st.session_state.run_message = None
            st.rerun()

        st.download_button(
            "匯出狀態 JSON",
            data=json.dumps(asdict(state), indent=2, ensure_ascii=False),
            file_name="genetic_circuit_design_state.json",
            mime="application/json",
            use_container_width=True,
        )

        _render_run_message()
        st.caption("示範執行會產生可重現的範例節點。自備金鑰執行只會在目前工作階段使用你的 API key，不會匯出。")


def _render_status_strip(state: DesignState) -> None:
    best_score = _best_score(state)
    active_node = state.current_node_id or "尚未開始"
    status = "已完成" if state.is_completed else ("執行中" if state.tree_nodes else "待命")
    budget_text = f"{state.used_budget} / {state.compute_budget}"
    error_type = state.error_type
    if state.current_node_id and state.current_node_id in state.tree_nodes:
        error_type = state.tree_nodes[state.current_node_id].error_type

    cols = st.columns(5, gap="small")
    cards = [
        ("狀態", status),
        ("預算", budget_text),
        ("目前節點", active_node),
        ("最佳分數", "無資料" if best_score is None else f"{best_score:.2f}"),
        ("最新問題", ERROR_LABELS.get(error_type, error_type)),
    ]
    for col, (label, value) in zip(cols, cards):
        with col:
            st.markdown(
                f"""
                <div class="metric-card">
                    <div class="metric-label">{label}</div>
                    <div class="metric-value">{value}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def _render_byok_controls() -> None:
    config = st.session_state.llm_config
    model_presets = {
        "OpenAI": ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano"],
        "OpenRouter": [
            "openrouter/openai/gpt-5.4",
            "openrouter/anthropic/claude-sonnet-4-6",
            "openrouter/google/gemini-3.1-pro-preview",
        ],
        "Anthropic": [
            "anthropic/claude-sonnet-4-6",
            "anthropic/claude-opus-4-7",
            "anthropic/claude-haiku-4-5-20251001",
        ],
        "Google": [
            "gemini/gemini-3.5-flash",
            "gemini/gemini-3.1-pro-preview",
            "gemini/gemini-3.1-flash-lite",
            "gemini/gemini-2.5-pro",
        ],
        "Groq": [
            "groq/meta-llama/llama-4-scout-17b-16e-instruct",
            "groq/llama-3.1-8b-instant",
            "groq/qwen/qwen3-32b",
        ],
        "Custom LiteLLM": [config.get("model_name", "custom/model") or "custom/model"],
    }

    with st.expander("自備金鑰模型設定", expanded=False):
        provider_names = list(model_presets)
        config["provider"] = st.selectbox(
            "服務提供者",
            provider_names,
            index=_safe_index(provider_names, config.get("provider")),
        )
        preset_models = model_presets[config["provider"]]
        selected_model = st.selectbox(
            "模型預設",
            preset_models,
            index=_safe_index(preset_models, config.get("model_name")),
        )
        config["model_name"] = st.text_input(
            "LiteLLM 模型名稱",
            value=config.get("model_name") or selected_model,
            placeholder=selected_model,
        )
        config["api_key"] = st.text_input(
            "API key",
            value=config.get("api_key", ""),
            type="password",
            placeholder="貼上此工作階段要使用的服務金鑰",
        )
        config["api_base"] = st.text_input(
            "API base URL",
            value=config.get("api_base", ""),
            placeholder="選填，供 OpenRouter 或自架端點使用",
        )


def _render_cello_controls() -> None:
    options = st.session_state.ui_options
    with st.expander("Cello/UCF 物理映射設定", expanded=False):
        options["cello_command"] = st.text_input(
            "Cello CLI/Docker 指令",
            value=options.get("cello_command", ""),
            placeholder="留空則使用 Mock 模擬映射",
            help="例如：wsl podman run --rm -i -v {wsl_temp_dir}:/root/input ...",
        )
        options["ucf_path"] = st.text_input(
            "UCF 限制檔案路徑",
            value=options.get("ucf_path", ""),
            placeholder="例如：C:\\Cello\\Eco1C1G1T1.UCF.json",
        )
        options["part_library_path"] = st.text_input(
            "自訂元件庫 JSON 路徑",
            value=options.get("part_library_path", ""),
            placeholder="留空則使用預設 real sequences 元件庫",
        )
        options["sensor_path"] = st.text_input(
            "Sensor 輸入檔案路徑 (選填)",
            value=options.get("sensor_path", ""),
            placeholder="例如：C:\\Cello\\Eco1C1G1T1.input.json",
        )
        options["device_path"] = st.text_input(
            "Device 輸出檔案路徑 (選填)",
            value=options.get("device_path", ""),
            placeholder="例如：C:\\Cello\\Eco1C1G1T1.output.json",
        )


def _render_run_message() -> None:
    message = st.session_state.get("run_message")
    if not message:
        return
    level, text = message
    if level == "success":
        st.success(text)
    elif level == "error":
        st.error(text)
    else:
        st.warning(text)


def _render_human_loop_panel(state: DesignState) -> None:
    if not state.requires_human_input:
        return

    # Trigger PM Agent to translate raw error to friendly options if in hitl_dialogue stage
    if state.requires_human_input:
        if getattr(state, "pm_stage", None) != "hitl_dialogue" or not getattr(state, "pending_proposal", None) or "options" not in state.pending_proposal:
            state.pm_stage = "hitl_dialogue"
            config = st.session_state.get("llm_config", {})
            api_key = config.get("api_key", "").strip() or None
            model_name = config.get("model_name", "gpt-4o-mini").strip()
            api_base = config.get("api_base", "").strip() or None
            call_pm_agent(state, api_key=api_key, model_name=llm_utils.resolve_agent_model("pm", model_name), api_base=api_base)

    if getattr(state, "pm_stage", None) == "hitl_dialogue" and getattr(state, "pending_proposal", None) and "options" in state.pending_proposal:
        proposal = state.pending_proposal
        err_summary = proposal.get("error_summary_cn", "系統遇到設計瓶頸")
        ui_msg = proposal.get("ui_message", "請問您希望如何調整？")
        selected_node = _selected_node(state)

        st.markdown(
            f"""
            <div class="hitl-panel" style="border-left:5px solid #ea580c;">
                <h2 style="color:#ea580c;">需要人工介入 (設計經理協同)</h2>
                <p><strong>問題說明：</strong>{_escape_html(err_summary)}</p>
                <p style="margin-top:10px;"><strong>經理建議：</strong>{_escape_html(ui_msg)}</p>
                <div class="hitl-meta">目前節點：{_escape_html(selected_node.node_id if selected_node else "無")}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if state.human_constraints:
            with st.expander("已套用的人工限制", expanded=False):
                for constraint in state.human_constraints:
                    st.write(f"- {constraint}")

        for opt in proposal["options"]:
            opt_id = opt.get("option_id", "A")
            label = opt.get("label", "")
            action = opt.get("action", "Repair")
            constraints = opt.get("constraints", [])
            extra_budget = opt.get("extra_budget", 2)

            if st.button(f"👉 {opt_id}: {label}", key=f"pm_opt_{opt_id}", use_container_width=True, type="primary" if opt_id == "A" else "secondary"):
                state.human_constraints.extend(constraints)
                if extra_budget:
                    state.compute_budget += extra_budget
                if action == "Accept_Fallback":
                    _select_best_fallback(state)
                    state.is_completed = state.best_topology is not None
                else:
                    _create_guided_child(state, "Repair")

                state.requires_human_input = False
                state.pm_stage = "engine_running"
                state.pause_reason = None
                state.human_feedback_prompt = None
                state.pending_proposal = {}
                st.session_state.run_message = ("success", f"已套用設計經理方案 {opt_id}。請繼續執行下一輪搜尋。")
                st.rerun()

        if st.button("✏️ 切換為傳統手動輸入表單", use_container_width=True):
            state.pm_stage = "engine_running"
            state.pending_proposal = {}
            st.rerun()
        return

    # Fallback to normal human-loop panel if no options found
    selected_node = _selected_node(state)

    prompt = state.human_feedback_prompt or state.latest_critic_feedback or "系統需要更多限制或偏好，才能安全地繼續搜尋。"
    reason_label = _pause_reason_label(state.pause_reason)
    best_score = _best_score(state)
    fallback_text = "目前尚無可用的備用拓樸。"
    if state.best_topology:
        fallback_score = state.best_topology.get("score", best_score)
        fallback_mapping = state.best_topology.get("mapping_status", "unknown")
        fallback_text = f"目前最佳備用拓樸分數 {fallback_score}，mapping 狀態為 {fallback_mapping}。"
    escaped_fallback_text = _escape_html(fallback_text)

    st.markdown(
        f"""
        <div class="hitl-panel">
            <h2>需要人工介入</h2>
            <p><strong>暫停原因：</strong>{_escape_html(reason_label)}</p>
            <p><strong>系統請求：</strong>{_escape_html(prompt)}</p>
            <div class="hitl-meta">目前節點：{_escape_html(selected_node.node_id if selected_node else "無")} | {escaped_fallback_text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if state.human_constraints:
        with st.expander("已套用的人工限制", expanded=False):
            for constraint in state.human_constraints:
                st.write(f"- {constraint}")

    with st.form("human_loop_guidance_form"):
        guidance = st.text_area(
            "補充給系統的限制或偏好",
            height=110,
            placeholder="例如：優先降低 gate count；允許犧牲一點動態裕度；避免使用特定 promoter；保留目前最佳拓樸作為 fallback。",
        )
        action = st.radio(
            "下一步",
            [
                "加入限制並接續既有搜尋",
                "加入限制並建立修正分支",
                "加入限制並建立元件最佳化分支",
                "接受目前最佳拓樸作為結果",
            ],
            horizontal=False,
        )
        extra_budget = st.number_input("追加計算預算", min_value=0, max_value=20, value=2, step=1)
        submitted = st.form_submit_button("套用人工回饋", type="primary", use_container_width=True)

    if submitted:
        _apply_human_guidance(state, guidance, action, int(extra_budget))
        st.rerun()


def _pause_reason_label(reason: str | None) -> str:
    labels = {
        "compute_budget_exceeded": "計算預算已用完，尚未得到通過結果",
        "critic_requested_human_input": "評審代理判斷需要人工補充限制",
        "critic_unrecoverable": "評審代理判斷目前條件不足以自動修復",
        "repeated_error_type": "同類問題重複出現，需要改變搜尋方向",
        "no_recoverable_route": "評審回饋沒有明確可自動修復的路線",
        "frontier_exhausted": "所有搜尋分支都已耗盡",
    }
    return labels.get(reason or "", reason or "系統等待人工確認")


def _apply_human_guidance(state: DesignState, guidance: str, action: str, extra_budget: int) -> None:
    constraints = [line.strip("- ").strip() for line in guidance.splitlines() if line.strip()]
    state.human_constraints.extend(constraint for constraint in constraints if constraint)
    if extra_budget:
        state.compute_budget += extra_budget

    if action == "接受目前最佳拓樸作為結果":
        _select_best_fallback(state)
        state.is_completed = state.best_topology is not None
        state.requires_human_input = False
        state.pause_reason = None
        state.human_feedback_prompt = None
        st.session_state.run_message = (
            "success",
            "已接受目前最佳拓樸作為結果。" if state.best_topology else "目前沒有可接受的最佳拓樸，請改用修正或最佳化分支繼續搜尋。",
        )
        return

    if action == "加入限制並建立修正分支":
        _create_guided_child(state, "Repair")
    elif action == "加入限制並建立元件最佳化分支":
        _create_guided_child(state, "Exploitation")

    current_node = state.tree_nodes.get(state.current_node_id) if state.current_node_id else None
    if current_node and current_node.status == "Needs_Human_Input":
        current_node.status = "Evaluated"
    state.requires_human_input = False
    state.pause_reason = None
    state.human_feedback_prompt = None
    st.session_state.run_message = ("success", "已套用人工回饋。請繼續執行下一輪搜尋。")


def _create_guided_child(state: DesignState, search_mode: str) -> None:
    parent = state.tree_nodes.get(state.current_node_id) if state.current_node_id else _selected_node(state)
    if parent is None:
        return
    child_id = _child_id(parent.node_id, "repair" if search_mode == "Repair" else "exploit")
    child = SearchNode(
        node_id=child_id,
        parent_id=parent.node_id,
        search_mode=search_mode,
        logic_proposals=parent.logic_proposals[:] if search_mode == "Exploitation" else [],
        critic_feedbacks=parent.critic_feedbacks[:],
        failed_attempts=parent.failed_attempts[:],
        error_type=parent.error_type,
    )
    parent.children_ids.append(child_id)
    state.tree_nodes[child_id] = child
    state.active_frontier.insert(0, child_id)
    st.session_state.selected_node_id = child_id


def _render_pipeline(state: DesignState) -> None:
    st.markdown('<div class="section-title">工作流程進度</div>', unsafe_allow_html=True)
    current_step = _current_step(state)
    skill_context = getattr(state, "skill_context", "") or state.rag_context
    steps = [
        ("需求", bool(state.user_intent.strip()), "自然語言目標"),
        ("設計知識", bool(skill_context), "Motif 與記憶"),
        ("設計生成器", bool(state.logic_proposals), "邏輯提案"),
        ("轉譯器", bool(state.verilog_codes), "Cello Verilog"),
        ("Cello 映射", bool(state.candidate_topologies), "拓樸候選"),
        ("ODE 模擬", any("ode_status" in topo for topo in state.candidate_topologies), "動態分數"),
        ("評審代理", bool(state.critic_feedbacks), "回饋分流"),
        ("整合器", state.best_topology is not None, "最佳結果"),
    ]
    step_html = ['<div class="step-grid">']
    for index, (label, done, caption) in enumerate(steps):
        class_name = "step done" if done else "step"
        if index == current_step:
            class_name += " active"
        step_html.append(
            f'<div class="{class_name}">'
            f'<div class="step-label">{label}</div>'
            f'<div class="step-caption">{caption}</div>'
            "</div>"
        )
    step_html.append("</div>")
    st.markdown("".join(step_html), unsafe_allow_html=True)


def _render_chart_overview(state: DesignState) -> None:
    st.markdown('<div class="section-title">設計分析</div>', unsafe_allow_html=True)
    if not state.tree_nodes:
        st.markdown(
            '<div class="empty-state">工作流程產生已評估節點或拓樸候選後，這裡會顯示圖表。</div>',
            unsafe_allow_html=True,
        )
        return

    score_rows = _node_score_rows(state)
    topology_rows = _topology_chart_rows(state)
    left, right = st.columns(2, gap="medium")
    with left:
        st.caption("節點分數變化")
        if pd is not None and score_rows:
            chart_df = pd.DataFrame(score_rows).set_index("node")
            st.line_chart(chart_df[["score"]], use_container_width=True)
        else:
            st.info("目前尚無有效的節點分數。")
    with right:
        st.caption("候選拓樸分數")
        if pd is not None and topology_rows:
            chart_df = pd.DataFrame(topology_rows).set_index("candidate")
            st.bar_chart(chart_df[["score"]], use_container_width=True)
        else:
            st.info("目前尚無拓樸分數。")


def _render_tree_workspace(state: DesignState) -> None:
    st.markdown('<div class="section-title">樹狀搜尋工作區</div>', unsafe_allow_html=True)

    if not state.tree_nodes:
        st.markdown(
            '<div class="empty-state">請輸入設計需求，接著執行一次示範迭代來建立根搜尋節點。</div>',
            unsafe_allow_html=True,
        )
        return

    node_ids = list(state.tree_nodes.keys())
    default_node = st.session_state.selected_node_id or state.current_node_id or node_ids[0]
    selected = st.selectbox("檢視節點", node_ids, index=_safe_index(node_ids, default_node))
    st.session_state.selected_node_id = selected
    _render_search_path_panel(state, selected)

    table_rows = []
    for node in state.tree_nodes.values():
        table_rows.append(
            {
                "節點": node.node_id,
                "父節點": node.parent_id or "-",
                "模式": MODE_LABELS.get(node.search_mode, node.search_mode),
                "狀態": STATUS_LABELS.get(node.status, node.status),
                "分數": None if not math.isfinite(node.score) else round(node.score, 3),
                "問題": ERROR_LABELS.get(node.error_type, node.error_type),
                "子節點數": len(node.children_ids),
            }
        )

    if pd is not None:
        st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)
    else:
        st.table(table_rows)

    node = state.tree_nodes[selected]
    mode_color = MODE_COLORS.get(node.search_mode, "#64748b")
    status_color = STATUS_COLORS.get(node.status, "#64748b")
    error_color = ERROR_COLORS.get(node.error_type, "#64748b")
    st.markdown(
        f"""
        <div class="node-card">
            <div class="node-title">{node.node_id}</div>
            <div class="node-meta">
                <span class="pill" style="background:{mode_color};">{MODE_LABELS.get(node.search_mode, node.search_mode)}</span>
                <span class="pill" style="background:{status_color};">{STATUS_LABELS.get(node.status, node.status)}</span>
                <span class="pill" style="background:{error_color};">{ERROR_LABELS.get(node.error_type, node.error_type)}</span>
            </div>
            <div class="node-meta">父節點：{node.parent_id or "無"} | 子節點：{", ".join(node.children_ids) or "無"}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_search_path_panel(state: DesignState, selected_node_id: str) -> None:
    st.markdown('<div class="section-title">決策紀錄與搜尋路徑</div>', unsafe_allow_html=True)
    summary = _search_next_step_summary(state)
    if summary["level"] == "success":
        st.success(summary["text"])
    elif summary["level"] == "warning":
        st.warning(summary["text"])
    else:
        st.info(summary["text"])

    path = _search_path_to_node(state, selected_node_id)
    if not path:
        st.info("目前沒有可顯示的搜尋路徑。")
        return

    st.caption("關鍵決策時間線")
    _render_decision_timeline(state, path)

    dot = _build_search_tree_dot(state, selected_node_id)
    if dot:
        with st.expander("完整搜尋樹", expanded=False):
            st.graphviz_chart(dot, use_container_width=True)

    with st.expander("節點細節", expanded=False):
        st.caption("目前路徑")
        for depth, node in enumerate(path):
            _render_path_node_card(state, node, depth)


def _render_decision_timeline(state: DesignState, path: list[SearchNode]) -> None:
    for depth, node in enumerate(path):
        step = _decision_step_for_node(state, node, depth)
        current_class = " current" if node.node_id == state.current_node_id else ""
        st.markdown(
            f"""
            <div class="timeline-item{current_class}">
                <div class="timeline-title">{_escape_html(step["title"])}</div>
                <div class="timeline-meta">{_escape_html(step["meta"])}</div>
                <div class="timeline-body"><strong>做了什麼：</strong>{_escape_html(step["action"])}</div>
                <div class="timeline-body"><strong>為什麼：</strong>{_escape_html(step["reason"])}</div>
                <div class="timeline-body"><strong>結果：</strong>{_escape_html(step["result"])}</div>
                <div class="timeline-body"><strong>下一步：</strong>{_escape_html(step["next"])}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_path_node_card(state: DesignState, node: SearchNode, depth: int) -> None:
    reason = _branch_reason_for_node(state, node.node_id)
    feedback = _summarize_feedback(node.critic_feedbacks[-1] if node.critic_feedbacks else "")
    score = "無資料" if not math.isfinite(node.score) else f"{node.score:.2f}"
    badges = _node_state_badges(state, node)
    mode_color = MODE_COLORS.get(node.search_mode, "#64748b")
    status_color = STATUS_COLORS.get(node.status, "#64748b")
    error_color = ERROR_COLORS.get(node.error_type, "#64748b")
    mode_label = MODE_LABELS.get(node.search_mode, node.search_mode)
    status_label = STATUS_LABELS.get(node.status, node.status)
    error_label = ERROR_LABELS.get(node.error_type, node.error_type)
    st.markdown(
        f"""
        <div class="node-card">
            <div class="node-title">{depth + 1}. {_escape_html(node.node_id)}</div>
            <div class="node-meta">
                <span class="pill" style="background:{mode_color};">{mode_label}</span>
                <span class="pill" style="background:{status_color};">{status_label}</span>
                <span class="pill" style="background:{error_color};">{error_label}</span>
                {badges}
            </div>
            <div class="node-meta">分數：{score} | 建立原因：{_escape_html(reason)}</div>
            <div class="node-meta">評審摘要：{_escape_html(feedback or "尚無評審摘要")}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_inspector(state: DesignState) -> None:
    st.markdown('<div class="section-title">結果檢視器</div>', unsafe_allow_html=True)
    node = _selected_node(state)

    if node is None:
        st.markdown(
            '<div class="empty-state">尚未選取節點。執行工作流程後即可檢視提案、Verilog、拓樸與評審回饋。</div>',
            unsafe_allow_html=True,
        )
        return

    explanation_tab, proposal_tab, verilog_tab, topology_tab, compare_tab, ode_tab, charts_tab, critic_tab, stochastic_tab, skill_tab, raw_tab = st.tabs(
        ["解釋", "提案", "Verilog", "拓樸", "比較", "ODE 模擬", "圖表", "評審", "隨機驗證", "設計知識", "原始狀態"]
    )

    with explanation_tab:
        _render_explanation_tab(node, state)

    with proposal_tab:
        proposals = node.logic_proposals or state.logic_proposals
        if proposals:
            for index, proposal in enumerate(proposals, start=1):
                with st.expander(f"提案 {index}", expanded=index == 1):
                    _render_json_or_text(proposal)
        else:
            st.info("這個節點尚未產生邏輯提案。")

    with verilog_tab:
        codes = node.verilog_codes or state.verilog_codes
        if codes:
            selected_code = st.radio("Verilog 候選", [f"候選 {i + 1}" for i in range(len(codes))], horizontal=True)
            code_index = int(selected_code.split()[-1]) - 1
            st.markdown(f'<div class="code-panel">{_escape_html(codes[code_index])}</div>', unsafe_allow_html=True)
        else:
            st.info("這個節點尚未產生 Verilog。")

    with topology_tab:
        topologies = node.candidate_topologies or state.candidate_topologies
        if topologies:
            best_topology = node.best_topology or state.best_topology
            for index, topology in enumerate(topologies):
                _render_topology_card(index, topology, best_topology)
        else:
            st.info("目前尚無拓樸候選。")

    with compare_tab:
        _render_design_comparison_tab(node, state)

    with ode_tab:
        _render_ode_simulation_tab(node, state)

    with charts_tab:
        _render_topology_charts(node, state)

    with critic_tab:
        cols = st.columns(3)
        cols[0].metric("是否通過", "是" if node.is_approved else "否")
        cols[1].metric("問題類型", ERROR_LABELS.get(node.error_type, node.error_type))
        cols[2].metric("分數", "無資料" if not math.isfinite(node.score) else f"{node.score:.2f}")
        if node.critic_feedbacks:
            for feedback in node.critic_feedbacks:
                st.warning(feedback)
        elif state.critic_feedbacks:
            for feedback in state.critic_feedbacks:
                st.warning(feedback)
        else:
            st.info("目前尚無評審回饋。")
        if node.last_error:
            st.error(node.last_error)

    with stochastic_tab:
        _render_stochastic_tab(node, state)

    with skill_tab:
        skill_context = getattr(state, "skill_context", "") or state.rag_context
        if skill_context:
            st.text_area("Skill Context", value=skill_context, height=260)
        else:
            st.info("目前尚未載入邏輯設計知識。")

    with raw_tab:
        st.json(asdict(node))
        with st.expander("完整 DesignState"):
            st.json(asdict(state))


def _render_explanation_tab(node: SearchNode, state: DesignState) -> None:
    topology = node.best_topology or state.best_topology or _best_topology_from_list(node.candidate_topologies or state.candidate_topologies)
    if topology is None:
        st.info("目前還沒有可解釋的候選拓樸。請先執行一次示範迭代或自備金鑰工作流程。")
        return

    interpretation = _overall_interpretation(topology, node)
    strengths, limiting = _rank_score_components(topology)
    caveats = _topology_caveats(topology, node)
    next_action = _recommended_next_action(topology, node, caveats, limiting)

    st.markdown(
        f"""
        <div class="explain-grid">
            <div class="explain-card success">
                <div class="explain-label">整體判讀</div>
                <div class="explain-value">{_escape_html(interpretation["title"])}</div>
                <div class="explain-text">{_escape_html(interpretation["body"])}</div>
            </div>
            <div class="explain-card warning">
                <div class="explain-label">下一步建議</div>
                <div class="explain-value">{_escape_html(next_action["title"])}</div>
                <div class="explain-text">{_escape_html(next_action["body"])}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    score = _metric_value(topology, "score")
    mapping_status = str(topology.get("mapping_status", "unknown"))
    source = str(topology.get("source", "unknown"))
    cols = st.columns(4)
    cols[0].metric("總分", _format_metric(score))
    cols[1].metric("等級", _score_grade(score))
    cols[2].metric("Mapping", mapping_status)
    cols[3].metric("來源", source)
    _render_cello_claim_notice(topology)

    st.caption("主要加分證據")
    if strengths:
        for item in strengths[:2]:
            st.success(f"{item['label']}：{item['score']:.2f}。{item['evidence']}")
    else:
        st.info("目前缺少 component score；系統只能根據總分與候選拓樸欄位做粗略判讀。")

    st.caption("主要限制與拖累項目")
    if limiting:
        for item in limiting[:2]:
            reason = _component_limiting_reason(item, topology)
            st.warning(f"{item['label']}：{item['score']:.2f}。{reason}")
    else:
        st.info("目前沒有明顯拖累項目，或尚未提供足夠 component score。")

    if caveats:
        st.caption("這個分數不能證明什麼")
        for caveat in caveats[:4]:
            st.info(caveat)

    _render_component_score_overview(topology)

    with st.expander("完整 component score 解釋", expanded=False):
        for component in _score_component_items(topology):
            _render_score_component_row(component, topology)

    path = _search_path_to_node(state, node.node_id)
    if path:
        with st.expander("對應的設計決策紀錄", expanded=False):
            _render_decision_timeline(state, path)


def _render_component_score_overview(topology: dict[str, Any]) -> None:
    items = _score_component_items(topology)
    if not items:
        return
    if pd is None:
        return
    rows = [
        {
            "component": item["label"],
            "score": item["score"],
            "weighted_contribution": round(item["score"] * item["weight"], 3),
        }
        for item in items
    ]
    chart_df = pd.DataFrame(rows).set_index("component")
    left, right = st.columns(2, gap="medium")
    with left:
        st.caption("Component score")
        st.bar_chart(chart_df[["score"]], use_container_width=True)
    with right:
        st.caption("加權貢獻")
        st.bar_chart(chart_df[["weighted_contribution"]], use_container_width=True)


def _render_score_component_row(component: dict[str, Any], topology: dict[str, Any]) -> None:
    score = max(0.0, min(1.0, float(component["score"])))
    width = int(round(score * 100))
    st.markdown(
        f"""
        <div class="score-row">
            <div class="score-row-head">
                <div class="score-row-title">{_escape_html(component["label"])}</div>
                <div class="score-row-score">{score:.2f} · weight {component["weight"]:.0%}</div>
            </div>
            <div class="score-bar"><div class="score-bar-fill" style="width:{width}%;"></div></div>
            <div class="explain-text"><strong>證據：</strong>{_escape_html(component["evidence"])}</div>
            <div class="explain-text"><strong>限制：</strong>{_escape_html(component["caveat"])}</div>
            <div class="explain-text"><strong>修正含意：</strong>{_escape_html(_component_limiting_reason(component, topology))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_cello_claim_notice(topology: dict[str, Any]) -> None:
    notice = _cello_claim_notice(topology)
    st.markdown(
        f"""
        <div class="cello-claim {notice["level"]}">
            <div class="cello-claim-title">{_escape_html(notice["title"])}</div>
            <div>{_escape_html(notice["message"])}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_design_workspace(index: int, topology: dict[str, Any]) -> None:
    design = topology_to_design_ir(
        topology,
        host_organism=str(topology.get("host_organism", "Escherichia coli")),
        design_id=f"candidate_{int(topology.get('verilog_index', index)) + 1}",
    )
    st.markdown("#### Design workspace")
    _render_design_maturity(design)

    logic_tab, regulatory_tab, construct_tab, parts_tab, export_tab = st.tabs(
        ["Logic", "Regulatory", "DNA Construct", "Parts", "Export"]
    )
    with logic_tab:
        st.caption("Computational logic derived from the candidate Verilog.")
        st.code(design.logic_expression, language="text")
        graph = _verilog_to_gate_graph(str(topology.get("verilog", "") or ""))
        if graph["ok"]:
            st.graphviz_chart(str(graph["dot"]), use_container_width=True)
        else:
            st.warning(str(graph["message"]))

    with regulatory_tab:
        st.caption(
            "Conceptual biological interpretation. Nodes are functions and placeholders until a real part library is mapped."
        )
        if design.interactions:
            st.graphviz_chart(_build_regulatory_graph_dot(design), use_container_width=True)
        else:
            st.info("No regulatory interactions could be inferred from this candidate.")

    with construct_tab:
        st.caption("Conceptual 5' to 3' transcriptional units. This is not yet an assembly-ready plasmid.")
        _render_construct_view(design)
        for warning in design.warnings:
            st.warning(warning)

    with parts_tab:
        _render_part_library_view(design)

    with export_tab:
        revised = st.session_state.get(f"design_revision_{design.design_id}")
        export_design = revised if isinstance(revised, DesignIR) else design
        _render_design_exports(export_design)

    with st.expander("DesignIR data", expanded=False):
        st.json(design.to_dict())


def _render_design_maturity(design: DesignIR) -> None:
    labels = {
        "logic": "Logic",
        "regulatory_model": "Regulatory model",
        "part_mapping": "Part mapping",
        "sequences": "Sequences",
        "assembly_ready": "Assembly ready",
    }
    colors = {
        "available": ("#dcfce7", "#166534"),
        "external_mapping": ("#dcfce7", "#166534"),
        "conceptual": ("#fef3c7", "#92400e"),
        "missing": ("#fee2e2", "#991b1b"),
        "no": ("#fee2e2", "#991b1b"),
    }
    badges = []
    for key, label in labels.items():
        status = design.validation_status.get(key, "unknown")
        background, foreground = colors.get(status, ("#e2e8f0", "#334155"))
        badges.append(
            f'<span style="display:inline-block;margin:0 0.35rem 0.35rem 0;'
            f'padding:0.28rem 0.55rem;border-radius:999px;background:{background};'
            f'color:{foreground};font-size:0.78rem;font-weight:650;">'
            f'{_escape_html(label)}: {_escape_html(status.replace("_", " "))}</span>'
        )
    st.markdown("".join(badges), unsafe_allow_html=True)


def _build_regulatory_graph_dot(design: DesignIR) -> str:
    part_map = {part.id: part for part in design.parts}
    lines = [
        "digraph RegulatoryGraph {",
        '  graph [rankdir=LR, bgcolor="transparent", pad="0.2", nodesep="0.4", ranksep="0.7"];',
        '  node [fontname="Arial", fontsize=10, style="rounded,filled", shape=box];',
    ]
    colors = {
        "sensor": ("#dbeafe", "#60a5fa"),
        "promoter": ("#fef3c7", "#f59e0b"),
        "RBS": ("#ede9fe", "#8b5cf6"),
        "CDS": ("#dcfce7", "#22c55e"),
        "terminator": ("#fee2e2", "#ef4444"),
    }
    used_ids = {edge.source for edge in design.interactions} | {edge.target for edge in design.interactions}
    for part_id in sorted(used_ids):
        part = part_map.get(part_id)
        if part is None:
            continue
        fill, border = colors.get(part.part_type, ("#f1f5f9", "#94a3b8"))
        label = _dot_escape(f"{part.name}\\n{part.part_type}")
        lines.append(
            f'  {_dot_id(part.id)} [label="{label}", fillcolor="{fill}", color="{border}"];'
        )
    for edge in design.interactions:
        edge_color = "#dc2626" if edge.interaction_type == "repression" else "#2563eb"
        arrow = "tee" if edge.interaction_type == "repression" else "normal"
        label = _dot_escape(edge.interaction_type)
        lines.append(
            f'  {_dot_id(edge.source)} -> {_dot_id(edge.target)} '
            f'[label="{label}", color="{edge_color}", arrowhead="{arrow}"];'
        )
    lines.append("}")
    return "\n".join(lines)


def _render_construct_view(design: DesignIR) -> None:
    part_map = {part.id: part for part in design.parts}
    part_colors = {
        "promoter": ("#fef3c7", "#92400e"),
        "RBS": ("#ede9fe", "#5b21b6"),
        "CDS": ("#dcfce7", "#166534"),
        "terminator": ("#fee2e2", "#991b1b"),
        "sensor": ("#dbeafe", "#1e40af"),
    }
    if not design.constructs:
        st.info("No transcriptional units could be inferred.")
        return

    for construct in design.constructs:
        blocks = []
        for part_id in construct.parts:
            part = part_map.get(part_id)
            if part is None:
                continue
            background, foreground = part_colors.get(part.part_type, ("#f1f5f9", "#334155"))
            blocks.append(
                f'<div title="{_escape_html(part.role)}" style="min-width:105px;padding:0.65rem 0.75rem;'
                f'border-radius:0.5rem;background:{background};color:{foreground};'
                f'border:1px solid {foreground}33;text-align:center;">'
                f'<div style="font-size:0.68rem;text-transform:uppercase;opacity:0.75;">'
                f'{_escape_html(part.part_type)}</div>'
                f'<div style="font-weight:700;">{_escape_html(part.name)}</div></div>'
            )
        joined = '<div style="font-size:1.2rem;color:#64748b;">&#8594;</div>'.join(blocks)
        st.markdown(
            f'<div style="margin:0.6rem 0 1rem;padding:0.85rem;border:1px solid #e2e8f0;'
            f'border-radius:0.75rem;background:#ffffff;">'
            f'<div style="font-weight:700;margin-bottom:0.6rem;">5&#8242; '
            f'{_escape_html(construct.name)} 3&#8242;</div>'
            f'<div style="display:flex;align-items:center;gap:0.35rem;overflow-x:auto;">{joined}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


def _render_part_library_view(design: DesignIR) -> None:
    if not design.parts:
        st.info("No parts are available.")
        return
    labels = [f"{part.name} ({part.part_type})" for part in design.parts]
    selected_label = st.selectbox(
        "Inspect component",
        labels,
        key=f"part_inspector_{design.design_id}",
    )
    part = design.parts[labels.index(selected_label)]
    cols = st.columns(3)
    cols[0].metric("Type", part.part_type)
    cols[1].metric("Evidence", part.confidence)
    cols[2].metric("Sequence", "available" if part.sequence else "missing")
    st.markdown(f"**Role:** {_escape_html(part.role)}")
    st.markdown(f"**Why selected:** {_escape_html(part.rationale)}")
    st.markdown(f"**Host context:** {_escape_html(', '.join(part.host_compatibility) or 'not specified')}")
    st.markdown(f"**Upstream:** {_escape_html(', '.join(dict.fromkeys(part.upstream)) or 'none')}")
    st.markdown(f"**Downstream:** {_escape_html(', '.join(dict.fromkeys(part.downstream)) or 'none')}")
    if part.sequence:
        st.code(part.sequence, language="text")
    else:
        st.info("No DNA sequence has been assigned. This component is a design placeholder.")

    with st.expander("Replacement validation", expanded=False):
        library = PartLibrary.demo()
        candidates = library.compatible_parts(
            part_type=part.part_type,
            host_organism=part.host_compatibility[0] if part.host_compatibility else None,
            gate_type=(
                str(part.assignment.metadata.get("gate_type") or "")
                if part.assignment
                else None
            ),
        )
        if not candidates:
            st.info("No type- and host-compatible parts are available in the demonstration library.")
            return
        replacement_labels = [f"{item.name} ({item.id})" for item in candidates]
        replacement_label = st.selectbox(
            "Replacement candidate",
            replacement_labels,
            key=f"replacement_{design.design_id}_{part.id}",
        )
        replacement = candidates[replacement_labels.index(replacement_label)]
        validation = validate_replacement(
            design,
            target_part_id=part.id,
            replacement_part_id=replacement.id,
            library=library,
        )
        for error in validation.errors:
            st.error(error)
        for warning in validation.warnings:
            st.warning(warning)
        if validation.valid:
            st.success("Replacement passed structural validation.")
            if st.button(
                "Create immutable revision",
                key=f"apply_replacement_{design.design_id}_{part.id}",
            ):
                result = replace_part_immutable(
                    design,
                    target_part_id=part.id,
                    replacement_part_id=replacement.id,
                    library=library,
                )
                if result.design:
                    st.session_state[f"design_revision_{design.design_id}"] = result.design
        revised = st.session_state.get(f"design_revision_{design.design_id}")
        if isinstance(revised, DesignIR):
            st.caption(
                f"Created {revised.revision.revision_id}: {revised.revision.summary}"
            )
            st.json(revised.revision.changes)


def _render_design_exports(design: DesignIR) -> None:
    st.caption(
        f"Exporting {design.design_id}, revision {design.revision.revision_id}. "
        "BOM and SBOL can describe incomplete designs; GenBank requires complete construct sequences."
    )

    # 選擇骨架選項
    backbone_choice = st.selectbox(
        "Select Plasmid Backbone for assembly / 選擇載體骨架進行質體重組",
        ["None (Linear Constructs)", "pUC19 (High copy, AmpR)", "p15A (Medium copy, KanR)"],
        index=0,
        key=f"backbone_select_{design.design_id}_{design.revision.revision_id}"
    )

    bom = export_bom_csv(design)
    sbol = export_sbol3_turtle(design)

    st.download_button(
        "Download BOM CSV",
        data=bom.content,
        file_name=bom.filename,
        mime=bom.media_type,
        key=f"bom_download_{design.design_id}_{design.revision.revision_id}",
        use_container_width=True,
    )
    for warning in bom.warnings:
        st.warning(warning)

    if backbone_choice == "None (Linear Constructs)":
        genbank = export_genbank(design)
        if genbank.ok:
            st.download_button(
                "Download GenBank",
                data=genbank.content,
                file_name=genbank.filename,
                mime=genbank.media_type,
                key=f"genbank_download_{design.design_id}_{design.revision.revision_id}",
                use_container_width=True,
            )
        else:
            st.error("GenBank export is blocked because construct sequences are incomplete.")
            for error in genbank.errors:
                st.caption(error)
    else:
        # 進行質體拼接導出
        plasmid = export_plasmid_genbank(design, backbone_choice)
        if plasmid.ok:
            st.download_button(
                "Download Plasmid GenBank",
                data=plasmid.content,
                file_name=plasmid.filename,
                mime=plasmid.media_type,
                key=f"plasmid_download_{design.design_id}_{design.revision.revision_id}",
                use_container_width=True,
            )
            for warning in plasmid.warnings:
                st.warning(warning)
        else:
            st.error("Plasmid GenBank export is blocked.")
            for error in plasmid.errors:
                st.caption(error)

    st.download_button(
        "Download SBOL3 Turtle",
        data=sbol.content,
        file_name=sbol.filename,
        mime=sbol.media_type,
        key=f"sbol_download_{design.design_id}_{design.revision.revision_id}",
        use_container_width=True,
    )
    for warning in sbol.warnings[:8]:
        st.warning(warning)


def _render_design_comparison_tab(node: SearchNode, state: DesignState) -> None:
    topologies = node.candidate_topologies or state.candidate_topologies
    if len(topologies) < 2:
        st.info("At least two candidates are required for DesignDiff.")
        return

    labels = [_topology_candidate_label(index, item) for index, item in enumerate(topologies)]
    left_col, right_col = st.columns(2)
    with left_col:
        left_label = st.selectbox(
            "Left candidate",
            labels,
            index=0,
            key=f"compare_left_{node.node_id}",
        )
    with right_col:
        right_label = st.selectbox(
            "Right candidate",
            labels,
            index=1,
            key=f"compare_right_{node.node_id}",
        )
    left_index = labels.index(left_label)
    right_index = labels.index(right_label)
    if left_index == right_index:
        st.warning("Choose two different candidates.")
        return

    left_topology = topologies[left_index]
    right_topology = topologies[right_index]
    left_design = topology_to_design_ir(
        left_topology,
        host_organism=state.host_organism,
        design_id=f"candidate_{left_index + 1}",
    )
    right_design = topology_to_design_ir(
        right_topology,
        host_organism=state.host_organism,
        design_id=f"candidate_{right_index + 1}",
    )
    metric_keys = [
        "score",
        "gate_count",
        "dynamic_margin",
        "metabolic_burden_score",
        "robustness_score",
        "orthogonality_score",
        "cello_assignment_score",
    ]
    left_metrics = {key: left_topology.get(key) for key in metric_keys}
    right_metrics = {key: right_topology.get(key) for key in metric_keys}
    diff = compare_designs(
        left_design,
        right_design,
        left_metrics=left_metrics,
        right_metrics=right_metrics,
    )
    st.markdown(f"**Summary:** {_escape_html(diff.summary)}")
    st.info(diff.recommendation)

    metric_rows = [
        {
            "metric": change.metric,
            "left": change.left,
            "right": change.right,
            "delta": change.delta,
        }
        for change in diff.metric_changes
    ]
    if metric_rows:
        st.caption("Metric differences")
        st.dataframe(metric_rows, use_container_width=True, hide_index=True)

    validation_rows = [
        {
            "validation": change.metric,
            "left": change.left,
            "right": change.right,
        }
        for change in diff.validation_changes
    ]
    if validation_rows:
        st.caption("Design maturity differences")
        st.dataframe(validation_rows, use_container_width=True, hide_index=True)

    if diff.part_changes:
        st.caption("Part differences")
        for change in diff.part_changes:
            with st.expander(f"{change.change_type}: {change.part_id}"):
                left_part, right_part = st.columns(2)
                with left_part:
                    st.markdown("**Left**")
                    st.json(change.before or {})
                with right_part:
                    st.markdown("**Right**")
                    st.json(change.after or {})
    else:
        st.success("No material part differences were detected.")

    if diff.construct_changes:
        st.caption("Construct order differences")
        st.json(diff.construct_changes)


def _render_topology_card(index: int, topology: dict[str, Any], best_topology: dict[str, Any] | None) -> None:
    candidate_label = f"候選 {int(topology.get('verilog_index', index)) + 1}"
    is_best = topology is best_topology or (
        best_topology is not None
        and topology.get("verilog_index") == best_topology.get("verilog_index")
        and topology.get("score") == best_topology.get("score")
    )
    status = str(topology.get("mapping_status", "unknown"))
    best_label = " · 最佳拓樸" if is_best else ""
    with st.container(border=True):
        st.markdown(
            f"""
            <div class="topology-title">{_escape_html(candidate_label)}{best_label}</div>
            <div class="topology-subtitle">Mapping：{_escape_html(status)} · Source：{_escape_html(str(topology.get("source", "unknown")))}</div>
            {_topology_metrics_html(topology)}
            """,
            unsafe_allow_html=True,
        )
        if is_best:
            st.success("目前分數最高或已選定的最佳拓樸。")

        _render_cello_claim_notice(topology)

        _render_design_workspace(index, topology)

        if _is_failed_mapping(status) and topology.get("mapping_error_summary"):
            st.error(str(topology["mapping_error_summary"]))

        with st.expander(f"{candidate_label} 原始資料", expanded=False):
            raw_topology = {key: value for key, value in topology.items() if key != "verilog"}
            st.json(raw_topology)
            if topology.get("verilog"):
                st.markdown(f'<div class="code-panel">{_escape_html(str(topology["verilog"]))}</div>', unsafe_allow_html=True)

def _list_json_repository_records(base_dir: str | Path) -> list[dict[str, Any]]:
    try:
        from repositories.json_repository import JsonRepository

        return JsonRepository(base_dir).list()
    except Exception:
        return []


def _list_host_profiles() -> list[dict[str, Any]]:
    return _list_json_repository_records(
        Path("outputs") / "api_data" / "host_profiles"
    )


def _list_parameter_fit_snapshots() -> list[dict[str, Any]]:
    return _list_json_repository_records(
        Path("outputs") / "api_data" / "parameter_fit_snapshots"
    )


def _render_layout_audit(node: SearchNode, state: DesignState, topology: dict[str, Any]) -> None:
    from schemas.design_ir import topology_to_design_ir
    from schemas.design_migrations import migrate_design_payload_to_v2
    from schemas.design_ir_v2 import PlasmidV2, AttributedValue
    from benchmark_suite.layout_critic import analyze_layout_issues

    try:
        # Convert topology to DesignIR
        design_v1 = topology_to_design_ir(
            topology,
            host_organism=str(topology.get("host_organism", "Escherichia coli")),
            design_id="candidate_layout_audit"
        )
        # Migrate to V2
        migration_res = migrate_design_payload_to_v2(design_v1.to_dict())
        design_v2 = migration_res.design

        # Ensure we have at least one plasmid to analyze
        if not design_v2.plasmids:
            # Create a mock plasmid that contains all constructs in the design
            construct_ids = [c.id for c in design_v2.constructs]
            plasmid = PlasmidV2(
                id="mock_plasmid_layout_audit",
                name="Mock Plasmid for Layout Audit",
                construct_ids=construct_ids,
                backbone=AttributedValue(value="None (Linear)", status="defaulted")
            )
            plasmids_to_analyze = [plasmid]
        else:
            plasmids_to_analyze = design_v2.plasmids

        # Run layout critic
        all_issues = []
        for plasmid in plasmids_to_analyze:
            issues = analyze_layout_issues(design_v2, plasmid)
            all_issues.extend(issues)

        # Render layout critic results
        st.markdown("### DNA 配置佈局審查 (DNA Layout Audit)")
        if all_issues:
            st.warning("檢測到以下 DNA 配置佈局與轉錄干擾風險：")
            for issue in all_issues:
                severity_str = "⚠️ [警告]" if issue.severity == "warning" else "❌ [錯誤]"
                st.markdown(f"**{severity_str} {issue.code}** (標的: `{issue.subject_id}`): {issue.message}")
        else:
            st.success("DNA 配置佈局審查通過！無顯著干擾或碰撞風險。")
    except Exception as e:
        st.error(f"DNA 佈局審查失敗：{e}")


def _render_ode_simulation_tab(node: SearchNode, state: DesignState) -> None:
    topologies = node.candidate_topologies or state.candidate_topologies
    if not topologies:
        st.info("目前尚無拓樸候選，因此沒有 ODE 模擬圖表。")
        return

    labels = [_topology_candidate_label(index, topology) for index, topology in enumerate(topologies)]
    selected_label = st.selectbox("檢視 ODE 候選", labels, key=f"ode_candidate_{node.node_id}")
    selected_index = labels.index(selected_label)
    topology = topologies[selected_index]
    _render_ode_explanation(topology)

    # Render Layout Audit panel
    _render_layout_audit(node, state, topology)

    # Host environment selection
    st.markdown("### 模擬宿主環境設定 (Host Organism)")
    host_profiles = _list_host_profiles()
    default_chassis = topology.get("chassis") or state.host_organism or "Escherichia coli"
    default_profile_id = "ecoli_k12_default"
    ch = str(default_chassis).lower().strip()
    if "yeast" in ch or "cerevisiae" in ch:
        default_profile_id = "yeast_sc_default"
    elif "cho" in ch or "mammalian" in ch or "sapiens" in ch:
        default_profile_id = "mammalian_cho_default"

    profile_options = [p["profile_id"] for p in host_profiles]
    for pid in ["ecoli_k12_default", "yeast_sc_default", "mammalian_cho_default"]:
        if pid not in profile_options:
            profile_options.append(pid)

    selected_host_id = st.selectbox(
        "選擇模擬宿主環境 (Host Organism)",
        profile_options,
        index=profile_options.index(default_profile_id) if default_profile_id in profile_options else 0,
        key=f"ode_host_select_{node.node_id}_{selected_index}",
        format_func=lambda pid: f"{pid} (宿主: {next((p['host_organism'] for p in host_profiles if p['profile_id'] == pid), 'unknown')})" if any(p['profile_id'] == pid for p in host_profiles) else pid
    )

    # Load profile object
    selected_profile_data = next((p for p in host_profiles if p["profile_id"] == selected_host_id), None)
    if selected_profile_data is None:
        if selected_host_id == "ecoli_k12_default":
            from schemas.host_profile import default_ecoli_profile
            selected_profile = default_ecoli_profile()
        elif selected_host_id == "yeast_sc_default":
            from schemas.host_profile import default_yeast_profile
            selected_profile = default_yeast_profile()
        elif selected_host_id == "mammalian_cho_default":
            from schemas.host_profile import default_mammalian_profile
            selected_profile = default_mammalian_profile()
        else:
            selected_profile = None
    else:
        from schemas.host_profile import host_profile_from_dict
        selected_profile = host_profile_from_dict(selected_profile_data)

    # Temporal profile setup
    st.markdown("### 時間輸入訊號配置 (Temporal Input Profiles)")
    try:
        from tools.ode_simulator import parse_verilog_netlist
        verilog = str(topology.get("verilog") or "")
        signals, _ = parse_verilog_netlist(verilog)
        input_signals = [name for name in sorted(signals.keys()) if signals[name] == "input"]
    except Exception:
        input_signals = []

    temporal_inputs = {}
    if input_signals:
        st.caption("針對個別輸入化學訊號 (Inducer) 配置其隨時間變化的濃度 profile")
        for inp in input_signals:
            st.markdown(f"**輸入訊號 `{inp}`**")
            profile_type = st.selectbox(
                f"訊號 `{inp}` 隨時間變化模式",
                ["Constant (恆定)", "Step (階梯)", "Pulse (脈衝)", "Sine (弦波)"],
                key=f"temp_profile_type_{node.node_id}_{selected_index}_{inp}"
            )

            if profile_type.startswith("Constant"):
                temporal_inputs[inp] = {"type": "step", "time": 0.0, "start_value": 200.0, "end_value": 200.0}
            elif profile_type.startswith("Step"):
                step_time = st.number_input(f"`{inp}` 階梯跳變時間 (秒)", min_value=0.0, value=100.0, key=f"step_time_{node.node_id}_{selected_index}_{inp}")
                val_start = st.number_input(f"`{inp}` 跳變前濃度 (nM)", min_value=0.0, value=0.0, key=f"step_start_{node.node_id}_{selected_index}_{inp}")
                val_end = st.number_input(f"`{inp}` 跳變後濃度 (nM)", min_value=0.0, value=200.0, key=f"step_end_{node.node_id}_{selected_index}_{inp}")
                temporal_inputs[inp] = {
                    "type": "step",
                    "time": step_time,
                    "start_value": val_start,
                    "end_value": val_end
                }
            elif profile_type.startswith("Pulse"):
                start_time = st.number_input(f"`{inp}` 脈衝開始時間 (秒)", min_value=0.0, value=50.0, key=f"pulse_start_{node.node_id}_{selected_index}_{inp}")
                end_time = st.number_input(f"`{inp}` 脈衝結束時間 (秒)", min_value=0.0, value=150.0, key=f"pulse_end_{node.node_id}_{selected_index}_{inp}")
                val_active = st.number_input(f"`{inp}` 脈衝期間濃度 (nM)", min_value=0.0, value=200.0, key=f"pulse_active_{node.node_id}_{selected_index}_{inp}")
                val_basal = st.number_input(f"`{inp}` 脈衝外基底濃度 (nM)", min_value=0.0, value=0.0, key=f"pulse_basal_{node.node_id}_{selected_index}_{inp}")
                temporal_inputs[inp] = {
                    "type": "pulse",
                    "start_time": start_time,
                    "end_time": end_time,
                    "active_value": val_active,
                    "basal_value": val_basal
                }
            elif profile_type.startswith("Sine"):
                amplitude = st.number_input(f"`{inp}` 震盪振幅 (nM)", min_value=0.0, value=100.0, key=f"sine_amp_{node.node_id}_{selected_index}_{inp}")
                frequency = st.number_input(f"`{inp}` 震盪頻率 (Hz)", min_value=0.0, value=0.005, format="%.5f", key=f"sine_freq_{node.node_id}_{selected_index}_{inp}")
                bias = st.number_input(f"`{inp}` 濃度基準/偏置 (nM)", min_value=0.0, value=100.0, key=f"sine_bias_{node.node_id}_{selected_index}_{inp}")
                temporal_inputs[inp] = {
                    "type": "sine",
                    "amplitude": amplitude,
                    "frequency": frequency,
                    "bias": bias
                }
    else:
        st.caption("無偵測到輸入化學訊號。")

    # Run host-specific simulation on the fly
    with st.spinner("執行宿主特異性模擬中..."):
        try:
            from tools.ode_simulator import BatchODESimulator
            from schemas.host_profile import apply_host_profile_to_topology

            if selected_profile:
                sim_topology = apply_host_profile_to_topology(topology, selected_profile)
            else:
                sim_topology = topology

            spec_data = topology.get("simulation_spec", {})
            sim_time = spec_data.get("simulation_time", 600.0)
            samp_count = spec_data.get("sample_count", 80)
            mc_samples = spec_data.get("monte_carlo_samples", 1)
            noise_frac = spec_data.get("noise_level", 0.15)
            seed = spec_data.get("random_seed")

            simulator = BatchODESimulator(
                simulation_time=sim_time,
                sample_count=samp_count,
                monte_carlo_samples=mc_samples,
                noise_fraction=noise_frac,
                random_seed=seed,
                temporal_inputs=temporal_inputs,
            )
            simured_topology = simulator.simulate_topology(sim_topology)
        except Exception as exc:
            st.error(f"模擬失敗：{exc}")
            simured_topology = {"ode_status": "failed", "error": str(exc)}

    status = str(simured_topology.get("ode_status", "unknown"))
    if status == "disabled":
        st.info("此候選的 ODE 模擬已停用。請在左側開啟 ODE 模擬後重新執行。")
        return
    if status == "failed":
        st.error(f"此候選的 ODE 模擬失敗，無法顯示時間序列圖。原因: {simured_topology.get('error')}")
        return

    trace = simured_topology.get("ode_trace")
    if not _valid_ode_trace(trace):
        st.warning("此候選尚未保存 ODE 時間序列。請重新執行 ODE 模擬以產生圖表資料。")
        _render_ode_metric_summary(simured_topology)
        return

    _render_ode_metric_summary(simured_topology)
    trace_rows = _ode_trace_rows(trace)
    if pd is not None:
        trace_df = pd.DataFrame(trace_rows).set_index("time")
        left, right = st.columns(2, gap="medium")
        with left:
            st.caption("輸出蛋白濃度")
            st.line_chart(trace_df[["output_protein"]], use_container_width=True)
        with right:
            st.caption("mRNA / protein 總負擔")
            burden_cols = [column for column in ["total_mrna", "total_protein"] if column in trace_df.columns]
            st.line_chart(trace_df[burden_cols], use_container_width=True)
        st.caption("資源佔用率")
        occupancy_cols = [column for column in ["rnap_occupancy", "ribosome_occupancy"] if column in trace_df.columns]
        st.line_chart(trace_df[occupancy_cols], use_container_width=True)
    else:
        st.table(trace_rows)

    with st.expander("ODE trace 原始資料", expanded=False):
        st.json(trace)

    st.markdown("---")
    st.subheader("參數配適快照對比 (Parameter Fit Snapshot Comparison)")

    snapshots = _list_parameter_fit_snapshots()
    if not snapshots:
        st.info("目前無可用的參數配適快照。請先使用 API 建立配適快照。")
    else:
        compare_enabled = st.checkbox(
            "啟用快照對比 (Compare with Parameter Fit Snapshot)",
            value=False,
            key=f"compare_snapshot_enable_{node.node_id}_{selected_index}"
        )
        if compare_enabled:
            snapshot_options = [s["snapshot_id"] for s in snapshots]
            selected_snapshot_id = st.selectbox(
                "選擇配適快照 ID",
                snapshot_options,
                key=f"compare_snapshot_id_{node.node_id}_{selected_index}",
                format_func=lambda sid: f"{sid} (元件: {next((s['part_id'] for s in snapshots if s['snapshot_id'] == sid), 'unknown')})"
            )

            snapshot = next(s for s in snapshots if s["snapshot_id"] == selected_snapshot_id)

            with st.spinner("執行快照對比模擬中..."):
                try:
                    from tools.ode_simulator import BatchODESimulator
                    from benchmark_suite.parameter_fitting import apply_parameter_fit_snapshot
                    from schemas.host_profile import apply_host_profile_to_topology

                    # Extract simulation params
                    spec_data = topology.get("simulation_spec", {})
                    sim_time = spec_data.get("simulation_time", 600.0)
                    samp_count = spec_data.get("sample_count", 80)
                    mc_samples = spec_data.get("monte_carlo_samples", 1)
                    noise_frac = spec_data.get("noise_level", 0.15)
                    seed = spec_data.get("random_seed")

                    if selected_profile:
                        default_topology_input = apply_host_profile_to_topology(topology, selected_profile)
                    else:
                        default_topology_input = topology

                    simulator = BatchODESimulator(
                        simulation_time=sim_time,
                        sample_count=samp_count,
                        monte_carlo_samples=mc_samples,
                        noise_fraction=noise_frac,
                        random_seed=seed,
                        temporal_inputs=temporal_inputs,
                    )
                    default_topology = simulator.simulate_topology(default_topology_input)

                    fitted_topology_input = apply_parameter_fit_snapshot(topology, snapshot)
                    if selected_profile:
                        fitted_topology_input = apply_host_profile_to_topology(fitted_topology_input, selected_profile)

                    fitted_simulator = BatchODESimulator(
                        simulation_time=sim_time,
                        sample_count=samp_count,
                        monte_carlo_samples=mc_samples,
                        noise_fraction=noise_frac,
                        random_seed=seed,
                        temporal_inputs=temporal_inputs,
                    )
                    fitted_topology = fitted_simulator.simulate_topology(fitted_topology_input)
                except Exception as exc:
                    fitted_topology = {"ode_status": "failed", "error": str(exc)}
                    default_topology = topology

            if fitted_topology.get("ode_status") == "failed":
                st.error(f"套用快照後的 ODE 模擬失敗：{fitted_topology.get('error')}")
            else:
                st.markdown("#### 模擬指標對比 (Metrics Comparison)")

                def_margin = default_topology.get("dynamic_margin")
                fit_margin = fitted_topology.get("dynamic_margin")
                def_snr = default_topology.get("signal_to_noise_ratio")
                fit_snr = fitted_topology.get("signal_to_noise_ratio")
                def_kinetic = default_topology.get("kinetic_score")
                fit_kinetic = fitted_topology.get("kinetic_score")

                def_prov = default_topology.get("parameter_provenance", {})
                fit_prov = fitted_topology.get("parameter_provenance", {})

                def _format_diff(val):
                    if val is None:
                        return "-"
                    if val > 0:
                        return f"+{val:.4f}"
                    return f"{val:.4f}"

                metrics_comparison_data = [
                    {
                        "指標 (Metric)": "Kinetic Score (健壯性得分)",
                        "預設值 (Default)": _format_metric(def_kinetic),
                        "快照配適值 (Fitted)": _format_metric(fit_kinetic),
                        "差異 (Delta)": _format_diff(fit_kinetic - def_kinetic) if def_kinetic is not None and fit_kinetic is not None else "-"
                    },
                    {
                        "指標 (Metric)": "Dynamic Margin (動態範圍邊際)",
                        "預設值 (Default)": _format_metric(def_margin),
                        "快照配適值 (Fitted)": _format_metric(fit_margin),
                        "差異 (Delta)": _format_diff(fit_margin - def_margin) if def_margin is not None and fit_margin is not None else "-"
                    },
                    {
                        "指標 (Metric)": "Signal-to-Noise Ratio (訊噪比 SNR)",
                        "預設值 (Default)": _format_metric(def_snr),
                        "快照配適值 (Fitted)": _format_metric(fit_snr),
                        "差異 (Delta)": _format_diff(fit_snr - def_snr) if def_snr is not None and fit_snr is not None else "-"
                    },
                    {
                        "指標 (Metric)": "私有參數數量 (Local Private Parameters)",
                        "預設值 (Default)": str(def_prov.get("local_private_parameter_count", 0)),
                        "快照配適值 (Fitted)": str(fit_prov.get("local_private_parameter_count", 0)),
                        "差異 (Delta)": f"+{fit_prov.get('local_private_parameter_count', 0) - def_prov.get('local_private_parameter_count', 0)}"
                    },
                    {
                        "指標 (Metric)": "覆蓋/覆寫參數數量 (Overridden Parameters)",
                        "預設值 (Default)": str(def_prov.get("override_count", 0)),
                        "快照配適值 (Fitted)": str(fit_prov.get("override_count", 0)),
                        "差異 (Delta)": f"+{fit_prov.get('override_count', 0) - def_prov.get('override_count', 0)}"
                    }
                ]
                st.table(metrics_comparison_data)

                st.markdown("#### 時間序列重疊對比 (Overlaid Output Protein)")
                def_trace = default_topology.get("ode_trace")
                fit_trace = fitted_topology.get("ode_trace")

                if _valid_ode_trace(def_trace) and _valid_ode_trace(fit_trace):
                    def_rows = _ode_trace_rows(def_trace)
                    fit_rows = _ode_trace_rows(fit_trace)

                    if pd is not None:
                        df_def = pd.DataFrame(def_rows).set_index("time")[["output_protein"]].rename(columns={"output_protein": "Default (預設)"})
                        df_fit = pd.DataFrame(fit_rows).set_index("time")[["output_protein"]].rename(columns={"output_protein": "Fitted (快照配適)"})
                        combined_df = df_def.join(df_fit)
                        st.line_chart(combined_df, use_container_width=True)
                    else:
                        st.warning("Pandas 未載入，無法顯示重疊折線圖。")
                else:
                    st.warning("模擬追蹤資料無效，無法顯示圖表。")

    # Render Parameter Sensitivity Sweep
    st.markdown("---")
    st.subheader("參數敏感度分析 (Parameter Sensitivity Sweep)")
    st.caption("透過對特定生物物理參數進行範圍掃描，評估其對電路效能指標 (訊噪比 SNR、動態範圍等) 的影響。")

    sweep_param = st.selectbox(
        "選擇要掃描的參數",
        [
            "copy_number (質體拷貝數)",
            "growth_dilution (生長稀釋速率, 1/s)",
            "translation_rate (蛋白質翻譯速率)",
            "km_rnap (RNAP 飽和常數)",
            "km_ribo (核糖體飽和常數)",
            "rnap_total (總 RNAP 濃度)",
            "ribo_total (總核糖體濃度)",
        ],
        key=f"sweep_param_select_{node.node_id}_{selected_index}"
    )

    param_name = sweep_param.split()[0]
    left_c, mid_c, right_c = st.columns(3)
    with left_c:
        min_val = st.number_input("掃描下限", min_value=0.001, value=1.0, format="%.3f", key=f"sweep_min_{node.node_id}_{selected_index}")
    with mid_c:
        max_val = st.number_input("掃描上限", min_value=0.001, value=15.0, format="%.3f", key=f"sweep_max_{node.node_id}_{selected_index}")
    with right_c:
        steps = st.slider("掃描點數", min_value=5, max_value=50, value=15, key=f"sweep_steps_{node.node_id}_{selected_index}")

    if min_val >= max_val:
        st.error("下限必須小於上限！")
    else:
        import numpy as np
        sweep_values = list(np.linspace(min_val, max_val, steps))

        if st.button("執行敏感度掃描", key=f"run_sweep_btn_{node.node_id}_{selected_index}"):
            with st.spinner("掃描模擬中..."):
                try:
                    from tools.sensitivity_analysis import run_parameter_sweep
                    sweep_res = run_parameter_sweep(
                        topology=topology,
                        parameter_name=param_name,
                        sweep_values=sweep_values,
                        host_profile_id=selected_host_id,
                        host_profiles={p["profile_id"]: p for p in host_profiles} if host_profiles else None
                    )

                    if pd is not None:
                        sweep_df = pd.DataFrame([
                            {
                                param_name: r["value"],
                                "Dynamic Margin (動態範圍邊際)": r["dynamic_margin"],
                                "SNR (訊噪比)": r["signal_to_noise_ratio"],
                                "Kinetic Score (健壯性得分)": r["kinetic_score"],
                                "Max Burden (最大細胞負擔 nM)": r["max_burden_nM"]
                            }
                            for r in sweep_res["results"]
                        ]).set_index(param_name)

                        st.success("敏感度分析完成！")

                        col1, col2 = st.columns(2)
                        with col1:
                            st.caption("電路效能指標 (Dynamic Margin / SNR / Kinetic Score)")
                            st.line_chart(sweep_df[["Dynamic Margin (動態範圍邊際)", "SNR (訊噪比)", "Kinetic Score (健壯性得分)"]], use_container_width=True)
                        with col2:
                            st.caption("細胞代謝負擔 (Max Burden nM)")
                            st.line_chart(sweep_df[["Max Burden (最大細胞負擔 nM)"]], use_container_width=True)
                    else:
                        st.table(sweep_res["results"])

                    with st.expander("敏感度掃描原始數據", expanded=False):
                        st.write(sweep_res)
                except Exception as e:
                    st.error(f"敏感度掃描失敗：{e}")

    # Render Bifurcation Sweep
    st.markdown("---")
    st.subheader("分岔與轉移函數分析 (Bifurcation & Transfer Function)")
    st.caption("掃描輸入化學訊號 (Inducer) 的濃度範圍，觀察穩態輸出濃度的轉移函數曲線 (Hill curve)。")

    if input_signals:
        selected_bif_input = st.selectbox(
            "選擇要掃描的輸入訊號",
            input_signals,
            key=f"bif_input_select_{node.node_id}_{selected_index}"
        )

        col_b1, col_b2, col_b3 = st.columns(3)
        with col_b1:
            bif_min = st.number_input("輸入濃度下限 (nM)", min_value=0.0, value=0.1, key=f"bif_min_{node.node_id}_{selected_index}")
        with col_b2:
            bif_max = st.number_input("輸入濃度上限 (nM)", min_value=0.1, value=500.0, key=f"bif_max_{node.node_id}_{selected_index}")
        with col_b3:
            bif_steps = st.slider("濃度掃描點數", min_value=5, max_value=50, value=20, key=f"bif_steps_{node.node_id}_{selected_index}")

        if bif_min >= bif_max:
            st.error("下限必須小於上限！")
        else:
            import numpy as np
            log_scale = st.checkbox("採用對數尺度 (Log Scale) 進行掃描", value=True, key=f"bif_log_scale_{node.node_id}_{selected_index}")
            if log_scale and bif_min > 0:
                bif_values = list(np.logspace(np.log10(bif_min), np.log10(bif_max), bif_steps))
            else:
                bif_values = list(np.linspace(bif_min, bif_max, bif_steps))

            if st.button("執行轉移函數掃描", key=f"run_bif_btn_{node.node_id}_{selected_index}"):
                with st.spinner("穩態轉移模擬中..."):
                    try:
                        from tools.sensitivity_analysis import run_bifurcation_sweep
                        bif_res = run_bifurcation_sweep(
                            topology=topology,
                            input_name=selected_bif_input,
                            input_values=bif_values,
                            host_profile_id=selected_host_id,
                            host_profiles={p["profile_id"]: p for p in host_profiles} if host_profiles else None
                        )

                        if pd is not None:
                            bif_df = pd.DataFrame([
                                {
                                    "Input (nM)": r["input_value"],
                                    "Output (nM)": r["output_value"],
                                    "Cell Burden (nM)": r["burden_nM"]
                                    if "burden_nM" in r
                                    else r.get("burden")
                                    if "burden" in r
                                    else 0.0
                                }
                                for r in bif_res["results"]
                            ]).set_index("Input (nM)")

                            st.success("轉移函數掃描完成！")

                            col_c1, col_c2 = st.columns(2)
                            with col_c1:
                                st.caption(f"穩態轉移曲線 (Transfer Function: {selected_bif_input} -> {bif_res.get('target_output', 'Output')})")
                                st.line_chart(bif_df[["Output (nM)"]], use_container_width=True)
                            with col_c2:
                                st.caption("穩態代謝負擔 (Steady-state Burden nM)")
                                st.line_chart(bif_df[["Cell Burden (nM)"]], use_container_width=True)
                        else:
                            st.table(bif_res["results"])

                        with st.expander("轉移掃描原始數據", expanded=False):
                            st.write(bif_res)
                    except Exception as e:
                        st.error(f"轉移函數掃描失敗：{e}")
    else:
        st.info("無可用輸入訊號，無法進行轉移函數掃描。")



def _render_ode_explanation(topology: dict[str, Any]) -> None:
    explanation = explain_ode_topology(topology)
    st.caption("ODE 解釋摘要")
    st.info(str(explanation.get("summary", "目前沒有 ODE 解釋摘要。")))

    readouts = explanation.get("key_readouts", {})
    burden = explanation.get("burden_readouts", {})
    stability = explanation.get("stability_readouts", {})
    if isinstance(readouts, dict) and readouts:
        cols = st.columns(4)
        cols[0].metric("Peak output", _format_metric(readouts.get("peak_output_protein")))
        cols[1].metric("Time to peak", _format_metric(readouts.get("time_to_peak")))
        cols[2].metric("Final output", _format_metric(readouts.get("final_output_protein")))
        cols[3].metric("Steady state", str(readouts.get("steady_state_reached", "unknown")))
    if isinstance(burden, dict) and burden:
        cols = st.columns(4)
        cols[0].metric("Max mRNA", _format_metric(burden.get("max_total_mrna")))
        cols[1].metric("Max protein", _format_metric(burden.get("max_total_protein")))
        cols[2].metric("Max RNAP", _format_metric(burden.get("max_rnap_occupancy")))
        cols[3].metric("Burden risk", str(burden.get("burden_risk_level", "unknown")))
    if isinstance(stability, dict) and stability:
        cols = st.columns(4)
        cols[0].metric("Uncertainty", "是" if stability.get("uncertainty_evaluated") else "否")
        cols[1].metric("MC runs", _format_metric(stability.get("monte_carlo_runs")))
        cols[2].metric("MC fail rate", _format_metric(stability.get("monte_carlo_failure_rate")))
        cols[3].metric("Output CV", _format_metric(stability.get("output_cv")))

    interpretation = explanation.get("interpretation", [])
    warnings = explanation.get("coverage_warnings", [])
    next_checks = explanation.get("next_checks", [])
    interpretation_items = interpretation[:4] if isinstance(interpretation, list) else []
    warning_items = warnings[:3] if isinstance(warnings, list) else []
    next_check_items = next_checks[:3] if isinstance(next_checks, list) else []
    left, right = st.columns(2, gap="medium")
    with left:
        st.caption("生物學判讀")
        for item in interpretation_items:
            st.write(f"- {item}")
    with right:
        st.caption("覆蓋缺口與下一步")
        for item in warning_items:
            st.warning(str(item))
        for item in next_check_items:
            st.write(f"- {item}")

    with st.expander("ODE 解釋完整資料", expanded=False):
        st.json(explanation)


def _render_ode_metric_summary(topology: dict[str, Any]) -> None:
    cols = st.columns(4)
    cols[0].metric("ODE 狀態", str(topology.get("ode_status", "unknown")))
    cols[1].metric("Dynamic margin", _format_metric(topology.get("dynamic_margin")))
    cols[2].metric("SNR", _format_metric(topology.get("signal_to_noise_ratio")))
    cols[3].metric("Output CV", _format_metric(topology.get("metrics_cv")))

    sim_res = topology.get("simulation_result") or {}
    sim_warnings = sim_res.get("warnings") or topology.get("warnings") or []
    if sim_warnings:
        for w in sim_warnings:
            st.warning(w)


def _topology_candidate_label(index: int, topology: dict[str, Any]) -> str:
    candidate_number = int(topology.get("verilog_index", index)) + 1
    score = topology.get("score")
    status = topology.get("ode_status", topology.get("mapping_status", "unknown"))
    score_text = f" · score {float(score):.2f}" if isinstance(score, int | float) else ""
    return f"候選 {candidate_number}{score_text} · {status}"


def _best_topology_from_list(topologies: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not topologies:
        return None
    return max(topologies, key=lambda item: float(item.get("score", -9999)))


def _overall_interpretation(topology: dict[str, Any], node: SearchNode) -> dict[str, str]:
    score = _metric_value(topology, "score")
    grade = _score_grade(score)
    if score is None:
        return {
            "title": "尚無足夠分數資料",
            "body": "目前只能檢視候選拓樸與評審回饋，尚不能穩定比較候選品質。",
        }
    if node.is_approved or score >= 0.80:
        return {
            "title": f"{grade}：可作為優先審查候選",
            "body": "此候選在目前計算檢查下表現較好，適合作為後續人工審查或真實 Cello/UCF 驗證的起點。",
        }
    if score >= 0.60:
        return {
            "title": f"{grade}：可保留但仍需修正",
            "body": "此候選已有部分設計證據支持，但仍存在明顯拖累項目，建議依限制項目建立修正分支。",
        }
    return {
        "title": f"{grade}：目前不宜直接採用",
        "body": "此候選在目前評估中風險偏高，較適合用來診斷失敗原因，而不是作為最終設計。",
    }


def _score_grade(score: float | None) -> str:
    if score is None:
        return "無資料"
    if score >= 0.80:
        return "Excellent"
    if score >= 0.60:
        return "Pass"
    return "Fail"


def _rank_score_components(topology: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items = _score_component_items(topology)
    strengths = sorted([item for item in items if item["score"] >= 0.70], key=lambda item: item["score"], reverse=True)
    limiting = sorted([item for item in items if item["score"] < 0.70], key=lambda item: item["score"])
    if not limiting and items:
        limiting = sorted(items, key=lambda item: item["score"])[:2]
    return strengths, limiting


def _score_component_items(topology: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for component in SCORE_COMPONENTS:
        score = _component_score(topology, component)
        if score is None:
            continue
        item = dict(component)
        item["score"] = max(0.0, min(1.0, float(score)))
        items.append(item)
    return items


def _component_score(topology: dict[str, Any], component: dict[str, Any]) -> float | None:
    benchmark_report = topology.get("benchmark_report")
    if not isinstance(benchmark_report, dict):
        benchmark_report = {}
    for key in [component["key"], *component.get("aliases", [])]:
        value = topology.get(key, benchmark_report.get(key))
        normalized = _score_like_value(value)
        if normalized is not None:
            return normalized
    return None


def _score_like_value(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, int | float):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if number > 1.0:
        if number <= 100.0:
            return number / 100.0
        return None
    return number


def _metric_value(topology: dict[str, Any], key: str) -> float | None:
    value = topology.get(key)
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(float(value)):
        return float(value)
    benchmark_report = topology.get("benchmark_report")
    if isinstance(benchmark_report, dict):
        report_value = benchmark_report.get(key)
        if isinstance(report_value, int | float) and not isinstance(report_value, bool) and math.isfinite(float(report_value)):
            return float(report_value)
    return None


def _component_limiting_reason(component: dict[str, Any], topology: dict[str, Any]) -> str:
    key = component["key"]
    gate_count = topology.get("gate_count")
    mapping_status = str(topology.get("mapping_status", "unknown"))
    ode_status = str(topology.get("ode_status", "unknown"))
    if key == "metabolic_burden":
        if isinstance(gate_count, int | float):
            return f"可能受到 gate count = {gate_count} 或調控層級較多影響；可嘗試降低 gate depth 或移除冗餘 gate。"
        return "可能受到 gate count 或資源佔用影響；可嘗試簡化拓樸。"
    if key == "cello_assignment":
        return f"目前 mapping 狀態為 {mapping_status}；若是 mock 或 unmapped，建議使用真實 UCF/Cello 重新檢查。"
    if key == "kinetic":
        margin = topology.get("dynamic_margin")
        margin_text = f"dynamic margin = {margin}" if margin is not None else f"ODE 狀態為 {ode_status}"
        return f"{margin_text}；可調整元件參數、降低負擔或重新選擇拓樸。"
    if key == "robustness":
        return "穩健性不足時，建議提高 ON/OFF separation，並用 Monte Carlo 擾動檢查參數敏感度。"
    if key == "orthogonality":
        return "正交性不足時，建議替換可能交互干擾的 promoter/repressor pair，或使用更完整的 part library。"
    if key == "functional":
        return "功能分數偏低時，應先回到需求解析、truth table 與 Verilog 是否一致。"
    if key == "static_plausibility":
        return "結構合理性偏低時，建議檢查重複元件、過深邏輯與不必要的中間訊號。"
    if key == "temporal":
        return "時間表現不足時，建議檢查 rise time、response delay 與輸出是否達穩態。"
    return "建議依此 component 的低分原因建立修正分支。"


def _topology_caveats(topology: dict[str, Any], node: SearchNode) -> list[str]:
    caveats = [
        "總分代表目前計算檢查下的相對可信度，不代表已完成濕實驗驗證。",
    ]
    caveats.append(_cello_claim_notice(topology)["message"])
    source = str(topology.get("source", "")).lower()
    mapping_status = str(topology.get("mapping_status", "")).lower()
    if "mock" in source or "mock" in mapping_status or source == "demo_cello_wrapper":
        caveats.append("目前包含 mock/demo mapping；不能把它解讀為真實 Cello part assignment 成功。")
    if topology.get("ode_status") in {None, "disabled", "unknown"}:
        caveats.append("ODE 模擬尚未提供完整動態證據；kinetic 與 robustness 判讀需要保守。")
    if node.error_type != "NONE":
        caveats.append(f"評審仍標記 {ERROR_LABELS.get(node.error_type, node.error_type)}，表示設計仍有待修正的風險。")
    if not topology.get("cello_buildable", False) and _metric_value(topology, "cello_assignment_score") is not None:
        caveats.append("尚未標記為 Cello buildable；若要宣稱可建構，需要真實 UCF 與外部 Cello 檢查。")
    return caveats


def _cello_claim_notice(topology: dict[str, Any]) -> dict[str, str]:
    source = str(topology.get("source", "")).lower()
    mode = str(topology.get("cello_mode", "")).lower()
    claim_level = str(topology.get("cello_claim_level", "")).lower()
    status = str(topology.get("mapping_status", "unknown")).lower()
    warning = str(topology.get("cello_warning", "") or "").strip()
    if mode == "mock" or "mock" in source or source == "demo_cello_wrapper" or claim_level == "mock_only":
        return {
            "level": "mock",
            "title": "Cello 警示：目前是 mock/demo mapping",
            "message": warning
            or "此結果只代表流程可執行，不能解讀為真實 Cello part assignment，也不能宣稱 biological buildability。",
        }
    if status in {"mapping_failed", "failed", "unmapped", "error", "unknown", ""} or claim_level == "external_mapping_failed":
        return {
            "level": "failed",
            "title": "Cello 警示：尚未取得可用 mapping",
            "message": warning
            or "目前沒有可用的 Cello mapping；請避免宣稱此候選已完成元件指派或可建構。",
        }
    if mode == "external" or source == "external_cello_wrapper" or claim_level == "externally_mapped":
        return {
            "level": "real",
            "title": "Cello 狀態：已執行外部 Cello",
            "message": warning
            or "外部 Cello 已完成 mapping；仍需確認 UCF/library、序列層級限制與專家審查後，才能提出更強的生物實作宣稱。",
        }
    return {
        "level": "unknown",
        "title": "Cello 狀態：來源不明",
        "message": "此候選缺少 Cello mode/claim metadata；建議檢查 source、mapping_status 與 artifacts 後再解讀。",
    }


def _recommended_next_action(
    topology: dict[str, Any],
    node: SearchNode,
    caveats: list[str],
    limiting: list[dict[str, Any]],
) -> dict[str, str]:
    source = str(topology.get("source", "")).lower()
    mapping_status = str(topology.get("mapping_status", "unknown")).lower()
    if "mock" in source or "mock" in mapping_status or source == "demo_cello_wrapper":
        return {
            "title": "用真實 Cello/UCF 重新驗證",
            "body": "目前結果適合展示 workflow 與設計推理，但若要面向生物實作審查，下一步應接上真實 part library。",
        }
    if limiting:
        first = limiting[0]
        return {
            "title": f"優先修正 {first['label']}",
            "body": _component_limiting_reason(first, topology),
        }
    if node.is_approved:
        return {
            "title": "保留此候選並進行人工審查",
            "body": "此候選已通過目前門檻；下一步可檢查序列層級限制、part availability 與實驗設計。",
        }
    if caveats:
        return {
            "title": "先釐清主要 caveat",
            "body": caveats[0],
        }
    return {
        "title": "繼續搜尋替代拓樸",
        "body": "目前沒有明確單一修正方向，可增加預算或建立探索分支比較更多候選。",
    }


def _topology_metrics_html(topology: dict[str, Any]) -> str:
    metric_keys = [
        ("score", "Score"),
        ("gate_count", "Gates"),
        ("dynamic_margin", "Dynamic"),
        ("robustness_score", "Robustness"),
        ("orthogonality_score", "Orthogonality"),
        ("cello_assignment_score", "Cello"),
    ]
    blocks = []
    for key, label in metric_keys:
        if key not in topology:
            continue
        blocks.append(
            '<div class="topology-metric">'
            f'<div class="topology-metric-label">{label}</div>'
            f'<div class="topology-metric-value">{_escape_html(_format_metric(topology.get(key)))}</div>'
            "</div>"
        )
    return f'<div class="topology-metrics">{"".join(blocks)}</div>' if blocks else ""


def _format_metric(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return "無資料" if value is None else str(value)


def _is_failed_mapping(status: str) -> bool:
    return status.strip().lower() in {"failed", "mapping_failed", "unmapped", "error"}


def _render_topology_charts(node: SearchNode, state: DesignState) -> None:
    if pd is None:
        st.info("請安裝 pandas 以啟用 Streamlit 圖表渲染。")
        return

    topologies = node.candidate_topologies or state.candidate_topologies
    if not topologies:
        st.info("目前沒有可用於圖表的拓樸候選。")
        return

    chart_df = pd.DataFrame(_topology_rows(topologies)).set_index("candidate")
    st.caption("各拓樸候選分數")
    st.bar_chart(chart_df[["score"]], use_container_width=True)

    metric_cols = [column for column in ["gate_count", "dynamic_margin"] if column in chart_df.columns]
    if metric_cols:
        st.caption("實作複雜度與動態裕度")
        st.line_chart(chart_df[metric_cols], use_container_width=True)


def _render_stochastic_tab(node: SearchNode, state: DesignState) -> None:
    if pd is None:
        st.info("請安裝 pandas 以啟用 Streamlit 圖表渲染。")
        return

    topologies = node.candidate_topologies or state.candidate_topologies
    if not topologies:
        st.info("目前沒有可用於隨機驗證的拓樸候選。")
        return

    # Select topology to run stochastic simulation on
    top_options = [f"候選 {i}: {top.get('verilog', '')[:40]}..." for i, top in enumerate(topologies)]
    selected_idx = st.selectbox("選擇拓樸候選", range(len(top_options)), format_func=lambda i: top_options[i])
    topology = topologies[selected_idx]

    col1, col2 = st.columns(2)
    runs = col1.number_input("隨機模擬次數 (runs)", min_value=1, max_value=100, value=20, step=5)
    scale_factor = col2.number_input("分子個數縮放因子 (scale)", min_value=1.0, max_value=100.0, value=10.0, step=1.0)

    if st.button("執行隨機驗證 (Run Stochastic Audit)"):
        with st.spinner("執行 Gillespie 隨機模擬中..."):
            from tools.tool_adapters import StochasticSimulationAdapter
            adapter = StochasticSimulationAdapter()
            res = adapter.run({"topology": topology, "runs": int(runs), "scale_factor": scale_factor})

            if res.status == "ok":
                st.success("隨機模擬完成！")
                stoch_res = res.output["stochastic_result"]

                # Show overall metrics
                m_cols = st.columns(2)
                m_cols[0].metric("記憶體穩定性 (Memory Stability)", f"{stoch_res.get('memory_stability', 1.0) * 100:.1f}%")
                m_cols[1].metric("切換失敗率 (P_fail)", f"{stoch_res.get('switching_failure_probability', 0.0) * 100:.1f}%")

                # Show Fano factors
                st.caption("Fano 因子 (Fano Factors) - 評估分子噪聲雜訊強度")
                fano_df = pd.DataFrame([
                    {"基因": gene, "Fano 因子": fano}
                    for gene, fano in stoch_res.get("fano_factors", {}).items()
                ])
                if not fano_df.empty:
                    st.dataframe(fano_df.set_index("基因"))

                # Plot trajectories
                # We can plot the mean trajectory first
                mean_traj = stoch_res.get("mean_trajectory") or {}
                if mean_traj and "time" in mean_traj:
                    st.caption("平均分子濃度軌跡 (Mean Trajectory)")
                    plot_data = pd.DataFrame(mean_traj).set_index("time")
                    st.line_chart(plot_data, use_container_width=True)

                    # Plot single cell runs
                    st.caption("單細胞隨機軌跡範例 (Sample Single-Cell Traces)")
                    sample_runs = stoch_res.get("runs") or []
                    if sample_runs:
                        for idx_run, run in enumerate(sample_runs[:3]):
                            with st.expander(f"細胞軌跡 {idx_run + 1}"):
                                run_df = pd.DataFrame(run).set_index("time")
                                st.line_chart(run_df, use_container_width=True)
            else:
                st.error("隨機模擬執行失敗！")
                if hasattr(res, "warnings"):
                    for w in res.warnings:
                        st.warning(_tool_warning_message(w))


def _tool_warning_message(warning: Any) -> str:
    if isinstance(warning, dict):
        return str(warning.get("message") or "未知錯誤")
    message = getattr(warning, "message", None)
    return str(message or warning or "未知錯誤")



def _run_demo_iteration(state: DesignState) -> None:
    # Auto-complete PM Stage if running engine directly
    if getattr(state, "pm_stage", "elicitation") == "elicitation":
        fallbacks = {
            "chassis": "Escherichia coli",
            "inputs": [{"name": "IPTG", "sensor_promoter": "pLac", "type": "input_sensor"}],
            "outputs": [{"name": "sfGFP", "type": "reporter_gene"}],
            "logic_relation": "sfGFP = IPTG",
            "copy_number": 15
        }
        for k, v in fallbacks.items():
            if k not in state.structured_spec:
                state.structured_spec[k] = v
        state.pm_stage = "completed"
        state.pending_proposal = {}

    options = st.session_state.ui_options
    if not state.tree_nodes:
        root = SearchNode(node_id="root", search_mode="Exploration")
        state.tree_nodes[root.node_id] = root
        state.active_frontier = [root.node_id]


    if not state.active_frontier or state.used_budget >= state.compute_budget:
        _select_best_fallback(state)
        return

    current_node_id = state.active_frontier.pop(0)
    node = state.tree_nodes[current_node_id]
    state.current_node_id = current_node_id

    mode = node.search_mode
    if options["enable_skill_context"] and mode in {"Exploration", "Repair"}:
        state.skill_context = _demo_skill_context(state.user_intent, mode)
        state.rag_context = state.skill_context

    if mode != "Exploitation":
        node.logic_proposals = _demo_proposals(state, node)
    elif not node.logic_proposals:
        node.logic_proposals = state.logic_proposals[:]

    node.current_topology = node.logic_proposals[0] if node.logic_proposals else ""
    state.logic_proposals = node.logic_proposals[:]
    state.current_topology = node.current_topology

    node.verilog_codes = [_demo_verilog(index, proposal, mode) for index, proposal in enumerate(node.logic_proposals)]
    state.verilog_codes = node.verilog_codes[:]

    node.candidate_topologies = _demo_topologies(node, options["enable_ode"])
    state.candidate_topologies = node.candidate_topologies[:]

    best_topology = max(node.candidate_topologies, key=lambda item: float(item.get("score", -9999)), default=None)
    node.best_topology = best_topology
    node.score = float(best_topology.get("score", -9999)) if best_topology else -float("inf")
    node.sync_evaluation_metrics(best_topology)
    state.best_topology = best_topology

    _demo_critic_and_branch(state, node, options["enable_tree_search"])
    node.status = "Pass" if node.is_approved else "Evaluated"
    state.error_type = node.error_type
    state.is_approved = node.is_approved
    state.critic_feedbacks = node.critic_feedbacks[:]
    state.last_error = None
    state.iteration_count += 1
    st.session_state.selected_node_id = node.node_id

    if not state.is_completed and not state.active_frontier and state.used_budget >= state.compute_budget:
        _select_best_fallback(state)


def _run_byok_workflow(state: DesignState) -> None:
    config = st.session_state.llm_config
    if not state.user_intent.strip():
        st.session_state.run_message = ("warning", "請先輸入設計需求再執行自備金鑰工作流程。")
        return

    # Auto-complete PM Stage if running engine directly
    if getattr(state, "pm_stage", "elicitation") == "elicitation":
        fallbacks = {
            "chassis": "Escherichia coli",
            "inputs": [{"name": "IPTG", "sensor_promoter": "pLac", "type": "input_sensor"}],
            "outputs": [{"name": "sfGFP", "type": "reporter_gene"}],
            "logic_relation": "sfGFP = IPTG",
            "copy_number": 15
        }
        for k, v in fallbacks.items():
            if k not in state.structured_spec:
                state.structured_spec[k] = v
        state.pm_stage = "completed"
        state.pending_proposal = {}

    if not config.get("api_key", "").strip():
        st.session_state.run_message = ("warning", "請在自備金鑰模型設定中輸入 API key。")
        return
    if not config.get("model_name", "").strip():
        st.session_state.run_message = ("warning", "請選擇或輸入 LiteLLM 模型名稱。")
        return

    try:
        from agents.builder_agent import BuilderAgent
        from agents.consolidator_agent import ConsolidatorAgent
        from agents.critic_agent import CriticAgent
        from agents.data_miner_agent import DataMinerAgent
        from agents.skill_extractor_agent import SkillExtractorAgent
        from agents.translator_agent import call_translator
        from tools.cello_wrapper import CelloWrapper
        from tools.ode_simulator import BatchODESimulator
        from tools.skill_retriever import SkillRetriever
        from utils import llm_utils
        from vector_db import InMemoryVectorDB
        from workflows.reflexion_controller import run_reflexion_workflow
    except Exception as exc:
        st.session_state.run_message = ("error", f"無法載入工作流程元件：{exc}")
        return

    class TranslatorRunner:
        def __init__(self, api_key: str, model_name: str, api_base: str | None):
            self.api_key = api_key
            self.model_name = model_name
            self.api_base = api_base
            self.kwargs: dict[str, Any] = {}

        def run(self, workflow_state: DesignState) -> DesignState:
            return call_translator(
                workflow_state,
                api_key=self.api_key,
                model_name=self.model_name,
                api_base=self.api_base,
                **self.kwargs,
            )

    options = st.session_state.ui_options
    api_key = config["api_key"].strip()
    model_name = config["model_name"].strip()
    api_base = config.get("api_base", "").strip() or None

    llm_utils.ENABLE_LLM_CACHE = bool(options["enable_cache"])
    state.last_error = None
    try:
        result_state = run_reflexion_workflow(
            state=state,
            builder=BuilderAgent(api_key=api_key, model_name=llm_utils.resolve_agent_model("builder", model_name), api_base=api_base),
            translator=TranslatorRunner(api_key=api_key, model_name=llm_utils.resolve_agent_model("translator", model_name), api_base=api_base),
            cello_wrapper=CelloWrapper(
                cello_command=options.get("cello_command") or None,
                ucf_path=options.get("ucf_path") or None,
                part_library_path=options.get("part_library_path") or None,
                sensor_path=options.get("sensor_path") or None,
                device_path=options.get("device_path") or None,
            ),
            batch_ode_simulator=BatchODESimulator() if options["enable_ode"] else _NoOpODESimulator(),
            critic=CriticAgent(api_key=api_key, model_name=llm_utils.resolve_agent_model("critic", model_name), api_base=api_base),
            consolidator=ConsolidatorAgent(),
            skill_retriever=SkillRetriever.from_json_file(include_extracted=True) if options["enable_skill_context"] else None,
            data_miner=DataMinerAgent() if options["enable_ode"] else None,
            skill_extractor=SkillExtractorAgent(
                vault_dir="outputs/obsidian_skills",
                vector_db=InMemoryVectorDB(),
                memory_path="outputs/extracted_skills.jsonl",
            ),
        )
    except Exception as exc:
        state.last_error = f"錯誤：自備金鑰工作流程失敗：{exc}"
        st.session_state.run_message = ("error", state.last_error)
        return

    st.session_state.design_state = result_state
    st.session_state.selected_node_id = result_state.current_node_id
    if result_state.last_error:
        st.session_state.run_message = ("error", result_state.last_error)
    else:
        st.session_state.run_message = ("success", "自備金鑰工作流程已完成。請檢視產生的節點、Verilog、拓樸與圖表。")


class _NoOpODESimulator:
    def run(self, state: DesignState) -> DesignState:
        node = state.tree_nodes.get(state.current_node_id) if state.current_node_id else None
        topologies = node.candidate_topologies if node else state.candidate_topologies
        for topology in topologies:
            topology["ode_status"] = "disabled"
        return state


def _demo_proposals(state: DesignState, node: SearchNode) -> list[str]:
    base_intent = state.user_intent.strip() or "設計一個基因邏輯電路"
    repair_hint = "，並納入評審回饋" if node.search_mode == "Repair" else ""
    blueprints = [
        ("proposal_a", "降低生物元件成本", "Y = A AND NOT B", 2, 3, ["PRESERVE_SIMPLE_GATES"]),
        ("proposal_b", "降低邏輯深度與延遲", "Y = A OR B", 1, 4, []),
        ("proposal_c", "優先提高雜訊輸入下的穩健性", "Y = (A AND NOT B) OR (A AND C)", 3, 5, ["USE_STRUCTURAL_INSTANTIATION"]),
    ]
    proposals = []
    for key, strategy, blueprint, depth, cost, directives in blueprints:
        proposals.append(
            json.dumps(
                {
                    "id": key,
                    "strategy_description": f"{strategy}{repair_hint}；需求：{base_intent}",
                    "total_logic_depth": depth,
                    "total_repressor_cost": cost,
                    "logic_blueprint": blueprint,
                    "translator_directives": directives,
                },
                ensure_ascii=False,
            )
        )
    return proposals


def _demo_verilog(index: int, proposal: str, mode: str) -> str:
    module_name = f"genetic_circuit_{mode.lower()}_{index + 1}"
    if index == 1:
        return f"""module {module_name}(input A, input B, output Y);
  assign Y = A | B;
endmodule"""
    if index == 2:
        return f"""module {module_name}(input A, input B, input C, output Y);
  wire not_b;
  wire arm_a;
  wire arm_b;
  not(not_b, B);
  and(arm_a, A, not_b);
  and(arm_b, A, C);
  or(Y, arm_a, arm_b);
endmodule"""
    return f"""module {module_name}(input A, input B, output Y);
  wire not_b;
  not(not_b, B);
  and(Y, A, not_b);
endmodule"""


def _demo_topologies(node: SearchNode, enable_ode: bool) -> list[dict[str, Any]]:
    mode_bonus = {"Exploration": 0.0, "Repair": 0.08, "Exploitation": 0.14}.get(node.search_mode, 0.0)
    topologies = []
    for index, code in enumerate(node.verilog_codes):
        score = min(0.96, 0.58 + mode_bonus + index * 0.08 + len(node.critic_feedbacks) * 0.03)
        topology = {
            "source": "demo_cello_wrapper",
            "cello_mode": "mock",
            "cello_claim_level": "mock_only",
            "cello_warning": "示範模式使用 demo/mock Cello 輸出，只能說明 workflow 與可解釋性，不能宣稱真實元件 mapping 或可建構。",
            "verilog_index": index,
            "mapping_status": "mapped",
            "gate_count": 2 + index,
            "score": round(score, 3),
            "functional_score": round(min(0.98, 0.78 + index * 0.05 + mode_bonus), 3),
            "kinetic_score": round(min(0.95, 0.62 + index * 0.07 + mode_bonus), 3),
            "static_plausibility_score": round(max(0.45, 0.86 - index * 0.08), 3),
            "metabolic_burden_score": round(max(0.35, 0.82 - index * 0.12), 3),
            "robustness_score": round(min(0.94, 0.66 + index * 0.05 + mode_bonus), 3),
            "temporal_score": round(min(0.9, 0.70 + index * 0.03), 3),
            "orthogonality_score": round(max(0.42, 0.76 - index * 0.04 + mode_bonus / 2), 3),
            "cello_assignment_score": round(min(0.88, 0.54 + index * 0.08 + mode_bonus), 3),
            "verilog": code,
        }
        if enable_ode:
            topology["ode_status"] = "simulated"
            topology["dynamic_margin"] = round(0.31 + index * 0.07 + mode_bonus, 3)
            topology["ode_trace"] = _demo_ode_trace(index, mode_bonus)
        else:
            topology["ode_status"] = "disabled"
        topologies.append(topology)
    return topologies


def _demo_ode_trace(index: int, mode_bonus: float) -> dict[str, list[float]]:
    time_points = [0, 60, 120, 180, 240, 300, 360, 420, 480, 540, 600]
    scale = 1.0 + index * 0.28 + mode_bonus
    output = [round(scale * 85.0 * (1.0 - math.exp(-point / 210.0)), 3) for point in time_points]
    total_mrna = [round(scale * 14.0 * (1.0 - math.exp(-point / 130.0)), 3) for point in time_points]
    total_protein = [round(scale * 120.0 * (1.0 - math.exp(-point / 260.0)), 3) for point in time_points]
    rnap_occupancy = [round(min(0.95, 0.12 + scale * 0.22 * (1.0 - math.exp(-point / 180.0))), 4) for point in time_points]
    ribosome_occupancy = [round(min(0.95, 0.16 + scale * 0.26 * (1.0 - math.exp(-point / 220.0))), 4) for point in time_points]
    return {
        "time": time_points,
        "output_protein": output,
        "total_mrna": total_mrna,
        "total_protein": total_protein,
        "rnap_occupancy": rnap_occupancy,
        "ribosome_occupancy": ribosome_occupancy,
    }


def _demo_critic_and_branch(state: DesignState, node: SearchNode, enable_tree_search: bool) -> None:
    approved = node.score >= 0.82 or state.used_budget >= state.compute_budget - 1
    node.is_approved = approved

    if approved:
        node.error_type = "NONE"
        node.critic_feedbacks.append("設計已通過示範門檻。請將此拓樸整合為目前最佳結果。")
        state.is_completed = True
        return

    state.used_budget += 1
    if node.search_mode == "Exploration":
        node.error_type = "LOGIC_ERROR"
        node.critic_feedbacks.append("邏輯方向合理，但規格仍不夠明確。請新增修正分支，在保留需求的前提下收斂布林行為。")
    elif node.search_mode == "Repair":
        node.error_type = "PART_ERROR"
        node.critic_feedbacks.append("邏輯目前可接受，但元件 mapping 仍可改善。請在不改變架構的前提下進行最佳化。")
    else:
        node.error_type = "PART_ERROR"
        node.critic_feedbacks.append("元件指派仍需要微調。請保留最高分拓樸作為備用結果。")

    if not enable_tree_search:
        return

    if node.error_type in {"LOGIC_ERROR", "BOTH"}:
        repair_id = _child_id(node.node_id, "repair")
        repair_node = SearchNode(
            node_id=repair_id,
            parent_id=node.node_id,
            search_mode="Repair",
            critic_feedbacks=node.critic_feedbacks[:],
            error_type=node.error_type,
        )
        node.children_ids.append(repair_id)
        state.tree_nodes[repair_id] = repair_node
        state.active_frontier.append(repair_id)

        if state.used_budget < state.compute_budget - 1:
            explore_id = _child_id(node.node_id, "explore")
            explore_node = SearchNode(
                node_id=explore_id,
                parent_id=node.node_id,
                search_mode="Exploration",
                critic_feedbacks=node.critic_feedbacks[:],
                error_type=node.error_type,
            )
            node.children_ids.append(explore_id)
            state.tree_nodes[explore_id] = explore_node
            state.active_frontier.append(explore_id)
    elif node.error_type == "PART_ERROR":
        exploit_id = _child_id(node.node_id, "exploit")
        exploit_node = SearchNode(
            node_id=exploit_id,
            parent_id=node.node_id,
            search_mode="Exploitation",
            logic_proposals=node.logic_proposals[:],
            critic_feedbacks=node.critic_feedbacks[:],
            error_type=node.error_type,
        )
        node.children_ids.append(exploit_id)
        state.tree_nodes[exploit_id] = exploit_node
        state.active_frontier.append(exploit_id)


def _select_best_fallback(state: DesignState) -> None:
    best_node = None
    for node in state.tree_nodes.values():
        if not node.best_topology:
            continue
        if best_node is None or node.score > best_node.score:
            best_node = node
    if best_node:
        state.current_node_id = best_node.node_id
        state.best_topology = best_node.best_topology
        state.logic_proposals = best_node.logic_proposals[:]
        state.verilog_codes = best_node.verilog_codes[:]
        state.candidate_topologies = best_node.candidate_topologies[:]
        state.error_type = best_node.error_type
        state.critic_feedbacks = best_node.critic_feedbacks[:]
        st.session_state.selected_node_id = best_node.node_id


def _demo_skill_context(intent: str, mode: str) -> str:
    return "\n".join(
        [
            f"模式感知設計知識：{MODE_LABELS.get(mode, mode)}",
            "優先使用 Cello 相容的組合邏輯：primitive gates、wire、assign。",
            "避免 always blocks、registers、clocks、latches、memories 與 delay syntax。",
            f"需求關鍵字：{', '.join(intent.lower().split()[:8]) or '未提供'}",
        ]
    )


def _node_score_rows(state: DesignState) -> list[dict[str, Any]]:
    rows = []
    for index, node in enumerate(state.tree_nodes.values(), start=1):
        if not math.isfinite(node.score):
            continue
        rows.append(
            {
                "node": f"{index}. {node.node_id}",
                "score": round(float(node.score), 3),
            }
        )
    return rows


def _topology_chart_rows(state: DesignState) -> list[dict[str, Any]]:
    node = _selected_node(state)
    topologies = []
    if node is not None:
        topologies = node.candidate_topologies
    if not topologies:
        topologies = state.candidate_topologies
    return _topology_rows(topologies)


def _topology_rows(topologies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for index, topology in enumerate(topologies):
        candidate = f"候選 {int(topology.get('verilog_index', index)) + 1}"
        rows.append(
            {
                "candidate": candidate,
                "score": float(topology.get("score", 0.0)),
                "gate_count": int(topology.get("gate_count", 0)),
                "dynamic_margin": float(topology.get("dynamic_margin", 0.0)),
            }
        )
    return rows


def _verilog_to_gate_graph(verilog: str) -> dict[str, Any]:
    code = _strip_verilog_comments(verilog)
    if not code.strip():
        return {"ok": False, "dot": "", "message": "此拓樸沒有 Verilog，無法產生 gate graph。"}

    inputs, outputs, wires = _extract_verilog_signals(code)
    nodes: dict[str, str] = {}
    edges: list[tuple[str, str]] = []
    gate_index = 0

    for signal in sorted(inputs):
        nodes[signal] = "input"
    for signal in sorted(outputs):
        nodes[signal] = "output"
    for signal in sorted(wires):
        nodes.setdefault(signal, "wire")

    for gate, body in re.findall(r"\b(and|or|not|nand|nor|xor|xnor)\s*\(([^;]+?)\)\s*;", code, flags=re.IGNORECASE | re.DOTALL):
        parts = [_clean_signal_name(part) for part in body.split(",")]
        parts = [part for part in parts if part]
        if len(parts) < 2:
            continue
        output, gate_inputs = parts[0], parts[1:]
        gate_index += 1
        gate_node = f"{gate.upper()}_{gate_index}"
        nodes[gate_node] = "gate"
        nodes.setdefault(output, "wire")
        for signal in gate_inputs:
            nodes.setdefault(signal, "unknown")
            edges.append((signal, gate_node))
        edges.append((gate_node, output))

    for lhs, rhs in re.findall(r"\bassign\s+([^=;]+?)\s*=\s*([^;]+?)\s*;", code, flags=re.IGNORECASE | re.DOTALL):
        output = _clean_signal_name(lhs)
        if not output:
            continue
        nodes.setdefault(output, "output" if output in outputs else "wire")
        gate_index = _add_assign_expression_edges(output, rhs, nodes, edges, gate_index)

    if not edges:
        return {"ok": False, "dot": "", "message": "無法解析 gate graph；請展開原始資料查看 Verilog。"}

    return {"ok": True, "dot": _build_gate_graph_dot(nodes, edges, inputs, outputs), "message": ""}


def _strip_verilog_comments(verilog: str) -> str:
    without_block = re.sub(r"/\*.*?\*/", "", verilog, flags=re.DOTALL)
    return re.sub(r"//.*", "", without_block)


def _extract_verilog_signals(code: str) -> tuple[set[str], set[str], set[str]]:
    signals: dict[str, set[str]] = {"input": set(), "output": set(), "wire": set()}
    for keyword in signals:
        for match in re.finditer(rf"\b{keyword}\b\s*(?:\[[^\]]+\]\s*)?([^;);]+)", code, flags=re.IGNORECASE):
            declaration = re.split(r"\b(?:input|output|wire|module|endmodule)\b", match.group(1), flags=re.IGNORECASE)[0]
            for name in re.split(r",", declaration):
                signal = _clean_signal_name(name)
                if signal:
                    signals[keyword].add(signal)
        for match in re.finditer(rf"\b{keyword}\b\s*(?:\[[^\]]+\]\s*)?([A-Za-z_]\w*)", code, flags=re.IGNORECASE):
            signals[keyword].add(match.group(1))
    return signals["input"], signals["output"], signals["wire"]


def _add_assign_expression_edges(
    output: str,
    expression: str,
    nodes: dict[str, str],
    edges: list[tuple[str, str]],
    gate_index: int,
) -> int:
    expression = _strip_outer_parens(expression.strip())
    direct = _clean_signal_name(expression)
    if direct and direct == expression.strip():
        nodes.setdefault(direct, "unknown")
        edges.append((direct, output))
        return gate_index

    for operator, gate in [("&", "AND"), ("|", "OR"), ("^", "XOR")]:
        parts = _split_expression(expression, operator)
        if len(parts) > 1:
            gate_index += 1
            gate_node = f"{gate}_{gate_index}"
            nodes[gate_node] = "gate"
            for part in parts:
                source, gate_index = _expression_source_node(part, nodes, edges, gate_index)
                if source:
                    edges.append((source, gate_node))
            edges.append((gate_node, output))
            return gate_index

    source, gate_index = _expression_source_node(expression, nodes, edges, gate_index)
    if source:
        edges.append((source, output))
    return gate_index


def _expression_source_node(
    expression: str,
    nodes: dict[str, str],
    edges: list[tuple[str, str]],
    gate_index: int,
) -> tuple[str | None, int]:
    expression = _strip_outer_parens(expression.strip())
    if expression.startswith("~") or expression.startswith("!"):
        source = _clean_signal_name(expression[1:])
        if not source:
            return None, gate_index
        gate_index += 1
        gate_node = f"NOT_{gate_index}"
        nodes.setdefault(source, "unknown")
        nodes[gate_node] = "gate"
        edges.append((source, gate_node))
        return gate_node, gate_index

    signal = _clean_signal_name(expression)
    if signal:
        nodes.setdefault(signal, "unknown")
        return signal, gate_index
    return None, gate_index


def _split_expression(expression: str, operator: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in expression:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == operator and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current).strip())
    return [part for part in parts if part]


def _strip_outer_parens(value: str) -> str:
    value = value.strip()
    while value.startswith("(") and value.endswith(")"):
        inner = value[1:-1].strip()
        if not inner:
            break
        value = inner
    return value


def _clean_signal_name(value: str) -> str:
    value = re.sub(r"\b(?:input|output|wire|reg)\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\[[^\]]+\]", "", value)
    match = re.search(r"[A-Za-z_]\w*", value.strip())
    return match.group(0) if match else ""


def _build_gate_graph_dot(nodes: dict[str, str], edges: list[tuple[str, str]], inputs: set[str], outputs: set[str]) -> str:
    lines = [
        "digraph GateGraph {",
        "  graph [rankdir=LR, bgcolor=\"transparent\", pad=\"0.2\", nodesep=\"0.45\", ranksep=\"0.65\"];",
        "  node [fontname=\"Arial\", fontsize=10, margin=\"0.08,0.05\"];",
        "  edge [color=\"#64748b\", arrowsize=0.7];",
    ]
    for name, kind in sorted(nodes.items()):
        normalized_kind = "input" if name in inputs else "output" if name in outputs else kind
        lines.append(f"  {_dot_id(name)} [{_dot_node_attrs(name, normalized_kind)}];")
    for source, target in edges:
        lines.append(f"  {_dot_id(source)} -> {_dot_id(target)};")
    lines.append("}")
    return "\n".join(lines)


def _dot_node_attrs(name: str, kind: str) -> str:
    label = _dot_escape(_gate_label(name))
    if kind == "input":
        return f'label="{label}", shape=box, style="rounded,filled", fillcolor="#dbeafe", color="#60a5fa"'
    if kind == "output":
        return f'label="{label}", shape=box, style="rounded,filled", fillcolor="#dcfce7", color="#34d399"'
    if kind == "gate":
        return f'label="{label}", shape=box, style="rounded,filled", fillcolor="#ffffff", color="#334155"'
    return f'label="{label}", shape=ellipse, style=filled, fillcolor="#f1f5f9", color="#cbd5e1"'


def _gate_label(name: str) -> str:
    return re.sub(r"_\d+$", "", name) if re.match(r"^(?:AND|OR|NOT|NAND|NOR|XOR|XNOR)_\d+$", name) else name


def _dot_id(name: str) -> str:
    return f'"{_dot_escape(name)}"'


def _dot_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _search_path_to_node(state: DesignState, node_id: str | None) -> list[SearchNode]:
    if not node_id or node_id not in state.tree_nodes:
        return []
    path: list[SearchNode] = []
    seen: set[str] = set()
    current_id: str | None = node_id
    while current_id and current_id in state.tree_nodes and current_id not in seen:
        seen.add(current_id)
        node = state.tree_nodes[current_id]
        path.append(node)
        current_id = node.parent_id
    path.reverse()
    return path


def _branch_reason_for_node(state: DesignState, node_id: str) -> str:
    node = state.tree_nodes.get(node_id)
    if node is None:
        return "找不到節點"
    parent = state.tree_nodes.get(node.parent_id or "")
    if parent is None:
        return "搜尋起點"
    if node.search_mode == "Repair":
        return f"{ERROR_LABELS.get(parent.error_type, parent.error_type)} -> 修正"
    if node.search_mode == "Exploitation":
        return f"{ERROR_LABELS.get(parent.error_type, parent.error_type)} -> 元件最佳化"
    if node.search_mode == "Exploration":
        return f"{ERROR_LABELS.get(parent.error_type, parent.error_type)} -> 重新探索"
    return f"{ERROR_LABELS.get(parent.error_type, parent.error_type)} -> {MODE_LABELS.get(node.search_mode, node.search_mode)}"


def _decision_step_for_node(state: DesignState, node: SearchNode, depth: int) -> dict[str, str]:
    mode = MODE_LABELS.get(node.search_mode, node.search_mode)
    status = STATUS_LABELS.get(node.status, node.status)
    score = "無資料" if not math.isfinite(node.score) else f"{node.score:.2f}"
    title = f"第 {depth + 1} 輪：{_decision_title(node)}"
    meta = f"{node.node_id} · {mode} · {status} · score {score}"
    reason = _branch_reason_for_node(state, node.node_id)
    action = _decision_action(node)
    result = _decision_result(node)
    next_step = _decision_next_step(state, node)
    return {
        "title": title,
        "meta": meta,
        "action": action,
        "reason": reason,
        "result": result,
        "next": next_step,
    }


def _decision_title(node: SearchNode) -> str:
    if node.search_mode == "Repair":
        return "依評審回饋修正設計"
    if node.search_mode == "Exploitation":
        return "保留邏輯並最佳化元件"
    if node.parent_id:
        return "重新探索替代設計"
    return "建立初始設計假設"


def _decision_action(node: SearchNode) -> str:
    proposal_count = len(node.logic_proposals)
    verilog_count = len(node.verilog_codes)
    topology_count = len(node.candidate_topologies)
    if node.search_mode == "Repair":
        return f"建立修正分支，產生 {proposal_count} 個邏輯提案、{verilog_count} 個 Verilog 候選與 {topology_count} 個拓樸候選。"
    if node.search_mode == "Exploitation":
        return f"沿用較可接受的邏輯方向，嘗試改善 mapping、負擔或動態表現，並評估 {topology_count} 個拓樸候選。"
    return f"從自然語言需求建立初始設計假設，產生 {proposal_count} 個邏輯提案與 {topology_count} 個拓樸候選。"


def _decision_result(node: SearchNode) -> str:
    best = node.best_topology
    feedback = _summarize_feedback(node.critic_feedbacks[-1] if node.critic_feedbacks else "", limit=150)
    if best:
        candidate = int(best.get("verilog_index", 0)) + 1
        score = _format_metric(best.get("score", node.score if math.isfinite(node.score) else None))
        mapping = best.get("mapping_status", "unknown")
        base = f"候選 {candidate} 目前最佳，分數 {score}，mapping 狀態為 {mapping}。"
    else:
        base = "目前尚未形成可排名的最佳拓樸。"
    if feedback:
        return f"{base} 評審摘要：{feedback}"
    return base


def _decision_next_step(state: DesignState, node: SearchNode) -> str:
    if node.is_approved:
        return "保留此候選作為目前最佳結果，並進入人工審查或匯出階段。"
    if node.status == "Needs_Human_Input" or state.requires_human_input and node.node_id == state.current_node_id:
        return "等待人工補充限制或接受 fallback，避免系統在不明確條件下繼續搜尋。"
    if node.children_ids:
        child_labels = []
        for child_id in node.children_ids[:3]:
            child = state.tree_nodes.get(child_id)
            child_labels.append(MODE_LABELS.get(child.search_mode, child.search_mode) if child else child_id)
        return f"已建立後續分支：{', '.join(child_labels)}。"
    if node.error_type == "LOGIC_ERROR":
        return "建議建立修正分支，先收斂 truth table 與布林需求。"
    if node.error_type == "PART_ERROR":
        return "建議建立元件最佳化分支，優先處理 mapping、負擔或正交性。"
    if state.active_frontier:
        return f"下一個待處理節點是 {state.active_frontier[0]}。"
    return "若尚未滿意目前候選，可增加預算或補充人工限制後繼續搜尋。"


def _search_next_step_summary(state: DesignState) -> dict[str, str]:
    if state.requires_human_input:
        return {"level": "warning", "text": "工作流程正在等待人工回饋。請先處理上方的人工介入面板，再繼續執行搜尋。"}
    if state.is_completed:
        return {"level": "success", "text": "搜尋已完成，最佳拓樸已可在結果檢視器中查看。"}
    if state.active_frontier:
        next_id = state.active_frontier[0]
        reason = _branch_reason_for_node(state, next_id)
        return {"level": "info", "text": f"下一個待處理節點：{next_id}。原因：{reason}。"}
    return {"level": "warning", "text": "目前沒有待處理節點；若尚未完成，請補充人工限制或選擇 fallback 拓樸。"}


def _build_search_tree_dot(state: DesignState, selected_node_id: str | None) -> str:
    if not state.tree_nodes:
        return ""
    lines = [
        "digraph SearchTree {",
        "  graph [rankdir=TB, bgcolor=\"transparent\", pad=\"0.2\", nodesep=\"0.35\", ranksep=\"0.55\"];",
        "  node [fontname=\"Arial\", fontsize=10, margin=\"0.10,0.06\"];",
        "  edge [fontname=\"Arial\", fontsize=9, color=\"#94a3b8\", arrowsize=0.7];",
    ]
    frontier = set(state.active_frontier)
    for node_id, node in state.tree_nodes.items():
        lines.append(f"  {_dot_id(node_id)} [{_search_tree_node_attrs(state, node, selected_node_id, frontier)}];")
    for node_id, node in state.tree_nodes.items():
        for child_id in node.children_ids:
            if child_id not in state.tree_nodes:
                continue
            reason = _branch_reason_for_node(state, child_id)
            lines.append(f"  {_dot_id(node_id)} -> {_dot_id(child_id)} [label=\"{_dot_escape(reason)}\"];")
    lines.append("}")
    return "\n".join(lines)


def _search_tree_node_attrs(
    state: DesignState,
    node: SearchNode,
    selected_node_id: str | None,
    frontier: set[str],
) -> str:
    label_parts = [node.node_id, MODE_LABELS.get(node.search_mode, node.search_mode)]
    if math.isfinite(node.score):
        label_parts.append(f"{node.score:.2f}")
    if node.node_id == state.current_node_id:
        label_parts.append("current")
    if node.node_id in frontier:
        label_parts.append("frontier")
    label = _dot_escape("\\n".join(label_parts))
    fill = {
        "Exploration": "#dbeafe",
        "Repair": "#ffedd5",
        "Exploitation": "#dcfce7",
    }.get(node.search_mode, "#f1f5f9")
    color = STATUS_COLORS.get(node.status, MODE_COLORS.get(node.search_mode, "#64748b"))
    penwidth = "3" if node.node_id == selected_node_id else "1.6"
    style = "rounded,filled,bold" if node.node_id in frontier or node.node_id == selected_node_id else "rounded,filled"
    return f'label="{label}", shape=box, style="{style}", fillcolor="{fill}", color="{color}", penwidth={penwidth}'


def _node_state_badges(state: DesignState, node: SearchNode) -> str:
    badges = []
    if node.node_id == state.current_node_id:
        badges.append(("#0f172a", "目前"))
    if node.node_id in state.active_frontier:
        badges.append(("#0891b2", "待處理"))
    if node.node_id == st.session_state.get("selected_node_id"):
        badges.append(("#6366f1", "選取"))
    return "".join(
        f'<span class="pill" style="background:{color};">{label}</span>'
        for color, label in badges
    )


def _summarize_feedback(feedback: str, limit: int = 110) -> str:
    compact = " ".join(feedback.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _current_step(state: DesignState) -> int:
    skill_context = getattr(state, "skill_context", "") or state.rag_context
    checks = [
        bool(state.user_intent.strip()),
        bool(skill_context),
        bool(state.logic_proposals),
        bool(state.verilog_codes),
        bool(state.candidate_topologies),
        any("ode_status" in topo for topo in state.candidate_topologies),
        bool(state.critic_feedbacks),
        state.best_topology is not None,
    ]
    for index, done in enumerate(checks):
        if not done:
            return max(0, index - 1)
    return len(checks) - 1


def _selected_node(state: DesignState) -> SearchNode | None:
    selected = st.session_state.selected_node_id or state.current_node_id
    if selected and selected in state.tree_nodes:
        return state.tree_nodes[selected]
    if state.tree_nodes:
        return next(iter(state.tree_nodes.values()))
    return None


def _best_score(state: DesignState) -> float | None:
    scores = [node.score for node in state.tree_nodes.values() if math.isfinite(node.score)]
    return max(scores) if scores else None


def _render_json_or_text(value: str) -> None:
    try:
        st.json(json.loads(value))
    except Exception:
        st.write(value)


def _safe_index(items: list[str], value: str | None) -> int:
    try:
        return items.index(value or "")
    except ValueError:
        return 0


def _child_id(parent_id: str, mode: str) -> str:
    return f"{parent_id}_{mode}_{uuid.uuid4().hex[:4]}"


def _generate_mermaid_from_spec(spec: dict[str, Any]) -> str:
    if not spec:
        return ""
    lines = ["graph LR"]
    inputs = spec.get("inputs", [])
    outputs = spec.get("outputs", [])
    logic = spec.get("logic_relation", "")
    chassis = spec.get("chassis", "Unknown Host")

    # 建立 Logic Engine 節點
    logic_label = f"\"🧬 {chassis} 電路核心<br/>規格: {logic}\"" if logic else f"\"🧬 {chassis} 電路核心\""
    lines.append(f"    Logic[{logic_label}]")

    # 建立 Inputs 連結
    for index, inp in enumerate(inputs):
        name = inp.get("name", f"Input_{index}")
        promoter = inp.get("sensor_promoter", "")
        label = f"\"{name} (感測器: {promoter})\"" if promoter else f"\"{name}\""
        lines.append(f"    In_{index}[{label}] --> Logic")

    # 建立 Outputs 連結
    for index, out in enumerate(outputs):
        name = out.get("name", f"Output_{index}")
        label = f"\"{name} (報告基因)\""
        lines.append(f"    Logic --> Out_{index}[{label}]")

    # 美化樣式
    lines.append("    style Logic fill:#f1f5f9,stroke:#334155,stroke-width:2px")
    for index in range(len(inputs)):
        lines.append(f"    style In_{index} fill:#eff6ff,stroke:#2563eb,stroke-width:1px")
    for index in range(len(outputs)):
        lines.append(f"    style Out_{index} fill:#ecfdf5,stroke:#059669,stroke-width:2px")

    return "\n".join(lines)


def _render_pm_elicitation_dashboard(state: DesignState) -> None:
    st.markdown(
        """
        <div style="padding:15px; background-color:#f8fafc; border-radius:8px; border:1px solid #e2e8f0; margin-bottom:20px;">
            <h3 style="margin:0 0 5px 0; color:#1e293b;">🧬 基因電路設計經理 (Bio-Design PM)</h3>
            <p style="margin:0; font-size:14px; color:#64748b;">
                我們將透過簡單的對話確認您的設計規格（Chassis, Inputs, Outputs, Logic 等）。
                PM Agent 會自動尋找生物學上的推薦預設值，以提高後續評分並防範模擬錯誤。
            </p>
        </div>
        """,
        unsafe_allow_html=True
    )

    field_status = state.pending_proposal.get("field_status") if state.pending_proposal else []
    if not field_status and state.structured_spec:
        ordered_fields = ["chassis", "inputs", "outputs", "logic_relation", "copy_number"]
        field_status = [
            {
                "field": field,
                "label": field.replace("_", " ").title(),
                "status": "confirmed" if field in state.structured_spec else "pending",
                "value": state.structured_spec.get(field),
            }
            for field in ordered_fields
        ]
    if field_status:
        cols = st.columns(len(field_status))
        for col, item in zip(cols, field_status):
            status = item.get("status", "pending")
            icon = "✅" if status == "confirmed" else "🔵" if status == "current" else "·"
            col.metric(
                f"{icon} {item.get('label', item.get('field', 'Field'))}",
                "完成" if status == "confirmed" else "確認中" if status == "current" else "待補",
            )

    # 1. 檢查是否有輸入 Intent
    if not state.user_intent.strip():
        st.info("💡 請在左側側邊欄的「設計需求」中輸入您的初步意圖（例如：『我需要一個溫敏型啟動子，高溫時啟動 GFP』），隨後 PM Agent 將為您補完其他規格。")
        if st.button("⚡ 略過對話，直接以大腸桿菌預設值啟動設計", use_container_width=True):
            state.structured_spec = {
                "chassis": "Escherichia coli",
                "inputs": [{"name": "IPTG", "sensor_promoter": "pLac", "type": "input_sensor"}],
                "outputs": [{"name": "sfGFP", "type": "reporter_gene"}],
                "logic_relation": "sfGFP = IPTG",
                "copy_number": 15
            }
            state.pm_stage = "completed"
            st.session_state.run_message = ("success", "已略過前置對話，已載入預設規格。")
            st.rerun()
        return

    # 2. 如果沒有 pending_proposal，嘗試初始化
    if not state.pending_proposal:
        config = st.session_state.get("llm_config", {})
        api_key = config.get("api_key", "").strip() or None
        model_name = config.get("model_name", "gpt-4o-mini").strip()
        api_base = config.get("api_base", "").strip() or None

        # 顯示載入中
        with st.spinner("設計經理正在分析意圖並規劃生物學預設推薦..."):
            call_pm_agent(state, api_key=api_key, model_name=llm_utils.resolve_agent_model("pm", model_name), api_base=api_base)
        st.rerun()

    # 3. 呈現對話歷史
    if not state.pm_chat_history:
        state.pm_chat_history.append({
            "role": "assistant",
            "content": f"你好！我是您的設計經理。我看到您的意圖是：『{state.user_intent}』。為了保證後續的電路模擬與評分能順利成功，我會逐步向您提議規格設定，您只需確認或修改即可。"
        })

    chat_container = st.container(height=280, border=True)
    with chat_container:
        for msg in state.pm_chat_history:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])

    # 4. 如果有等待確認 copy_number
    if state.pending_proposal and "missing_field" in state.pending_proposal:
        proposal = state.pending_proposal
        missing_field = proposal["missing_field"]
        proposed_value = proposal["proposed_value"]
        reason = proposal.get("proposal_reason", "無特定理由")
        ui_msg = proposal.get("ui_message", f"我為您推薦了 {missing_field} 欄位的預設值，請問您同意嗎？")
        source = proposal.get("source", "unknown")
        confidence = proposal.get("confidence", "unknown")

        st.markdown(
            f"""
            <div class="hitl-panel" style="margin-top:15px; margin-bottom:15px; border-left:5px solid #2563eb; background-color:#f1f5f9;">
                <h4 style="margin:0 0 5px 0; color:#2563eb; font-size:16px;">💡 設計經理推薦方案 ({missing_field.upper()})</h4>
                <p style="margin:0 0 5px 0; font-size:12px; color:#64748b;">來源：{_escape_html(str(source))} · 信心：{_escape_html(str(confidence))}</p>
                <p style="margin:0 0 5px 0; font-size:14px;"><strong>推薦預設：</strong><code>{json.dumps(proposed_value, ensure_ascii=False)}</code></p>
                <p style="margin:0 0 5px 0; font-size:14px; color:#475569;"><strong>推薦原因：</strong>{reason}</p>
                <div style="margin-top:8px; font-weight:bold; font-size:14px; color:#1e293b;">{ui_msg}</div>
            </div>
            """,
            unsafe_allow_html=True
        )

        col1, col2 = st.columns(2)
        with col1:
            if st.button("👍 同意推薦並繼續", type="primary", use_container_width=True):
                # 寫入規格
                state.structured_spec[missing_field] = proposed_value
                # 對話紀錄
                state.pm_chat_history.append({"role": "user", "content": f"同意使用推薦值：{json.dumps(proposed_value, ensure_ascii=False)}"})
                state.pm_chat_history.append({"role": "assistant", "content": f"已儲存 {missing_field} 設定。"})
                # 清除提案，以便下一步尋找新的缺失項
                state.pending_proposal = {}

                config = st.session_state.get("llm_config", {})
                api_key = config.get("api_key", "").strip() or None
                model_name = config.get("model_name", "gpt-4o-mini").strip()
                api_base = config.get("api_base", "").strip() or None
                call_pm_agent(state, api_key=api_key, model_name=llm_utils.resolve_agent_model("pm", model_name), api_base=api_base)
                st.rerun()

        with col2:
            if st.button("✏️ 自訂修改此項", use_container_width=True):
                st.session_state.pm_show_custom_input = True

        if st.session_state.get("pm_show_custom_input"):
            st.markdown("---")
            custom_input = st.text_input(
                f"自訂 {missing_field} 設定:",
                value=json.dumps(proposed_value, ensure_ascii=False) if not isinstance(proposed_value, (list, dict)) else ""
            )
            c_col1, c_col2 = st.columns([1, 4])
            with c_col1:
                if st.button("確認套用", type="primary", use_container_width=True):
                    try:
                        parsed_val = json.loads(custom_input)
                    except Exception:
                        if "," in custom_input:
                            parsed_val = [x.strip() for x in custom_input.split(",")]
                        else:
                            parsed_val = custom_input

                    state.structured_spec[missing_field] = parsed_val
                    state.pm_chat_history.append({"role": "user", "content": f"我想要改為：{custom_input}"})
                    state.pm_chat_history.append({"role": "assistant", "content": f"已自訂 {missing_field} 為: {json.dumps(parsed_val, ensure_ascii=False)}。"})
                    state.pending_proposal = {}
                    st.session_state.pm_show_custom_input = False

                    config = st.session_state.get("llm_config", {})
                    api_key = config.get("api_key", "").strip() or None
                    model_name = config.get("model_name", "gpt-4o-mini").strip()
                    api_base = config.get("api_base", "").strip() or None
                    call_pm_agent(state, api_key=api_key, model_name=llm_utils.resolve_agent_model("pm", model_name), api_base=api_base)
                    st.rerun()

    # 呈現電路視覺化流程圖預覽
    if state.structured_spec:
        st.markdown("---")
        st.subheader("📊 當前規格電路流程圖 (高階預覽)")
        mermaid_code = _generate_mermaid_from_spec(state.structured_spec)
        st.markdown(f"```mermaid\n{mermaid_code}\n```")

    # 提供直接啟動選項
    st.markdown("---")

    if st.button("⚡ 結束對話，將剩餘項目套用預設值並直接啟動設計", use_container_width=True):
        fallbacks = {
            "chassis": "Escherichia coli",
            "inputs": [{"name": "IPTG", "sensor_promoter": "pLac", "type": "input_sensor"}],
            "outputs": [{"name": "sfGFP", "type": "reporter_gene"}],
            "logic_relation": "sfGFP = IPTG",
            "copy_number": 15
        }
        for k, v in fallbacks.items():
            if k not in state.structured_spec:
                state.structured_spec[k] = v
        state.pm_stage = "completed"
        state.pending_proposal = {}
        st.session_state.run_message = ("success", "已略過剩餘對話，規格已齊全，可開始執行設計。")
        st.rerun()


def _escape_html(value: str) -> str:
    value = str(value)
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


if __name__ == "__main__":
    main()
