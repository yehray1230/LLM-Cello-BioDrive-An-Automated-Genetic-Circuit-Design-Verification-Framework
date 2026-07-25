from __future__ import annotations

from utils.llm_utils import resolve_agent_model

def test_resolve_agent_model_passthrough():
    # Standard non-routed config passes through directly
    model = "gemini/gemini-1.5-pro"
    assert resolve_agent_model("critic", model) == model
    assert resolve_agent_model("builder", model) == model
    assert resolve_agent_model("translator", model) == model
    assert resolve_agent_model("pm", model) == model

def test_resolve_agent_model_gemini_routing():
    # Routed gemini maps critic to pro/base and others to 3.5-flash
    routed_model = "routed:gemini/gemini-1.5-pro"
    assert resolve_agent_model("critic", routed_model) == "gemini/gemini-1.5-pro"
    assert resolve_agent_model("builder", routed_model) == "gemini/gemini-2.5-flash"
    assert resolve_agent_model("translator", routed_model) == "gemini/gemini-2.5-flash"
    assert resolve_agent_model("pm", routed_model) == "gemini/gemini-2.5-flash"

def test_resolve_agent_model_openai_routing():
    # Routed openai maps critic to base and others to gpt-4o-mini
    routed_model = "routed:gpt-4o"
    assert resolve_agent_model("critic", routed_model) == "gpt-4o"
    assert resolve_agent_model("builder", routed_model) == "gpt-4o-mini"
    assert resolve_agent_model("translator", routed_model) == "gpt-4o-mini"
    assert resolve_agent_model("pm", routed_model) == "gpt-4o-mini"

def test_resolve_agent_model_fallback():
    # None resolves to gemini-3.5-flash
    assert resolve_agent_model("critic", None) == "gemini/gemini-3.5-flash"

    # Unrecognized model uses base_model fallback for all roles
    routed_model = "routed:custom-llama-3"
    assert resolve_agent_model("critic", routed_model) == "custom-llama-3"
    assert resolve_agent_model("builder", routed_model) == "custom-llama-3"
