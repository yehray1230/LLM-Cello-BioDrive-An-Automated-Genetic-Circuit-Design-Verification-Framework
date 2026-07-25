from __future__ import annotations

import math
from typing import Any, Callable

import pytest

from benchmark_suite.benchmark_controller import (
    _candidate_bool as controller_candidate_bool,
)
from benchmark_suite.benchmark_controller import (
    _candidate_float as controller_candidate_float,
)
from benchmark_suite.benchmark_controller import (
    _candidate_int as controller_candidate_int,
)
from benchmark_suite.cello_constraint_evaluator import _coerce_bool as cello_coerce_bool
from benchmark_suite.candidate_values import candidate_float
from benchmark_suite.candidate_values import candidate_int
from benchmark_suite.candidate_values import maybe_float
from benchmark_suite.functional_scorer import _candidate_float as functional_candidate_float
from benchmark_suite.functional_scorer import _maybe_float as functional_maybe_float
from benchmark_suite.kinetic_scorer import _candidate_float as kinetic_candidate_float
from benchmark_suite.kinetic_scorer import _candidate_int as kinetic_candidate_int
from benchmark_suite.temporal_scorer import _candidate_float as temporal_candidate_float
from benchmark_suite.temporal_scorer import _maybe_float as temporal_maybe_float
from benchmark_suite.static_plausibility_evaluator import (
    _maybe_float as static_maybe_float,
)


FloatReader = Callable[[dict[str, Any], str, float], float]
IntReader = Callable[[dict[str, Any], str, int], int]

FLOAT_READERS: tuple[FloatReader, ...] = (
    candidate_float,
    controller_candidate_float,
    kinetic_candidate_float,
    functional_candidate_float,
    temporal_candidate_float,
)
INT_READERS: tuple[IntReader, ...] = (
    candidate_int,
    controller_candidate_int,
    kinetic_candidate_int,
)


def test_scorers_reference_the_canonical_numeric_helpers() -> None:
    assert controller_candidate_float is candidate_float
    assert kinetic_candidate_float is candidate_float
    assert functional_candidate_float is candidate_float
    assert temporal_candidate_float is candidate_float
    assert controller_candidate_int is candidate_int
    assert kinetic_candidate_int is candidate_int
    assert functional_maybe_float is maybe_float
    assert temporal_maybe_float is maybe_float
    assert static_maybe_float is maybe_float


@pytest.mark.parametrize("reader", FLOAT_READERS)
@pytest.mark.parametrize(
    ("candidate", "expected"),
    (
        ({}, 7.5),
        ({"value": None}, 7.5),
        ({"value": " 2.5 "}, 2.5),
        ({"value": 3}, 3.0),
        ({"value": True}, 1.0),
        ({"value": "invalid"}, 7.5),
        ({"value": []}, 7.5),
    ),
)
def test_candidate_float_readers_share_conversion_contract(
    reader: FloatReader,
    candidate: dict[str, Any],
    expected: float,
) -> None:
    assert reader(candidate, "value", 7.5) == expected


@pytest.mark.parametrize("reader", FLOAT_READERS)
@pytest.mark.parametrize(("raw", "predicate"), (("nan", math.isnan), ("inf", math.isinf)))
def test_candidate_float_readers_preserve_non_finite_values(
    reader: FloatReader,
    raw: str,
    predicate: Callable[[float], bool],
) -> None:
    assert predicate(reader({"value": raw}, "value", 7.5))


@pytest.mark.parametrize("reader", INT_READERS)
@pytest.mark.parametrize(
    ("candidate", "expected"),
    (
        ({}, 7),
        ({"value": None}, 7),
        ({"value": " 2 "}, 2),
        ({"value": 3.9}, 3),
        ({"value": True}, 1),
        ({"value": "invalid"}, 7),
        ({"value": float("nan")}, 7),
    ),
)
def test_candidate_int_readers_share_conversion_contract(
    reader: IntReader,
    candidate: dict[str, Any],
    expected: int,
) -> None:
    assert reader(candidate, "value", 7) == expected


@pytest.mark.parametrize("reader", INT_READERS)
def test_candidate_int_readers_currently_propagate_infinite_overflow(
    reader: IntReader,
) -> None:
    with pytest.raises(OverflowError):
        reader({"value": float("inf")}, "value", 7)


@pytest.mark.parametrize(
    ("raw", "default", "expected"),
    (
        (None, True, True),
        (True, False, True),
        (False, True, False),
        (" yes ", False, True),
        ("N", True, False),
        ("unknown", True, True),
        (0, True, False),
        (2, False, True),
    ),
)
def test_general_and_cello_boolean_contracts_share_basic_tokens(
    raw: Any,
    default: bool,
    expected: bool,
) -> None:
    assert controller_candidate_bool({"value": raw}, "value", default) is expected
    assert cello_coerce_bool(raw, default) is expected


@pytest.mark.parametrize("raw", ("mapped", "success", "successful"))
def test_cello_boolean_contract_adds_external_success_tokens(raw: str) -> None:
    assert cello_coerce_bool(raw, False) is True
    assert controller_candidate_bool({"value": raw}, "value", False) is False


@pytest.mark.parametrize("raw", ("failed", "unmapped"))
def test_cello_boolean_contract_adds_external_failure_tokens(raw: str) -> None:
    assert cello_coerce_bool(raw, True) is False
    assert controller_candidate_bool({"value": raw}, "value", True) is True
