from __future__ import annotations

from web.routes import _ode_trace_view


def test_web_ode_trace_view_rejects_malformed_plot_data() -> None:
    for trace in (
        {"time": [0.0, 1.0], "output_protein": [0.0, "invalid"]},
        {"time": [0.0, 1.0], "output_protein": [0.0, float("inf")]},
        {"time": [1.0, 0.0], "output_protein": [0.0, 1.0]},
    ):
        view = _ode_trace_view(
            {
                "summary": {
                    "best_topology": {
                        "ode_status": "simulated",
                        "ode_trace": trace,
                    }
                }
            }
        )

        assert view["series"] == []
        assert view["message"] == "No saved ODE time series was found for this run."


def test_web_ode_trace_view_preserves_valid_series() -> None:
    view = _ode_trace_view(
        {
            "summary": {
                "best_topology": {
                    "ode_status": "simulated",
                    "ode_trace": {
                        "time": [0.0, 1.0],
                        "output_protein": [0.0, 2.0],
                        "total_mrna": [1.0],
                        "rnap_occupancy": [0.1, float("nan")],
                    },
                }
            }
        }
    )

    assert view["series"][0]["key"] == "output_protein"
    assert [item["key"] for item in view["series"]] == ["output_protein"]
    assert view["message"] == ""
