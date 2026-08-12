from __future__ import annotations
import logging
import re
import litellm
from schemas.state import DesignState

def _validate_verilog_ast(code: str) -> tuple[bool, str]:
    if "module " not in code or "endmodule" not in code:
        return False, "missing `module` or `endmodule`."
    if "input " not in code:
        return False, "missing input declaration."
    if "output " not in code:
        return False, "missing output declaration."
    has_logic = any(keyword in code for keyword in ["and(", "or(", "not(", "nor(", "nand(", "xor(", "xnor(", "assign "])
    if not has_logic:
        return False, "missing combinational logic (`assign` or primitive gates)."
    if re.search(r"\balways\b", code):
        return False, "`always` blocks are not Cello-compatible."
    if re.search(r"\breg\b", code):
        return False, "`reg` is not allowed; use `wire` and combinational assignments."
    if "#" in code:
        return False, "delay syntax (`#`) is not allowed."
    return True, ""

def _translate_single_proposal(
    proposal: str,
    api_key: str | None,
    model_name: str,
    api_base: str | None = None,
    skill_context: str = "",
    feedback: str = "",
    temperature: float = 0.1,
    is_exploitation: bool = False,
    max_retries: int = 3,
    attempt_budget=None,
) -> str:
    system_prompt = """You are an expert biological circuit compiler for Cello CAD.

Translate one structured logic proposal into raw, valid, Cello-compatible Verilog.

Rules:
- Output only Verilog code. No Markdown fences.
- Use one module with explicit input and output declarations.
- Use only combinational logic.
- Allowed constructs: primitive gates (`and`, `or`, `not`, `nand`, `nor`, `xor`, `xnor`), `wire`, and continuous `assign`.
- Do not use `always`, `reg`, clocks, latches, memories, temporal delays, or `#`.
- If the proposal requests preserving a motif, use explicit wires and primitive gates instead of simplifying it away.
"""
    if is_exploitation:
        system_prompt += "\nMODE: EXPLOITATION. Do NOT change the logical architecture. Only modify part assignments or constraints to improve scoring based on the feedback.\n"

    if skill_context:
        system_prompt += (
            f"\n=== Logic Design Skill Context ===\n"
            f"{skill_context}\n"
        )
        
    system_prompt += (
        "\n若歷史經驗或 Critic 的最新反饋中提及特定元件（如某個 Promoter 或 RBS）具有高代謝負荷、毒性漏電等物理問題，"
        "你必須在生成的 Verilog 代碼中，利用 Cello 的約束語法 (如 `// cello_constraint` 或元件指派) 強制避開或替換該實體元件，"
        "且不得隨意修改原本正確的邏輯結構。\n"
    )

    if feedback:
        system_prompt += f"\nCritic feedback to address:\n{feedback}\n"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Translate this proposal to Cello-compatible Verilog:\n{proposal}"},
    ]

    max_retries = max(1, int(max_retries))
    for attempt in range(max_retries):
        try:
            if attempt_budget is not None:
                attempt_budget.consume_provider("translator")
            response = litellm.completion(
                model=model_name,
                messages=messages,
                temperature=temperature,
                api_key=api_key.strip() if api_key and api_key.strip() else None,
                api_base=api_base.strip() if api_base and api_base.strip() else None,
            )
            raw_output = response.choices[0].message["content"]
            match = re.search(r"(?s)module\s+\w+\s*\(.*?\);.*?endmodule", raw_output, re.MULTILINE)
            if not match:
                error = "no complete Verilog module was found in the response."
                if attempt < max_retries - 1:
                    messages.append({"role": "assistant", "content": raw_output})
                    messages.append({"role": "user", "content": f"{error} Return only one complete module."})
                    continue
                return f"ERROR: {error}"

            extracted_code = match.group(0).strip()
            is_valid, validation_error = _validate_verilog_ast(extracted_code)
            if not is_valid:
                if attempt < max_retries - 1:
                    messages.append({"role": "assistant", "content": extracted_code})
                    messages.append({"role": "user", "content": f"Validation failed: {validation_error} Fix the module."})
                    continue
                return f"ERROR: generated Verilog is not Cello-compatible: {validation_error}"

            return extracted_code

        except Exception as exc:
            logging.error("Translation error: %s", exc)
            if attempt == max_retries - 1:
                return f"ERROR: translation failed: {exc}"
    return "ERROR: translation failed after retries."

def call_translator(
    state: DesignState,
    api_key: str | None,
    model_name: str,
    api_base: str | None = None,
    temperature: float = 0.1,
    max_retries: int = 3,
    attempt_budget=None,
    **kwargs,
) -> DesignState:
    state.verilog_codes = []
    skill_context = getattr(state, "skill_context", "") or state.rag_context or ""
    
    node = None
    if state.current_node_id and state.current_node_id in state.tree_nodes:
        node = state.tree_nodes[state.current_node_id]
        
    feedback = ""
    if node and node.error_type == "PART_ERROR" and node.critic_feedbacks:
        feedback = node.critic_feedbacks[-1]
    elif not node and state.error_type == "PART_ERROR" and state.critic_feedbacks:
        feedback = state.critic_feedbacks[-1]
        
    is_exploitation = False
    if node and node.search_mode == "Exploitation":
        is_exploitation = True
        
    proposals = []
    if node:
        proposals = node.logic_proposals or ([node.current_topology] if node.current_topology else [])
    if not proposals:
        proposals = state.logic_proposals or ([state.current_topology] if state.current_topology else [])
        
    if not proposals:
        state.last_error = "ERROR: Translator received no logic proposals."
        return state

    for proposal in proposals:
        verilog_result = _translate_single_proposal(
            proposal,
            api_key,
            model_name,
            api_base,
            skill_context=skill_context,
            feedback=feedback,
            temperature=temperature,
            is_exploitation=is_exploitation,
            max_retries=max_retries,
            attempt_budget=attempt_budget,
        )
        state.verilog_codes.append(verilog_result)
        if node:
            node.verilog_codes.append(verilog_result)

    if all(code.startswith("ERROR:") for code in state.verilog_codes):
        state.last_error = "ERROR: all Verilog translations failed."
    else:
        state.last_error = None

    return state
