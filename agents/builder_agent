from __future__ import annotations

from schemas.state import CircuitState
from tools.tool_schemas import TOOL_EXECUTORS, get_builder_tools
from utils.llm_utils import run_llm_with_tools
from vector_db import DEFAULT_EMBED_MODEL_NAME


def _build_system_prompt(state: CircuitState, *, force_zero_shot: bool) -> str:
    system_prompt = (
        "【角色與職責】\n"
        f"你是一位極具創造力且精通系統生物學的首席架構師（Builder）。你現在正在為【{state.host_organism}】設計基因電路，請確保所有元件的毒性、啟動子強度與代謝負擔評估皆符合該物種的生理限制，且使用正交於該物種的元件。\n"
        f"你的唯一任務是竭盡所能，利用可用的生物元件達成用戶的最終目標：【{state.user_intent}】\n\n"
        "【最高設計法則：意圖驅動與學理辯護】\n"
        "1. 忠於意圖的極致創新：不要被傳統數位電路的死板邏輯侷限。為了達成用戶目標，你可以大膽且合理地引入【負回饋/正回饋迴路】、【時序延遲機制】、【雙穩態記憶開關】或【細胞裂解/死亡機制】等高階生物學拓樸結構。\n"
        "2. 勇敢辯護 (Defensive Design)：面對 Critic（安全審查員）的批評時，你【不需要】無條件妥協。若 Critic 警告了代謝負擔、元件毒性或邏輯衝突，但你認為這些特徵是達成用戶目標的「必要犧牲」或「核心機制」，請以專業的分子生物學原理進行強而有力的反駁與辯護，並堅持你的設計。\n"
        "3. 元件的最佳化組合：請靈活運用提供的 RAG 元件庫，若發現無法用單一細胞實現複雜邏輯，可主動提議將電路拆分為多個互助的細胞群體（Consortia），並說明胞間通訊訊號。\n\n"
        "【工具使用規則】\n"
        "當你缺少特定感測器、啟動子、邏輯閘或輸出元件時，你應主動呼叫 `search_parts` 工具，而不是假設零件存在。\n"
        "收到工具回傳後，必須根據檢索結果重新組織你的設計，不可忽略工具資訊。\n\n"
        "【回應格式規範】\n"
        "若未收到 Critic 意見，請直接給出包含感測器、邏輯閘、輸出與動態調控原理的完整架構 (V1)。\n"
        "若收到 Critic 意見，請先明確表態【接受修改】或【學理反駁】，接著給出迭代後的設計與原理解釋。"
    )
    if force_zero_shot:
        system_prompt += "\n\n【初始背景】\n目前沒有可靠的靜態元件庫摘要，請更積極使用工具檢索。"
    else:
        system_prompt += f"\n\n【初始 RAG 參考】\n{state.rag_context}"
    return system_prompt


def _build_user_content(state: CircuitState) -> str:
    if state.latest_critic_feedback:
        return (
            "以下是 Critic 的最新意見。必要時先呼叫工具檢索缺少的元件，再提出修正版設計：\n\n"
            f"{state.latest_critic_feedback}"
        )
    if state.seed_debate_transcript:
        return (
            "以下是先前的辯論逐字稿。請延續脈絡，必要時使用工具，再提出新的 Builder 設計：\n\n"
            f"{state.seed_debate_transcript}"
        )
    return (
        "請根據使用者意圖提出第一版設計。若元件資訊不足，請主動呼叫工具檢索。\n\n"
        f"使用者意圖：{state.user_intent}"
    )


def call_builder(
    state: CircuitState,
    api_key: str | None,
    model_name: str,
    *,
    api_base: str | None = None,
    force_zero_shot: bool = False,
    embedding_model: str = DEFAULT_EMBED_MODEL_NAME,
    embedding_api_key: str | None = None,
    embedding_api_base: str | None = None,
    max_tool_rounds: int = 3,
) -> CircuitState:
    resolved_embedding_api_key = embedding_api_key if embedding_api_key is not None else api_key
    resolved_embedding_api_base = embedding_api_base if embedding_api_base is not None else api_base

    result = run_llm_with_tools(
        api_key=api_key,
        model_name=model_name,
        system_prompt=_build_system_prompt(state, force_zero_shot=force_zero_shot),
        user_content=_build_user_content(state),
        tools=get_builder_tools(),
        tool_executors=TOOL_EXECUTORS,
        api_base=api_base,
        temperature=0.3,
        max_tool_rounds=max_tool_rounds,
        error_prefix="Builder",
        tool_executor_kwargs={
            "search_parts": {
                "embedding_model": embedding_model,
                "embedding_api_key": resolved_embedding_api_key,
                "embedding_api_base": resolved_embedding_api_base,
            }
        },
    )
    state.current_topology = result["final_content"] if result["ok"] else result["error"]
    return state
