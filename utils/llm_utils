from __future__ import annotations

from typing import Any, Callable

import litellm
import litellm.exceptions


ToolExecutor = Callable[..., str]


def call_llm(
    api_key: str | None,
    model_name: str,
    system_prompt: str,
    user_content: str,
    api_base: str | None = None,
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
            temperature=0.3,
            api_key=api_key.strip() if api_key and api_key.strip() else None,
            api_base=api_base.strip() if api_base and api_base.strip() else None,
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
    """
    Shared LiteLLM tool-calling loop used by multiple agents.

    Returns:
    - ok: bool
    - final_content: str
    - messages: list[dict[str, Any]]
    - tool_outputs: list[dict[str, Any]]
    - error: str | None
    """
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
            )
        except litellm.exceptions.AuthenticationError:
            return {
                "ok": False,
                "final_content": "",
                "messages": messages,
                "tool_outputs": collected_tool_outputs,
                "error": f"ERROR: {error_prefix} authentication failed.",
            }
        except litellm.exceptions.RateLimitError:
            return {
                "ok": False,
                "final_content": "",
                "messages": messages,
                "tool_outputs": collected_tool_outputs,
                "error": f"ERROR: {error_prefix} rate limited.",
            }
        except litellm.exceptions.BadRequestError as exc:
            return {
                "ok": False,
                "final_content": "",
                "messages": messages,
                "tool_outputs": collected_tool_outputs,
                "error": f"ERROR: {error_prefix} bad request: {exc}",
            }
        except litellm.exceptions.APIError as exc:
            return {
                "ok": False,
                "final_content": "",
                "messages": messages,
                "tool_outputs": collected_tool_outputs,
                "error": f"ERROR: {error_prefix} API error: {exc}",
            }
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
