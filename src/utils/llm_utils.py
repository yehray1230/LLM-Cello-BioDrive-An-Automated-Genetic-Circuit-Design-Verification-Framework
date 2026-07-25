from __future__ import annotations

import shutil
from typing import Any, Callable

import litellm
import litellm.exceptions
from litellm.caching import Cache

litellm.cache = Cache(type="disk", disk_cache_dir="./.llm_cache")
ENABLE_LLM_CACHE = True

ToolExecutor = Callable[..., str]


def call_llm(
    api_key: str | None,
    model_name: str,
    system_prompt: str,
    user_content: str,
    api_base: str | None = None,
    temperature: float = 0.3,
) -> str:
    """
    Simple one-shot LiteLLM call.
    """
    try:
        response = litellm.completion(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=temperature,
            api_key=api_key.strip() if api_key and api_key.strip() else None,
            api_base=api_base.strip() if api_base and api_base.strip() else None,
            caching=ENABLE_LLM_CACHE,
        )
        return response.choices[0].message["content"].strip()
    except litellm.exceptions.AuthenticationError:
        return "ERROR: authentication failed."
    except litellm.exceptions.RateLimitError:
        return "ERROR: rate limited."
    except litellm.exceptions.BadRequestError as exc:
        return f"ERROR: bad request: {exc}"
    except litellm.exceptions.APIError as exc:
        return f"ERROR: API error: {exc}"
    except Exception as exc:
        return f"ERROR: unexpected failure: {exc}"


def extract_tool_calls(message) -> list[dict[str, Any]]:
    tool_calls = getattr(message, "tool_calls", None) or []
    normalized: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        normalized.append(
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments,
                },
            }
        )
    return normalized


def run_llm_with_tools(
    *,
    api_key: str | None,
    model_name: str,
    system_prompt: str,
    user_content: str,
    tools: list[dict[str, Any]],
    tool_executors: dict[str, ToolExecutor],
    api_base: str | None = None,
    temperature: float = 0.3,
    tool_choice: str = "auto",
    max_tool_rounds: int = 3,
    tool_executor_kwargs: dict[str, dict[str, Any]] | None = None,
    error_prefix: str = "LLM",
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    collected_tool_outputs: list[dict[str, Any]] = []
    executor_kwargs_map = tool_executor_kwargs or {}

    for _ in range(max_tool_rounds + 1):
        try:
            response = litellm.completion(
                model=model_name,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
                api_key=api_key.strip() if api_key and api_key.strip() else None,
                api_base=api_base.strip() if api_base and api_base.strip() else None,
                caching=ENABLE_LLM_CACHE,
            )
        except Exception as exc:
            return {
                "ok": False,
                "final_content": "",
                "messages": messages,
                "tool_outputs": collected_tool_outputs,
                "error": f"ERROR: {error_prefix} unexpected failure: {exc}",
            }

        message = response.choices[0].message
        tool_calls = extract_tool_calls(message)
        content = getattr(message, "content", None) or ""

        if not tool_calls:
            return {
                "ok": True,
                "final_content": content.strip(),
                "messages": messages,
                "tool_outputs": collected_tool_outputs,
                "error": None,
            }

        messages.append(
            {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            }
        )

        for tool_call in tool_calls:
            tool_name = tool_call["function"]["name"]
            tool_args = tool_call["function"]["arguments"]
            executor = tool_executors.get(tool_name)
            if executor is None:
                tool_result = f"TOOL_ERROR: unknown tool `{tool_name}`"
            else:
                try:
                    tool_result = executor(
                        tool_args,
                        **executor_kwargs_map.get(tool_name, {}),
                    )
                except Exception as exc:
                    tool_result = f"TOOL_ERROR: {tool_name} execution crashed: {exc}"

            collected_tool_outputs.append(
                {
                    "tool_name": tool_name,
                    "tool_call_id": tool_call["id"],
                    "content": tool_result,
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "name": tool_name,
                    "content": tool_result,
                }
            )

    return {
        "ok": False,
        "final_content": "",
        "messages": messages,
        "tool_outputs": collected_tool_outputs,
        "error": f"ERROR: {error_prefix} exceeded max tool-calling rounds.",
    }

def clear_llm_cache():
    shutil.rmtree("./.llm_cache", ignore_errors=True)


def resolve_agent_model(role: str, configured_model: str | None) -> str:
    """
    Resolves the model name for a specific agent role based on the configured model.
    If the model name starts with "routed:", it will route cheap roles (builder, translator, pm)
    to a cheaper model, while routing critical roles (critic) to the base strong model.

    Roles:
      - "critic": The strong review and critique role.
      - "builder": The logic design role.
      - "translator": The verilog compiler role.
      - "pm": The spec gathering and translation role.
    """
    if not configured_model:
        return "gemini/gemini-3.5-flash"  # Default fallback

    if not configured_model.startswith("routed:"):
        return configured_model

    base_model = configured_model[len("routed:"):]

    if role == "critic":
        return base_model

    # Cheap role: map to cheap counterpart based on base model provider
    if "gemini" in base_model.lower():
        return "gemini/gemini-2.5-flash"
    elif "gpt" in base_model.lower() or "openai" in base_model.lower():
        return "gpt-4o-mini"
    elif "claude" in base_model.lower() or "anthropic" in base_model.lower():
        return "claude-3-5-haiku-20241022"
    else:
        # Fallback to the same base model if provider is unrecognized
        return base_model
