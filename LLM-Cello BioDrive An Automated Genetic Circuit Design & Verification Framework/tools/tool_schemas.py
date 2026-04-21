from __future__ import annotations

import json
from typing import Any

from tools.ode_simulator import HOST_BASELINES, run_ode_simulation
from vector_db import DEFAULT_EMBED_MODEL_NAME, search_parts


SEARCH_PARTS_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_parts",
        "description": "檢索 Cello UCF 資料庫中符合條件的生物元件，例如感測器、啟動子、邏輯閘與輸出元件。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "自然語言查詢條件。",
                },
                "n_results": {
                    "type": "integer",
                    "description": "最多回傳幾筆結果。",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}


RUN_ODE_SIMULATION_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "run_ode_simulation",
        "description": "對候選基因電路做輕量 ODE 壓力測試，用來估計 leakage、穩定性與潛在動態風險。",
        "parameters": {
            "type": "object",
            "properties": {
                "topology_description": {
                    "type": "string",
                    "description": "Builder 設計的自然語言描述或拓樸摘要。",
                },
                "focus": {
                    "type": "string",
                    "description": "模擬關注點，例如 leakage、toxicity、stability。",
                    "default": "stability",
                },
                "host_organism": {
                    "type": "string",
                    "description": "宿主生物名稱。",
                    "default": "Escherichia coli",
                },
            },
            "required": ["topology_description"],
            "additionalProperties": False,
        },
    },
}


def get_builder_tools() -> list[dict[str, Any]]:
    return [SEARCH_PARTS_TOOL]


def get_critic_tools() -> list[dict[str, Any]]:
    return [RUN_ODE_SIMULATION_TOOL]


def _normalize_host_name(host_organism: str) -> str:
    host_text = str(host_organism or "").lower()
    if "bacillus" in host_text:
        return "Bacillus subtilis"
    if "saccharomyces" in host_text or "yeast" in host_text:
        return "Saccharomyces cerevisiae"
    return "Escherichia coli"


def _resolve_host_baseline_key(host_organism: str) -> str:
    normalized = _normalize_host_name(host_organism)
    if normalized == "Bacillus subtilis":
        return "Bacillus subtilis (?航?獢輯?)"
    if normalized == "Saccharomyces cerevisiae":
        return "Saccharomyces cerevisiae (??瘥?"
    return "Escherichia coli (憭扯獢輯?)"


def _build_lightweight_topology(topology_description: str) -> dict[str, Any]:
    desc = str(topology_description or "").lower()
    if any(keyword in desc for keyword in ["toggle", "memory", "bistable", "雙穩態", "記憶"]):
        return {
            "species": ["RegA", "RegB", "Output"],
            "interactions": [
                {"source": "RegA", "target": "RegB", "type": "repression"},
                {"source": "RegB", "target": "RegA", "type": "repression"},
                {"source": "RegB", "target": "Output", "type": "repression"},
            ],
            "inducers": {"InputA": {"target": "RegA"}},
        }
    if any(keyword in desc for keyword in ["oscillat", "repressilator", "振盪"]):
        return {
            "species": ["LacI", "TetR", "cI"],
            "interactions": [
                {"source": "LacI", "target": "TetR", "type": "repression"},
                {"source": "TetR", "target": "cI", "type": "repression"},
                {"source": "cI", "target": "LacI", "type": "repression"},
            ],
            "inducers": {"IPTG": {"target": "LacI"}},
        }
    return {
        "species": ["Sensor", "Regulator", "Output"],
        "interactions": [
            {"source": "Sensor", "target": "Regulator", "type": "activation"},
            {"source": "Regulator", "target": "Output", "type": "repression"},
        ],
        "inducers": {"Signal": {"target": "Sensor"}},
    }


def _summarize_dataframe(df, output_species: str) -> dict[str, Any]:
    output_series = df[output_species] if output_species in df.columns else df.iloc[:, -1]
    leakage_window = max(3, min(25, len(output_series) // 20))
    initial_mean = float(output_series.iloc[:leakage_window].mean())
    peak_value = float(output_series.max())
    final_value = float(output_series.iloc[-1])
    dynamic_range = float(peak_value - initial_mean)
    leakage_ratio = float(initial_mean / peak_value) if peak_value > 0 else 0.0
    return {
        "output_species": output_species,
        "initial_mean": initial_mean,
        "peak_value": peak_value,
        "final_value": final_value,
        "dynamic_range": dynamic_range,
        "leakage_ratio": leakage_ratio,
    }


def execute_search_parts_tool(
    arguments_json: str,
    *,
    embedding_model: str = DEFAULT_EMBED_MODEL_NAME,
    embedding_api_key: str | None = None,
    embedding_api_base: str | None = None,
) -> str:
    try:
        args = json.loads(arguments_json or "{}")
    except Exception as exc:
        return f"TOOL_ERROR: JSON 解析失敗: {exc}"

    query = str(args.get("query", "")).strip()
    n_results_raw = args.get("n_results", 5)
    try:
        n_results = int(n_results_raw)
    except Exception:
        return f"TOOL_ERROR: n_results 必須是整數，收到: {n_results_raw!r}"

    if not query:
        return "TOOL_ERROR: query 不可為空。"

    try:
        results = search_parts(
            query,
            embedding_model=embedding_model,
            api_key=embedding_api_key,
            api_base=embedding_api_base,
            n_results=n_results,
        )
        return json.dumps(
            {
                "query": query,
                "n_results": n_results,
                "results": results,
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return f"TOOL_ERROR: search_parts 執行失敗: {exc}"


def execute_run_ode_simulation_tool(arguments_json: str) -> str:
    try:
        args = json.loads(arguments_json or "{}")
    except Exception as exc:
        return f"TOOL_ERROR: JSON 解析失敗: {exc}"

    topology_description = str(args.get("topology_description", "")).strip()
    focus = str(args.get("focus", "stability")).strip().lower() or "stability"
    normalized_host = _normalize_host_name(str(args.get("host_organism", "Escherichia coli")))
    baseline_key = _resolve_host_baseline_key(normalized_host)

    if not topology_description:
        return "TOOL_ERROR: topology_description 不可為空。"

    topology = _build_lightweight_topology(topology_description)
    params = dict(HOST_BASELINES.get(baseline_key, HOST_BASELINES["Escherichia coli (憭扯獢輯?)"]))

    if focus == "leakage":
        params["leakage"] = 0.08
    elif focus == "toxicity":
        params["transcription_rate"] = float(params["transcription_rate"]) * 1.35
        params["translation_rate"] = float(params["translation_rate"]) * 1.25
        params["leakage"] = 0.04
    else:
        params["leakage"] = 0.02

    try:
        df = run_ode_simulation(params=params, topology=topology, stimulus_curves=None)
        output_species = topology["species"][-1]
        summary = _summarize_dataframe(df, output_species)
        result = {
            "focus": focus,
            "host_organism": normalized_host,
            "topology_used": topology,
            "summary": summary,
            "interpretation_hint": "Leakage ratio 越高表示 OFF 狀態基礎表現越明顯；dynamic_range 越低表示訊號分離度越差。",
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return f"TOOL_ERROR: run_ode_simulation 執行失敗: {exc}"


TOOL_EXECUTORS = {
    "search_parts": execute_search_parts_tool,
    "run_ode_simulation": execute_run_ode_simulation_tool,
}
