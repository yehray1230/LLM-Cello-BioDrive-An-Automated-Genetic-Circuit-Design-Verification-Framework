from __future__ import annotations

from typing import Any, Callable

import pytest

from benchmark_suite.benchmark_controller import _clamp_score
from benchmark_suite.cello_constraint_evaluator import _clamp01 as cello_clamp01
from benchmark_suite.functional_scorer import _clamp01 as functional_clamp01
from benchmark_suite.semantic_evaluator import _clamp01 as semantic_clamp01
from benchmark_suite.score_values import clamp01
from benchmark_suite.static_plausibility_evaluator import (
    _clamp01 as static_plausibility_clamp01,
)
from benchmark_suite.temporal_scorer import _clamp01 as temporal_clamp01


ClampFunction = Callable[[Any], float]

CLAMP_FUNCTIONS: tuple[ClampFunction, ...] = (
    clamp01,
    _clamp_score,
    cello_clamp01,
    functional_clamp01,
    semantic_clamp01,
    static_plausibility_clamp01,
    temporal_clamp01,
)


def test_scorers_reference_the_canonical_score_clamp() -> None:
    assert _clamp_score is clamp01
    assert cello_clamp01 is clamp01
    assert functional_clamp01 is clamp01
    assert semantic_clamp01 is clamp01
    assert static_plausibility_clamp01 is clamp01
    assert temporal_clamp01 is clamp01


@pytest.mark.parametrize("clamp", CLAMP_FUNCTIONS)
@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        (-2.0, 0.0),
        (0.0, 0.0),
        (0.25, 0.25),
        (1.0, 1.0),
        (2.0, 1.0),
        (" 0.5 ", 0.5),
        (False, 0.0),
        (True, 1.0),
    ),
)
def test_score_clamps_share_finite_conversion_contract(
    clamp: ClampFunction,
    raw: Any,
    expected: float,
) -> None:
    result = clamp(raw)
    assert result == expected
    assert isinstance(result, float)


@pytest.mark.parametrize("clamp", CLAMP_FUNCTIONS)
@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        (float("-inf"), 0.0),
        (float("inf"), 1.0),
        (float("nan"), 1.0),
    ),
)
def test_score_clamps_share_current_non_finite_contract(
    clamp: ClampFunction,
    raw: float,
    expected: float,
) -> None:
    assert clamp(raw) == expected


@pytest.mark.parametrize("clamp", CLAMP_FUNCTIONS)
@pytest.mark.parametrize(
    ("raw", "error_type"),
    (
        (None, TypeError),
        (object(), TypeError),
        ("invalid", ValueError),
    ),
)
def test_score_clamps_propagate_conversion_errors(
    clamp: ClampFunction,
    raw: Any,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        clamp(raw)
