from __future__ import annotations

from app import _ode_trace_rows, _tool_warning_message, _valid_ode_trace
from mcp_server.ode_explainer import explain_ode_topology
from tools.tool_adapters import normalize_tool_warning


def test_valid_ode_trace_requires_time_and_output_series() -> None:
    assert _valid_ode_trace({"time": [0.0, 1.0], "output_protein": [0.0, 2.0]}) is True
    assert _valid_ode_trace({"time": [0.0], "output_protein": []}) is False
    assert _valid_ode_trace({}) is False
    assert _valid_ode_trace(
        {"time": [0.0, 1.0], "output_protein": [0.0, "invalid"]}
    ) is False
    assert _valid_ode_trace(
        {"time": [1.0, 0.0], "output_protein": [0.0, 2.0]}
    ) is False


def test_ode_trace_rows_aligns_available_series() -> None:
    rows = _ode_trace_rows(
        {
            "time": [0.0, 1.0],
            "output_protein": [0.0, 10.0],
            "total_mrna": [1.0, 2.0],
            "rnap_occupancy": [0.2, 0.4],
        }
    )

    assert rows == [
        {"time": 0.0, "output_protein": 0.0, "total_mrna": 1.0, "rnap_occupancy": 0.2},
        {"time": 1.0, "output_protein": 10.0, "total_mrna": 2.0, "rnap_occupancy": 0.4},
    ]


def test_ode_trace_rows_omit_misaligned_or_non_finite_optional_series() -> None:
    rows = _ode_trace_rows(
        {
            "time": [0.0, 1.0],
            "output_protein": [0.0, 10.0],
            "total_mrna": [1.0],
            "rnap_occupancy": [0.2, float("nan")],
        }
    )

    assert rows == [
        {"time": 0.0, "output_protein": 0.0},
        {"time": 1.0, "output_protein": 10.0},
    ]


def test_tool_warning_message_supports_dataclass_and_dictionary() -> None:
    warning = normalize_tool_warning("TOOL_FAILED", "Simulation stopped.")

    assert _tool_warning_message(warning) == "Simulation stopped."
    assert _tool_warning_message({"message": "Dictionary warning."}) == "Dictionary warning."


def test_ode_explainer_extracts_key_readouts_and_warnings() -> None:
    explanation = explain_ode_topology(
        {
            "ode_status": "simulated",
            "ode_trace": {
                "time": [0, 10, 20, 30, 40],
                "output_protein": [1, 3, 7, 9, 9.05],
                "total_mrna": [0, 1, 2, 2, 2],
                "total_protein": [1, 4, 8, 11, 12],
                "rnap_occupancy": [0.1, 0.2, 0.3, 0.4, 0.4],
                "ribosome_occupancy": [0.2, 0.3, 0.5, 0.65, 0.66],
            },
            "monte_carlo_runs": 1,
        }
    )

    assert explanation["status"] == "simulated"
    assert explanation["key_readouts"]["peak_output_protein"] == 9.05
    assert explanation["key_readouts"]["time_to_peak"] == 40
    assert explanation["burden_readouts"]["burden_risk_level"] == "moderate"
    assert explanation["stability_readouts"]["uncertainty_evaluated"] is False
    assert any("OFF-state" in warning for warning in explanation["coverage_warnings"])
