from schemas.state import CircuitState
from utils.llm_utils import call_llm


def call_design_consolidator(
    state: CircuitState,
    api_key: str | None,
    model_name: str,
    api_base: str | None = None,
) -> CircuitState:
    system_prompt = (
        "【角色與職責】\n"
        "你是一位首席系統架構師（Design Consolidator）。你的任務是將 Builder 的最終設計與 Critic 的最終審查意見整合，產出一份清晰且人類可讀的基因電路設計規格書，供使用者確認。\n\n"
        "【整合規則】\n"
        "1. 以 Builder 的最終版本為主體。\n"
        "2. 若 Critic 的最終意見中仍有【未被 Builder 回應】的 FATAL 或 MISALIGNMENT 警告，請在 Biological_Constraints 區塊中明確標註，讓使用者知悉風險。\n"
        "3. 若 Critic 的最終意見為認可或無重大異議，則不需要特別說明辯論過程。\n\n"
        "【輸出格式】\n"
        "請嚴格依序輸出以下五個區塊，使用清晰的中文說明，絕不可加入廢話：\n\n"
        "1. System_Architecture: (描述電路的整體拓樸與動態行為，包含時序延遲、記憶狀態、回饋迴路或裂解機制等。)\n"
        "2. Consortia_Configuration: (若涉及多細胞協同，說明各細胞株與胞間通訊分子；單一細胞則填 N/A。)\n"
        "3. Biological_Constraints: (條列所有生化限制與風險提示，包含 Critic 未解決的警告，若無則填 None。)\n"
        "4. Selected_Parts: (條列選用的啟動子、感測器、邏輯閘與輸出元件名稱。)\n"
        "5. Pending_Risks: (條列 Critic 提出但 Builder 選擇保留的設計特徵，並說明 Builder 的辯護理由；若無爭議則填 NONE。)\n"
    )

    user_content = (
        "Builder 最終設計：\n"
        f"{state.current_topology or ''}\n\n"
        "Critic 最終審查意見：\n"
        f"{state.latest_critic_feedback or ''}"
    )
    state.formal_specification = call_llm(
        api_key,
        model_name,
        system_prompt,
        user_content,
        api_base=api_base,
    )
    return state
