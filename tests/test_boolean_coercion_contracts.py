from __future__ import annotations

from typing import Any, Callable

import pytest

from benchmark_suite.benchmark_controller import _candidate_bool
from benchmark_suite.cello_constraint_evaluator import _coerce_bool as cello_bool
from benchmark_suite.functional_scorer import _as_bool as truth_table_bool
from catalog.agent_catalog import AgentCatalogError
from catalog.agent_catalog import _as_bool as agent_catalog_bool
from catalog.workflow_kit_catalog import WorkflowKitCatalogError
from catalog.workflow_kit_catalog import _as_bool as workflow_catalog_bool
from schemas.state import _coerce_bool as state_bool
from tools.ode_simulator import _coerce_bool as ode_bool
from utils.boolean_values import defaulted_bool


DefaultedReader = Callable[[Any, bool], bool]


def _candidate_value_bool(value: Any, default: bool) -> bool:
    return _candidate_bool({"value": value}, "value", default)


DEFAULTED_READERS: tuple[DefaultedReader, ...] = (
    defaulted_bool,
    _candidate_value_bool,
    state_bool,
    ode_bool,
)


@pytest.mark.parametrize("reader", DEFAULTED_READERS)
@pytest.mark.parametrize(
    ("raw", "default", "expected"),
    (
        (None, False, False),
        (None, True, True),
        (True, False, True),
        (False, True, False),
        ("  TRUE  ", False, True),
        ("yes", False, True),
        ("Y", False, True),
        ("1", False, True),
        (" false ", True, False),
        ("NO", True, False),
        ("n", True, False),
        ("0", True, False),
        ("unknown", False, False),
        ("unknown", True, True),
        ("", True, True),
        (0, True, False),
        (2, False, True),
        ([], True, False),
        ([0], False, True),
    ),
)
def test_defaulted_boolean_contract(
    reader: DefaultedReader,
    raw: Any,
    default: bool,
    expected: bool,
) -> None:
    assert reader(raw, default) is expected


@pytest.mark.parametrize("raw", ("mapped", "success", "successful"))
def test_cello_contract_adds_success_status_tokens(raw: str) -> None:
    assert cello_bool(raw, False) is True


@pytest.mark.parametrize("raw", ("failed", "unmapped"))
def test_cello_contract_adds_failure_status_tokens(raw: str) -> None:
    assert cello_bool(raw, True) is False


@pytest.mark.parametrize("raw", ("mapping_failed", "not_mapped", "unknown", ""))
def test_cello_unknown_status_tokens_use_default(raw: str) -> None:
    assert cello_bool(raw, False) is False
    assert cello_bool(raw, True) is True


@pytest.mark.parametrize("raw", ("1", "true", "YES", " high ", "on"))
def test_truth_table_contract_recognizes_signal_true_tokens(raw: str) -> None:
    assert truth_table_bool(raw) is True


@pytest.mark.parametrize(
    "raw",
    ("0", "false", "no", "low", "off", "unknown", "", "mapped"),
)
def test_truth_table_contract_treats_other_strings_as_false(raw: str) -> None:
    assert truth_table_bool(raw) is False


@pytest.mark.parametrize("reader", (agent_catalog_bool, workflow_catalog_bool))
@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        (True, True),
        (False, False),
        ("true", True),
        (" YES ", True),
        ("1", True),
        ("false", False),
        ("NO", False),
        ("0", False),
    ),
)
def test_catalog_contract_accepts_only_explicit_boolean_tokens(
    reader: Callable[[Any], bool],
    raw: Any,
    expected: bool,
) -> None:
    assert reader(raw) is expected


@pytest.mark.parametrize(
    ("reader", "error_type"),
    (
        (agent_catalog_bool, AgentCatalogError),
        (workflow_catalog_bool, WorkflowKitCatalogError),
    ),
)
@pytest.mark.parametrize("raw", (None, 1, 0, "y", "n", "unknown", ""))
def test_catalog_contract_rejects_ambiguous_values_with_domain_error(
    reader: Callable[[Any], bool],
    error_type: type[ValueError],
    raw: Any,
) -> None:
    with pytest.raises(error_type, match="Expected boolean value"):
        reader(raw)
