import re
import streamlit as st
import litellm
import litellm.exceptions

from tools.vector_retriever import get_compressed_rag_context_v2
from schemas.state import CircuitState


# =========================
# 後端邏輯：核心轉譯函式
# =========================
def _model_requires_api_key(model_name: str) -> bool:
    # LiteLLM: ollama/* 走本地，不需要 API key；其他供應商通常需要
    return not str(model_name or "").startswith("ollama/")

def _validate_verilog_ast(code: str) -> tuple[bool, str]:
    """輕量級正則語意校驗，確保具備 Cello Verilog 所需基本要素。"""
    if "module " not in code or "endmodule" not in code:
        return False, "缺乏 `module` 或 `endmodule` 宣告。"
    if "input " not in code:
        return False, "缺乏 `input` 腳位宣告。"
    if "output " not in code:
        return False, "缺乏 `output` 腳位宣告。"
    # 簡易邏輯運算字眼檢查
    has_logic = any(kw in code for kw in ["and(", "or(", "not(", "nor(", "nand(", "assign "])
    if not has_logic:
        return False, "缺乏具體邏輯運算 (如 and, or, not) 或 assign 語句。"
    return True, ""


def generate_verilog(user_intent: str, api_key: str | None, model_name: str, api_base: str | None = None) -> str:
    """
    專家四：Verilog 編譯器 (Verilog Compiler)
    使用指定的 OpenAI API Key，根據使用者的自然語言需求（通常為設計整合者所產出的規格），
    產生對應的 Cello Verilog 程式碼。

    回傳：
    - 成功：純 Verilog 程式碼字串。
    - 失敗：以「錯誤：」開頭的錯誤訊息字串（方便前端識別）。
    """
    if _model_requires_api_key(model_name) and (not api_key or not api_key.strip()):
        return "錯誤：尚未提供模型供應商 API Key。"

    if not user_intent or not user_intent.strip():
        return "錯誤：自然語言需求不可為空白。"

    
    system_prompt = """You are an expert biological circuit compiler for the Cello CAD software. Your ONLY job is to translate structured specifications into standard structural/dataflow Verilog code.

STRICT RULES:

1. Output ONLY raw, valid Verilog code.
2. NO Markdown formatting (Do NOT use ```verilog or ```).
3. Convert "Biological_Constraints" and "Selected_Parts" into Verilog comments using `// Cello pragma:` at the top.
4. 【Logic Translation Rules】:
   - For simple combinational logic, use gates (and, or, not, etc.) or assign statements.
   - For sequential biological logic (e.g., biological memory, toggle switches), use Behavioral Verilog (e.g., `reg`, `always` blocks, edge triggers) to represent state changes.
   - For temporal dynamics (e.g., delayed expression, lysis countdown), you may use Verilog delay syntax (e.g., `#10`) to model the biological time delay.
5. 【Multi-Cellular Consortia】: If the specification involves multiple cell types communicating with each other (e.g., Sender and Receiver), you MUST output multiple separate `module` blocks, and potentially a top-level module to wire the intercellular chemical signals together.
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                "請根據以下自然語言描述，設計對應的 Cello Verilog 基因電路：\n"
                f"{user_intent}"
            ),
        },
    ]

    max_retries = 3

    for attempt in range(max_retries):
        try:
            response = litellm.completion(
                model=model_name,
                messages=messages,
                temperature=0.2,  # 降低隨機性，讓輸出更穩定
                api_key=api_key.strip() if api_key and api_key.strip() else None,
                api_base=api_base.strip() if api_base and api_base.strip() else None,
            )

            raw_output = response.choices[0].message["content"]

            # 正規表達式區塊萃取：只捕捉 // Cello pragma 註解以及 module ... endmodule 內容
            match = re.search(r"(?s)(?:^\s*//.*?\n)*module\s+\w+\s*\(.*?\);.*?endmodule", raw_output, re.MULTILINE)
            if not match:
                err_msg = "無法從輸出中萃取完整的 Verilog 區塊 (module ... endmodule)。"
                if attempt < max_retries - 1:
                    messages.append({"role": "assistant", "content": raw_output})
                    messages.append({"role": "user", "content": f"【格式校驗失敗】{err_msg} 請務必只產出純 Verilog 程式碼，移除其餘非語法宣告的對白與 Markdown。"})
                    continue
                return f"錯誤：{err_msg}\n模型輸出原文：\n{raw_output}"

            extracted_code = match.group(0).strip()

            # 輕量級語法與語意校驗
            is_valid, val_err = _validate_verilog_ast(extracted_code)
            if not is_valid:
                if attempt < max_retries - 1:
                    messages.append({"role": "assistant", "content": extracted_code})
                    messages.append({"role": "user", "content": f"【語意校驗失敗】{val_err} 請修正後重新產出。"})
                    continue
                return f"錯誤：多次重試後語法校驗依然失敗：{val_err}"

            # 若通過校驗，立即返回
            return extracted_code

        except litellm.exceptions.AuthenticationError:
            return "錯誤：模型供應商 API 驗證失敗，請確認 API Key 是否正確與有效。"
        except litellm.exceptions.RateLimitError:
            return "錯誤：已達到模型供應商使用上限或額度不足，請稍後再試或檢查帳戶狀態。"
        except litellm.exceptions.BadRequestError as e:
            return f"錯誤：請求參數不正確：{str(e)}"
        except litellm.exceptions.APIError as e:
            return f"錯誤：呼叫模型供應商 API 時發生問題：{str(e)}"
        except Exception as e:
            return f"錯誤：系統發生未預期錯誤：{str(e)}"

    return "錯誤：產生 Verilog 失敗，超過最大重試次數。"


# =========================
# 多機辯論區塊（Reflexion）：三輪迭代 設計→評估→修改
# =========================
from agents.consolidator_agent import call_design_consolidator


import itertools
import ast

def generate_test_vectors(consolidated_design: str, api_key: str | None, model_name: str, api_base: str | None = None) -> dict | tuple[bool, str]:
    """
    Test Vector Generation 模組
    從 consolidated_design 萃取環境感測器/輸入元件清單，並動態產生 2^n 布林真值表組合。
    """
    system_prompt = (
        "【角色與職責】\n"
        "你是一個資料萃取助理。請檢視下方 consolidated_design 中「4. Selected_Parts」與「1. System_Architecture」區塊，"
        "精準萃取出所有扮演「輸入元件 / 環境感測器 / Inducers」的化學物質或物理訊號名稱（例如：IPTG, aTc, Arabinose, Salicylate 等）。\n\n"
        "【輸出格式】\n"
        "請只輸出一個 Python List 格式的字串，包含所有找出的感測器名稱。如果沒有發現任何外部輸入，請回傳空的 List []。\n"
        "絕不要包含 markdown 標籤（如 ```json 等）或任何其他文字解釋。\n"
        "範例輸出：\n"
        "['IPTG', 'aTc']"
    )
    
    user_content = f"【Consolidated Design】\n{consolidated_design}"
    
    try:
        response = litellm.completion(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,
            api_key=api_key.strip() if api_key and api_key.strip() else None,
            api_base=api_base.strip() if api_base and api_base.strip() else None,
        )
        
        raw_output = response.choices[0].message["content"].strip()
        
        if raw_output.startswith("```"):
            raw_output = "\n".join(raw_output.split("\n")[1:])
        if raw_output.endswith("```"):
            raw_output = raw_output[:-3]
        raw_output = raw_output.strip()
        
        try:
            inputs = ast.literal_eval(raw_output)
            if not isinstance(inputs, list):
                return False, "解析出的感測器清單不是 List 格式。"
        except Exception:
            return False, f"LLM 輸出的感測器清單無法解析為 Python List：{raw_output}"
            
        if not inputs:
            return {"inputs": [], "test_vectors": [{"state_name": "default_state"}]}
            
        test_vectors = []
        combinations = list(itertools.product([0.0, 1.0], repeat=len(inputs)))
        
        for combo in combinations:
            vec = {}
            state_str = ""
            for idx, input_name in enumerate(inputs):
                vec[input_name] = combo[idx]
                state_str += str(int(combo[idx]))
            vec["state_name"] = f"{state_str}_state"
            test_vectors.append(vec)
            
        return {
            "inputs": inputs,
            "test_vectors": test_vectors
        }
        
    except litellm.exceptions.AuthenticationError:
        return False, "錯誤：模型供應商 API 驗證失敗，請確認 API Key 是否正確與有效。"
    except Exception as e:
        return False, f"未預期錯誤：{str(e)}"



def format_sessions_for_display(sessions: list[list[dict]]) -> str:
    """將多段三輪辯論整理成給使用者閱讀的完整文字。"""
    parts: list[str] = []
    for si, session in enumerate(sessions, start=1):
        parts.append(f"════════ 在線迭代對抗紀錄 {si} ════════")
        for round_num, turn in enumerate(session, start=1):
            parts.append(f"\n── 迭代輪次 {round_num} ──")
            parts.append("\n【合成生物學系統工程師】\n")
            parts.append(turn.get("builder", ""))
            parts.append("\n\n【嚴苛的生物安全與可行性審查員】\n")
            parts.append(turn.get("critic", ""))
        parts.append("\n")
    return "\n".join(parts).strip()


def format_sessions_for_llm(sessions: list[list[dict]]) -> str:
    """與 display 相同結構即可，供後續輪次餵給 LLM。"""
    return format_sessions_for_display(sessions)


from workflows.reflexion_controller import run_reflexion_workflow
# =========================
# 結果區塊：顯示 Verilog 與下載（路線 A：手動介接 Cello）
# =========================
def render_verilog_result(verilog_content: str) -> None:
    """顯示生成的 Verilog 程式碼，並提供 .v 檔案下載按鈕（路線 A）。"""
    st.success("生成完成！以下為模型產生的 Verilog 程式碼：")
    st.code(verilog_content, language="verilog")

    # 路線 A：提供下載按鈕，讓使用者可將 .v 檔匯入 Cello CAD 網頁版
    # 未來擴充點：此處可將 downloaded_file 替換為直接呼叫 Cello CLI / API 的函式 (路線 B)
    st.download_button(
        label="下載 Verilog 檔案 (.v) 供 Cello 使用",
        data=verilog_content,
        file_name="design_circuit.v",
        mime="text/plain",
    )


# =========================
# 前端介面：Streamlit App
# =========================
def main():
    # 設定頁面基本資訊
    st.set_page_config(
        page_title="LLM-Cello BioDrive MVP",
        page_icon="",
        layout="wide",
    )

    # 側邊欄：模型供應商與金鑰設定
    with st.sidebar:
        st.header("模型設定")

        provider = st.selectbox(
            "模型供應商 (Provider)",
            options=["OpenAI", "Anthropic", "Google", "Local (Ollama)", "Groq"],
            index=0,
        )

        default_model_by_provider = {
            "OpenAI": "gpt-5.4-mini",
            "Anthropic": "claude-sonnet-4-6",
            "Google": "gemini-3-flash-preview",
            "Local (Ollama)": "ollama/llama3.3",
            "Groq": "groq/llama-3.3-70b-versatile",
        }

        preset_models_by_provider = {
            "OpenAI": ["gpt-5.4-mini", "gpt-5.4", "gpt-5.4-nano", "o4", "o4-mini"],
            "Anthropic": ["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5-20251001"],
            "Google": ["gemini-3-flash-preview", "gemini-3.1-pro-preview", "gemini-3.1-flash-lite-preview"],
            "Local (Ollama)": ["ollama/llama3.3", "ollama/llama3.2", "ollama/llama3.1", "ollama/qwen2.5", "ollama/deepseek-r1"],
            "Groq": ["groq/llama-3.3-70b-versatile", "groq/deepseek-r1-distill-llama-70b", "groq/mixtral-8x7b-32768", "groq/gemma2-9b-it"],
        }

        model_name = st.selectbox(
            "模型名稱 (Model)",
            options=preset_models_by_provider.get(provider, [default_model_by_provider[provider]]),
            index=0,
        )

        st.markdown("---")
        st.subheader("Embeddings / RAG 設定")

        embed_provider = st.selectbox(
            "Embeddings 供應商",
            options=["Local (Ollama)", "OpenAI", "Anthropic", "Google"],
            index=0,
        )

        embed_model_presets = {
            "Local (Ollama)": ["ollama/nomic-embed-text", "ollama/mxbai-embed-large"],
            "OpenAI": ["text-embedding-3-small", "text-embedding-3-large"],
            "Anthropic": ["claude-3-embedding-2024-10-22"],
            "Google": ["text-embedding-004"],
        }
        embedding_model = st.selectbox(
            "Embedding Model",
            options=embed_model_presets.get(embed_provider, ["ollama/nomic-embed-text"]),
            index=0,
        )

        embedding_api_base: str | None = None
        embedding_api_key: str | None = None
        if embed_provider == "Local (Ollama)":
            embedding_api_base = st.text_input(
                "Ollama 伺服器連線網址 (API Base)",
                value="http://localhost:11434",
                placeholder="http://localhost:11434",
                help="請在此輸入 Ollama 的 API 伺服器網址，通常為 http://localhost:11434。**請勿在此輸入 API Key**。",
            )
            st.caption("ℹ️ Local Ollama 模式通常不需要 API Key。")
        else:
            embedding_api_key = st.text_input(
                f"{embed_provider} API Key（用於 RAG 向量檢索）",
                type="password",
                placeholder=f"請貼入你的 {embed_provider} API Key",
            )
            embedding_api_base = None

        st.markdown("---")
        st.subheader("LLM 金鑰與伺服器設定")

        # 根據 Provider 設定預設 Base URL
        if provider == "Local (Ollama)":
            default_base_url = "http://localhost:11434"
        elif provider == "Groq":
            default_base_url = "https://api.groq.com/openai/v1"
        else:
            default_base_url = ""

        help_text = "例如 http://localhost:11434 (Ollama) 或 https://api.groq.com/openai/v1 (Groq)"

        llm_api_base = st.text_input(
            "API Base URL (可選)",
            value=default_base_url,
            placeholder="留空則使用 LiteLLM 預設",
            help=help_text,
        )

        llm_api_key: str | None = None
        if provider != "Local (Ollama)":
            llm_api_key = st.text_input(
                f"{provider} API Key（用於 LLM 生成）",
                type="password",
                help="此金鑰僅在本次瀏覽器工作階段中使用，不會儲存在伺服器端。",
            )
        else:
            st.info("你已選擇 Local (Ollama)。請確認本地端 Ollama 服務已啟動（例如 `ollama serve`），且模型已下載。")

    # 主畫面標題與說明
    st.title("LLM-Cello BioDrive: 基於大型語言模型之基因電路自動化轉譯框架")
    st.subheader("由自然語言需求自動化生成基因電路網表 (Netlist)")

    host_options = ["Escherichia coli (大腸桿菌)", "Bacillus subtilis (枯草桿菌)", "Saccharomyces cerevisiae (釀酒酵母)"]
    host_organism = st.selectbox(
        "請選擇預計使用的寄主物種 (Host Organism)：",
        options=host_options,
        index=0,
        help="選擇底盤細胞將強制約束後續的轉譯、多智能體除錯範圍與 ODE 解算基底參數。"
    )

    # 多行文字輸入框：讓使用者輸入自然語言需求
    user_intent = st.text_area(
        "請輸入你的合成生物學設計需求（可以使用中文）：",
        height=200,
        placeholder="例：設計一個基因迴路：當環境中有 IPTG 且沒有 aTc 時，表現出 YFP 螢光蛋白。",
    )

    # 用來在重新執行後仍能顯示結果與下載按鈕（例如按下「下載」後畫面不丟失）
    if "last_verilog" not in st.session_state:
        st.session_state["last_verilog"] = None
    if "debate_review" not in st.session_state:
        st.session_state["debate_review"] = None
    if "consolidated_design" not in st.session_state:
        st.session_state["consolidated_design"] = None
    if "debate_run_id" not in st.session_state:
        st.session_state["debate_run_id"] = 0
    if "debate_extra_widget_id" not in st.session_state:
        st.session_state["debate_extra_widget_id"] = 0

    def _compile_to_verilog(final_spec: str) -> bool:
        """專家四：Verilog Compiler。將規格總結轉換為 Verilog。"""
        st.markdown("---")
        st.markdown("**合成生物學硬體描述語言生成中 (Generating HDL)…**")
        with st.spinner("HDL 生成中 (Generating HDL)…"):
            verilog_result = generate_verilog(final_spec, llm_api_key, model_name, api_base=llm_api_base)
        if verilog_result.startswith("錯誤："):
            st.error(verilog_result)
            return False
        st.session_state["last_verilog"] = verilog_result
        return True

    # ---------- 多機辯論區塊（Reflexion）：三輪迭代 設計→評估→修改；通過人機審核後才呼叫 Summarizer ----------
    if st.button("啟動基因電路設計流程"):
        if provider != "Local (Ollama)" and (not llm_api_key or not llm_api_key.strip()):
            st.error("請先在左側輸入模型供應商的 API Key。")
        elif not user_intent or not user_intent.strip():
            st.error("請先輸入合成生物學設計需求。")
        else:
            st.session_state["debate_review"] = None
            st.session_state["consolidated_design"] = None
            st.session_state["debate_run_id"] = int(st.session_state["debate_run_id"]) + 1
            run_id = int(st.session_state["debate_run_id"])
            intent_clean = user_intent.strip()
                
            with st.spinner("動態檢索與特徵萃取元件庫中 (Retrieving Component Features)..."):
                rag_context = get_compressed_rag_context_v2(
                    intent_clean,
                    embedding_model=embedding_model,
                    embedding_api_key=embedding_api_key,
                    embedding_api_base=embedding_api_base,
                )
            force_zero_shot = False
            if rag_context.startswith("錯誤："):
                st.warning(f"{rag_context} 將切換為「無依賴備援生成模式 (Fallback Generation Mode)」，使用離線基礎元件庫進行推論。")
                try:
                    import json
                    with open("default_cello_gates.json", "r", encoding="utf-8") as f:
                        fallback_data = json.load(f)
                        rag_context = "\n".join(fallback_data)
                except Exception:
                    rag_context = "【基礎感測器 Sensor】IPTG 感測器 (Tac), aTc 感測器 (Tet)\n【邏輯閘 Gate】NOT Gate, NOR Gate\n【輸出元件】YFP"
                force_zero_shot = True

            with st.expander("查看檢索到的可用元件 (RAG Context)", expanded=False):
                st.text_area(
                    "rag_context_preview",
                    value=rag_context,
                    height=220,
                    disabled=True,
                    label_visibility="collapsed",
                    key=f"rag_ctx_{run_id}",
                )

                def on_round_complete(rn: int, btxt: str, ctxt: str) -> None:
                    with st.expander(f"迭代設計 第 {rn} 輪", expanded=True):
                        st.markdown("**合成生物學系統工程師（Builder）**")
                        st.text_area(
                            "builder_output",
                            value=btxt,
                            height=140,
                            disabled=True,
                            label_visibility="collapsed",
                            key=f"builder_{run_id}_{rn}",
                        )
                        st.markdown("**嚴苛的生物安全與可行性審查員（Critic）**")
                        st.text_area(
                            "critic_output",
                            value=ctxt,
                            height=120,
                            disabled=True,
                            label_visibility="collapsed",
                            key=f"critic_{run_id}_{rn}",
                        )

                initial_state = CircuitState(
                    user_intent=intent_clean,
                    host_organism=host_organism,
                    rag_context=rag_context,
                )
                ok, final_state = run_reflexion_workflow(
                    llm_api_key,
                    model_name,
                    initial_state,
                    on_round_complete=on_round_complete,
                    api_base=llm_api_base,
                    force_zero_shot=force_zero_shot,
                    host_organism=host_organism
                )
                session_rounds = [
                    {"builder": record.builder_output, "critic": record.critic_output}
                    for record in final_state.round_history
                ]
                final_design_v3 = final_state.current_topology
                err_msg = final_state.last_error
                if not ok:
                    st.error(err_msg or "辯論過程發生錯誤。")
                elif final_design_v3 is not None:
                    st.session_state["debate_review"] = {
                        "sessions": [session_rounds],
                        "effective_intent": intent_clean,
                        "original_intent": intent_clean,
                        "rag_context": rag_context,
                        "force_zero_shot": force_zero_shot,
                    }
                    st.success("已完成三輪對抗式迭代。請檢視模擬結果並指定後續排程。")

    # ---------- 辯論後人機審核：完整對話 + 三選一 ----------
    dr = st.session_state.get("debate_review")
    if dr:
        st.markdown("---")
        st.subheader("當前有效驗證意圖 (Validated Design Intent)")
        st.info(dr["effective_intent"])

        st.subheader("辯論完整紀錄（合成生物學系統工程師 ↔ 生物安全與可行性審查員）")
        chat_container = st.container(height=600)
        with chat_container:
            for si, session in enumerate(dr["sessions"], start=1):
                if len(dr["sessions"]) > 1:
                    st.markdown(f"### ════════ 辯論段落 {si} ════════")
                for round_num, turn in enumerate(session, start=1):
                    with st.chat_message("user", avatar="🛠️"):
                        st.markdown(f"**【合成生物學系統工程師】 第 {round_num} 輪**")
                        st.markdown(turn.get("builder", ""))
                    with st.chat_message("assistant", avatar="🛡️"):
                        st.markdown(f"**【安全審查員】 第 {round_num} 輪**")
                        st.markdown(turn.get("critic", ""))

        st.subheader("請指定後續排程")
        st.caption(
            "（1）您可以讓 Design Consolidator 總結本次辯論為規格矩陣，或是繼續辯論。\n"
            "（2）引入額外生物學約束會併入有效意圖，並將**此前所有對抗紀錄**一併作為邊界條件。"
        )

        col_a, col_b = st.columns(2)
        
        has_summary = st.session_state.get("consolidated_design") is not None

        if not has_summary:
            with col_a:
                summarize_btn = st.button("(1) 讓 Design Consolidator 總結最終設計", key="debate_summarize")
            with col_b:
                redo = st.button("(2) 取消並重新執行追加對抗式迭代", key="debate_redo")
            
            if summarize_btn:
                if provider != "Local (Ollama)" and (not llm_api_key or not llm_api_key.strip()):
                    st.error("請先在左側輸入模型供應商的 API Key。")
                else:
                    last_session = dr["sessions"][-1]
                    final_v3_builder = last_session[-1]["builder"]
                    final_v3_critic = last_session[-1]["critic"]
                    with st.spinner("特徵矩陣萃取中 (Extracting Specification Matrix)…"):
                        consolidation_state = CircuitState(
                            user_intent=dr["effective_intent"],
                            host_organism=host_organism,
                            current_topology=final_v3_builder,
                            critic_feedbacks=[final_v3_critic],
                        )
                        consolidation_state = call_design_consolidator(
                            consolidation_state,
                            llm_api_key,
                            model_name,
                            api_base=llm_api_base,
                        )
                        final_spec = consolidation_state.formal_specification or ""
                    if final_spec.startswith("錯誤："):
                        st.error(final_spec)
                    else:
                        st.session_state["consolidated_design"] = final_spec
                        st.rerun()
        else:
            st.markdown("### 設計整合者 (Design Consolidator) 總結結果")
            st.info("您可以在送交 Verilog 編譯器前，手動編輯並微調以下設計規格：")
            # 讓使用者可以編輯
            edited_spec = st.text_area(
                "形式化邏輯與生化限制矩陣 (Formal Logic & Constraints)",
                value=st.session_state["consolidated_design"],
                height=300,
                key="edited_consolidated_design"
            )
            
            st.markdown("#### 自動化效能評估器 (Automated Oracle Verification)")
            col_target1, col_target2 = st.columns(2)
            with col_target1:
                target_spec_str = st.text_input("預期真值表 (Target Spec JSON)", value='{"0_state": 0, "1_state": 1}')
            with col_target2:
                target_species = st.text_input("評分基準輸出物種 (Output Species)", value='GFP')
                
            with st.expander("進階動態模擬設定 (Advanced Simulation Settings)", expanded=False):
                noise_level = st.slider("參數噪音擾動範圍 (Noise Level)", min_value=0.0, max_value=0.3, value=0.05, step=0.01)
                mc_iterations = st.slider("蒙地卡羅抽樣次數 (Monte Carlo Iterations)", min_value=1, max_value=100, value=10, step=1)
                pass_threshold = st.slider("穩健度及格門檻 (Pass Threshold %)", min_value=50, max_value=100, value=90, step=5)
                
            col_a, col_b = st.columns(2)
            with col_a:
                verify_btn = st.button("執行 Test Vector 萃取與 ODE 自動驗證", key="debate_oracle_verify")
            with col_b:
                compile_btn = st.button("(1) 忽略/確認總結無誤，交由 Verilog Compiler", key="debate_compile_gen")
                
            if verify_btn:
                if provider != "Local (Ollama)" and (not llm_api_key or not llm_api_key.strip()):
                    st.error("請先在左側輸入模型供應商的 API Key。")
                else:
                    import json
                    from oracle_evaluator import CircuitEvaluator
                    from tools.ode_simulator import run_ode_simulation
                    
                    try:
                        target_spec = json.loads(target_spec_str)
                    except Exception as e:
                        st.error(f"真值表解析失敗: {e}")
                        st.stop()
                        
                    with st.spinner("環境基準解析中 (Test Vector Generation)..."):
                        tv_result = generate_test_vectors(edited_spec, llm_api_key, model_name, api_base=llm_api_base)
                    
                    if isinstance(tv_result, tuple) and tv_result[0] is False:
                        st.error(f"Test Vector 產生錯誤: {tv_result[1]}")
                    else:
                        st.success("Test Vector 產生成功：")
                        st.json(tv_result)
                        
                        # 擷取 UI 下方的預設 Topology (簡化版處理) 作為驗證目標
                        test_topology = {
                            "species": ["LacI", "TetR", "GFP"],
                            "interactions": [
                                {"source": "TetR", "target": "LacI", "type": "repression"},
                                {"source": "LacI", "target": "TetR", "type": "repression"},
                                {"source": "TetR", "target": "GFP", "type": "repression"} 
                            ],
                            "inducers": {
                                "IPTG": {"target": "LacI"},
                                "aTc": {"target": "TetR"}
                            }
                        }
                        
                        NUM_SAMPLES = mc_iterations
                        mc_ode_results = [{} for _ in range(NUM_SAMPLES)]
                        with st.spinner(f"正在執行蒙地卡羅多狀態批次 ODE 模擬 ({NUM_SAMPLES} 組變異)..." if NUM_SAMPLES > 1 and noise_level > 0.0 else "正在執行單次確定性 ODE 模擬..."):
                            for vec in tv_result["test_vectors"]:
                                state_name = vec.pop("state_name")
                                # 組裝 stimulus_curves (使用恆定大數值 5000 模擬 1, 0 模擬 0)
                                stim_dict = {}
                                for ind_name, bool_val in vec.items():
                                    stim_dict[ind_name] = {"type": "pulse", "start": 0, "duration": 100000, "amp": 50000.0 if bool_val > 0 else 0.0}
                                
                                # 使用預設空參數即可 (會 fall back 到通用設定)
                                from tools.ode_simulator import run_monte_carlo_ode_simulation
                                dfs = run_monte_carlo_ode_simulation({}, topology=test_topology, stimulus_curves=stim_dict, num_samples=NUM_SAMPLES, noise_level=noise_level)
                                
                                for i, df in enumerate(dfs):
                                    mc_ode_results[i][state_name] = df
                        
                        evaluator = CircuitEvaluator()
                        # 向下傳遞 user_intent 作為意圖判斷 (抓取使用者字串)
                        result = evaluator.evaluate_monte_carlo_results(mc_ode_results, target_spec, target_species, user_intent, noise_level=noise_level, mc_iterations=mc_iterations, pass_threshold=pass_threshold)
                        
                        st.session_state["last_oracle_feedback"] = result["feedback_string"]
                        st.session_state["last_oracle_pass"] = result["pass"]
                        
                        if result["pass"]:
                            st.success(result["feedback_string"])
                            st.write(f"**判定 Score (Pass Rate):** {result['score']:.2f}%")
                            if result.get("toxicity_failures", 0) > 0:
                                st.warning(f"注意：有 {result['toxicity_failures']} 組出現潛在毒性負荷。")
                        else:
                            st.error(result["feedback_string"])
                            st.write(f"**判定 Score (Pass Rate):** {result['score']:.2f}% (未達標或毒性過高)")
                            with st.expander("查看蒙地卡羅樣本錯誤分佈"):
                                for msg in result.get("mc_feedbacks", [])[:30]:
                                    st.write(msg)
                            
            # 處理失敗後的按鈕：因為 Streamlit 的渲染特性，獨立放在按鈕邏輯外或使用 session
            back_to_critic = False
            if st.session_state.get("last_oracle_feedback") and not st.session_state.get("last_oracle_pass"):
                back_to_critic = st.button("將此 ODE 失敗報告送回 Critic 智能體並重啟辯論", key="debate_oracle_reject")

            if compile_btn:
                if provider != "Local (Ollama)" and (not llm_api_key or not llm_api_key.strip()):
                    st.error("請先在左側輸入模型供應商的 API Key。")
                else:
                    # 使用者編輯過的 edited_spec 交給 compiler
                    if _compile_to_verilog(edited_spec):
                        st.session_state["debate_review"] = None
                        st.session_state["consolidated_design"] = None
                        st.session_state["last_oracle_feedback"] = None
                        st.success("已核准任務：HDL 生成作業已完成，請檢視下方匯出的硬體描述網表。")

        st.markdown("**（3）引入額外生物學約束**（輸入後按下提交）")
        extra_condition = st.text_area(
            "追加條件約束說明",
            height=100,
            placeholder="例：須避免高拷貝載體；或須符合某實驗室生物安全等級…",
            key=f"debate_extra_condition_{st.session_state['debate_extra_widget_id']}",
        )
        append_go = st.button("(3) 合併約束條件並重啟迭代", key="debate_append_rerun")

        if redo:
            if provider != "Local (Ollama)" and (not llm_api_key or not llm_api_key.strip()):
                st.error("請先在左側輸入模型供應商的 API Key。")
            else:
                st.session_state["consolidated_design"] = None
                with st.spinner("重新辯論中（三輪 Builder ↔ Critic）…"):
                    rerun_state = CircuitState(
                        user_intent=dr["effective_intent"],
                        host_organism=host_organism,
                        rag_context=dr.get("rag_context", ""),
                    )
                    ok, rerun_result_state = run_reflexion_workflow(
                        llm_api_key,
                        model_name,
                        rerun_state,
                        api_base=llm_api_base,
                        force_zero_shot=dr.get("force_zero_shot", False),
                        host_organism=host_organism
                    )
                    new_sess = [
                        {"builder": record.builder_output, "critic": record.critic_output}
                        for record in rerun_result_state.round_history
                    ]
                    err_msg = rerun_result_state.last_error
                if not ok:
                    st.error(err_msg or "執行迭代過程發生異常。")
                else:
                    dr["sessions"].append(new_sess)
                    st.success("已成功重新啟動並完成新一輪對抗迭代，完整事件日誌已更新於上方。")
                    st.rerun()

        elif append_go or back_to_critic:
            if provider != "Local (Ollama)" and (not llm_api_key or not llm_api_key.strip()):
                st.error("請先在左側輸入模型供應商的 API Key。")
            else:
                if back_to_critic:
                    actual_condition = st.session_state.get("last_oracle_feedback", "")
                else:
                    actual_condition = extra_condition.strip() if extra_condition else ""
                    
                if not actual_condition:
                    st.error("請先輸入要追加的條件內容或確保已有 Oracle 失敗報告。")
                else:
                    carry = format_sessions_for_llm(dr["sessions"])
                    prefix_str = "\n\n【Automated Oracle 反饋】\n" if back_to_critic else "\n\n【使用者追加條件】\n"
                    new_intent = (
                        dr["effective_intent"].rstrip()
                        + prefix_str
                        + actual_condition
                    )
                    with st.spinner("已帶入完整歷史與新條件，進行三輪再辯論…"):
                        appended_state = CircuitState(
                            user_intent=new_intent,
                            host_organism=host_organism,
                            rag_context=dr.get("rag_context", ""),
                            seed_debate_transcript=carry,
                        )
                        ok, appended_result_state = run_reflexion_workflow(
                            llm_api_key,
                            model_name,
                            appended_state,
                            api_base=llm_api_base,
                            force_zero_shot=dr.get("force_zero_shot", False),
                            host_organism=host_organism
                        )
                        new_sess = [
                            {"builder": record.builder_output, "critic": record.critic_output}
                            for record in appended_result_state.round_history
                        ]
                        err_msg = appended_result_state.last_error
                if not ok:
                    st.error(err_msg or "追加條件後的辯論失敗。")
                else:
                    dr["effective_intent"] = new_intent
                    dr["sessions"].append(new_sess)
                    st.session_state["debate_extra_widget_id"] = (
                        int(st.session_state["debate_extra_widget_id"]) + 1
                    )
                    st.session_state["consolidated_design"] = None
                    st.success("已併入追加條件並完成新一輪三辯論；上方紀錄含完整歷史。")
                    st.rerun()

    # 顯示最近一次成功生成的 Verilog 與下載按鈕（路線 A：手動下載 .v 供 Cello 使用）
    if st.session_state.get("last_verilog"):
        st.markdown("---")
        render_verilog_result(st.session_state["last_verilog"])
        
        st.markdown("---")
        st.subheader("動力學模擬 (ODE Simulation - `solve_ivp` with Radau)")
        st.info("可自行定義電路拓樸與外部動態訊號 (JSON 格式)，支援剛性方程的自動微分求解與非同步參數補全。")
        
        default_topology = {
            "species": ["Lacl", "TetR", "cI"],
            "interactions": [
                {"source": "Lacl", "target": "TetR", "type": "repression"},
                {"source": "TetR", "target": "cI", "type": "repression"},
                {"source": "cI", "target": "Lacl", "type": "repression"} 
            ],
            "inducers": {
                "IPTG": {"target": "Lacl"}
            }
        }
        default_stimulus = {
            "IPTG": {"type": "pulse", "start": 10000, "duration": 50000, "amp": 50000.0}
        }
        
        import json
        col_t, col_s = st.columns(2)
        with col_t:
            topology_str = st.text_area("Topology JSON", value=json.dumps(default_topology, indent=2), height=250)
        with col_s:
            stimulus_str = st.text_area("Stimulus Curves JSON", value=json.dumps(default_stimulus, indent=2), height=250)
        
        if st.button("執行 ODE 模擬 (非同步參數補全 + 剛性求解)"):
            try:
                topo_dict = json.loads(topology_str)
                stim_dict = json.loads(stimulus_str)
            except Exception as e:
                st.error(f"JSON 格式解析錯誤：{e}")
                st.stop()
                
            from tools.ode_simulator import run_ode_simulation, resolve_missing_parameters
            import asyncio
            
            with st.spinner(f"Data Miner 正在非同步獲取缺失的生化參數..."):
                missing_keys = ["kd", "protein_deg_rate", "transcription_rate", "mrna_deg_rate", "translation_rate"]
                
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                resolved_params = loop.run_until_complete(
                    resolve_missing_parameters(missing_keys, topo_dict.get("species", []), llm_api_key, model_name, llm_api_base, host_organism)
                )
                
            st.success("成功利用 Data Miner Agent 補全缺失的動力學參數：")
            st.json(resolved_params)
            
            from tools.ode_simulator import run_monte_carlo_ode_simulation
            with st.spinner("正在呼叫 scipy.integrate.solve_ivp 解算蒙地卡羅非線性模型 (50 Samples)..."):
                dfs = run_monte_carlo_ode_simulation(resolved_params, topology=topo_dict, stimulus_curves=stim_dict, num_samples=50)
                
            st.success("✅ 蒙地卡羅 ODE 陣列積分完成！")
            
            st.markdown("### 動態時序軌跡與分佈通道 (Monte Carlo Trajectories)")
            use_log_scale = st.toggle("啟用對數座標 (Log Scale)", value=True)
            
            import plotly.graph_objects as go
            import numpy as np
            
            fig = go.Figure()
            if dfs and not dfs[0].empty:
                time_arr = dfs[0]["Time"].values
                species_cols = [c for c in dfs[0].columns if c != "Time"]
                
                colors = ['#636EFA', '#EF553B', '#00CC96', '#AB63FA', '#FFA15A', '#19D3F3', '#FF6692', '#B6E880', '#FF97FF', '#FECB52']
                
                # 平均值軌跡矩陣
                sum_dfs = {col: np.zeros(len(time_arr)) for col in species_cols}
                
                for df_run in dfs:
                    if df_run.empty:
                        continue
                    df_safe = df_run.copy()
                    for c in species_cols:
                        # 箝位防止 log(0)
                        df_safe[c] = np.maximum(df_safe[c], 1e-3)
                        if len(df_safe[c]) == len(time_arr):
                            sum_dfs[c] += df_safe[c].values
                            
                        c_idx = species_cols.index(c) % len(colors)
                        fig.add_trace(go.Scatter(
                            x=time_arr[:len(df_safe[c])], y=df_safe[c], mode='lines', 
                            line=dict(color=colors[c_idx], width=1), 
                            opacity=0.1, showlegend=False, hoverinfo='skip'
                        ))
                
                # 繪製粗體平均線
                active_dfs_count = len([d for d in dfs if not d.empty])
                if active_dfs_count > 0:
                    for c in species_cols:
                        mean_val = sum_dfs[c] / active_dfs_count
                        c_idx = species_cols.index(c) % len(colors)
                        fig.add_trace(go.Scatter(
                            x=time_arr, y=mean_val, mode='lines', 
                            name=f"{c} (Mean)",
                            line=dict(color=colors[c_idx], width=3)
                        ))
                    
                if use_log_scale:
                    fig.update_yaxes(type="log", title_text="Concentration (nM)")
                else:
                    fig.update_yaxes(title_text="Concentration (nM)")
                    
                fig.update_xaxes(title_text="Time (s)")
                
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.error("所有模擬皆失敗。請檢查參數。")


if __name__ == "__main__":
    main()
