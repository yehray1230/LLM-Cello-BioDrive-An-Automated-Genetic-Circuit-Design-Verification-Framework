from __future__ import annotations

import json

from schemas.state import CircuitState
from tools.tool_schemas import TOOL_EXECUTORS, get_critic_tools
from utils.llm_utils import run_llm_with_tools


def _build_system_prompt(state: CircuitState, *, force_zero_shot: bool) -> str:
    system_prompt = (
        "【角色與職責】\n"
        f"你是一位高階合成生物學審查員（Critic）。你的任務是確保 Builder 提出的基因電路設計既符合「使用者的原始意圖」，又具備在標靶細胞【{state.host_organism}】上的生物物理學可行性與工程穩定性。\n\n"
        f"【使用者的設計意圖（Target Intent）】：{state.user_intent}\n\n"
        "【多維度審查法則】\n"
        "你在審查時必須進行以下三個層次的評估，並於回覆最前方清楚標示對應的標籤：\n\n"
        "1. 🛑 [INTENT_INVALID 意圖不合理]：檢視使用者的需求本身是否違反分子生物學常理，或超出當前合成生物學技術極限。若是，請指出不合理處，並建議 Builder 如何引導使用者修改目標。\n"
        "2. ⚠️ [MISALIGNMENT 意圖偏離]：Builder 的設計雖然安全，但卻因為「過度保守」或「邏輯錯誤」而沒有達成使用者的原始意圖（例如：要求振盪器卻給單向開關；或使用者要求細胞裂解，Builder 卻移除了毒性元件）。請要求 Builder 重新對齊意圖。\n"
        "3. 🛑 [FATAL 致命阻斷]：設計中出現「未預期」的邏輯短路或嚴重的生物工程缺失。⚠️注意：若使用者的意圖本就包含『細胞死亡』、『裂解』或『動態負回饋』，對應的元件毒性即為【合法設計】，絕不可標記為 FATAL，但需嚴格審查其洩漏表現（Leakage）。\n\n"
        "【重點審查清單 (Checklist)】\n"
        "在評估是否給出 [FATAL] 或修改建議時，請逐一檢查下列真實工程限制。除了邏輯正確性外，你必須以【實體零件】的角度來批判：\n\n"
        "4-1 代謝負擔與資源競爭：設計中是否同時開啟過多強啟動子？這在當前的【宿主細胞背景】下，是否會耗盡 ATP 或核糖體導致生長停滯？\n"
        "4-2 啟動子漏電 (Leakage)：在 OFF 狀態下，系統是否會有微量的基礎表現？對於毒性蛋白質或高敏感度邏輯閘，這是否會導致系統崩潰？\n"
        "4-3 零件干擾與正交性：系統中多個阻遏蛋白或 RNA 零件的結合位點是否足夠專一？會不會發生交互干擾（Cross-talk）？\n"
        "4-4 產物毒性：大量表現的中間代謝物對當前宿主是否具備非預期的毒性？（若使用者核心意圖為殺死細胞則忽略此點）。\n"
        "4-5 訊號匹配與動態範圍：上游邏輯閘輸出的蛋白質濃度，是否足以跨越下游啟動子的感應閾值？（例如：弱啟動子無法驅動需要高濃度阻遏蛋白的開關）。\n"
        "4-6 時序延遲與競爭危害 (Race Condition)：設計是否忽略了蛋白質轉錄、轉譯與摺疊的時間差？若系統依賴兩個訊號同時到達，是否會因為延遲而產生錯誤的短暫輸出（Glitch）？\n"
        "4-7 演化穩定性：設計是否包含過多重複序列，導致細胞容易透過同源重組破壞迴路？\n\n"
        "【工具使用規則】\n"
        "若你懷疑存在 leakage、毒性、動態不穩定或 race condition，可以主動呼叫 `run_ode_simulation` 工具取得輕量動力學證據，再據此下結論。\n\n"
        "【審查輸出格式】\n"
        "請綜合以上條件進行無情但精確的點評。專注於物理與生物矛盾，直接給出修正指令。並在結尾給出明確的【審查結論：通過 (PASS) / 需修改 (REVISE) / 駁回 (REJECT)】。"
    )
    if force_zero_shot:
        system_prompt += "\n\n【初始背景】\n目前沒有可靠的靜態元件庫摘要，若需要動態證據請主動使用工具。"
    else:
        system_prompt += f"\n\n【初始 RAG 參考】\n{state.rag_context}"
    return system_prompt


def _build_user_content(state: CircuitState) -> str:
    if state.seed_debate_transcript:
        return (
            "以下是先前辯論逐字稿與 Builder 最新方案。若你需要動力學證據，可先呼叫工具。\n\n"
            f"{state.seed_debate_transcript}\n\n"
            f"{state.current_topology or ''}"
        )
    return state.current_topology or ""


def call_critic(
    state: CircuitState,
    api_key: str | None,
    model_name: str,
    *,
    api_base: str | None = None,
    force_zero_shot: bool = False,
    max_tool_rounds: int = 3,
) -> CircuitState:
    result = run_llm_with_tools(
        api_key=api_key,
        model_name=model_name,
        system_prompt=_build_system_prompt(state, force_zero_shot=force_zero_shot),
        user_content=_build_user_content(state),
        tools=get_critic_tools(),
        tool_executors=TOOL_EXECUTORS,
        api_base=api_base,
        temperature=0.2,
        max_tool_rounds=max_tool_rounds,
        error_prefix="Critic",
    )

    latest_simulation_payload: dict | None = None
    for tool_output in result["tool_outputs"]:
        if tool_output["tool_name"] == "run_ode_simulation" and not str(tool_output["content"]).startswith("TOOL_ERROR:"):
            try:
                latest_simulation_payload = json.loads(tool_output["content"])
            except Exception:
                latest_simulation_payload = {"raw_tool_output": tool_output["content"]}

    if latest_simulation_payload is not None:
        state.simulation_results = latest_simulation_payload

    critic_feedback = result["final_content"] if result["ok"] else result["error"]
    state.critic_feedbacks.append(critic_feedback)
    if result["ok"]:
        state.is_approved = "FATAL" not in critic_feedback.upper()
    return state
