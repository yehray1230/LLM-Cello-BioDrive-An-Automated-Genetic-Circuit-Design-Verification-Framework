from __future__ import annotations

import math
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

import pytest

from application.case01_evidence import _optional_text as evidence_optional_text
from application.services import _optional_string as service_optional_string
from benchmark_suite.candidate_values import maybe_float
from benchmark_suite.readiness_evaluator import (
    _optional_string as readiness_optional_string,
)
from schemas.design_ir import _optional_float as design_ir_optional_float
from schemas.design_ir import _optional_string as design_ir_optional_string
from schemas.design_ir_v2 import _optional_string as design_ir_v2_optional_string
from schemas.design_migrations import _optional_float as migration_optional_float
from schemas.design_migrations import _optional_string as migration_optional_string
from schemas.host_optimization import _optional_float as host_optional_float
from schemas.host_optimization import _optional_string as host_optional_string
from schemas.import_draft import _optional_text as import_draft_optional_text
from schemas.resource_calibration import _optional_float as resource_optional_float
from schemas.run_manifest import _optional_string as manifest_optional_string
from tools.cello_artifact_parser import _optional_float as cello_optional_float
from tools.part_library import _optional_float as part_optional_float
from tools.part_library import _optional_string as part_optional_string
from utils.scalar_values import optional_float
from utils.scalar_values import optional_trimmed_text


OptionalString = Callable[[Any], str | None]
OptionalFloat = Callable[[Any], float | None]

TRIMMED_OPTIONAL_STRINGS: tuple[OptionalString, ...] = (
    evidence_optional_text,
    service_optional_string,
    readiness_optional_string,
    design_ir_optional_string,
    design_ir_v2_optional_string,
    migration_optional_string,
    host_optional_string,
    import_draft_optional_text,
    part_optional_string,
)

PERMISSIVE_OPTIONAL_FLOATS: tuple[OptionalFloat, ...] = (
    maybe_float,
    design_ir_optional_float,
    migration_optional_float,
    host_optional_float,
    cello_optional_float,
    part_optional_float,
)


def test_approved_helpers_reference_neutral_scalar_values() -> None:
    assert evidence_optional_text is optional_trimmed_text
    assert service_optional_string is optional_trimmed_text
    assert readiness_optional_string is optional_trimmed_text
    assert design_ir_optional_string is optional_trimmed_text
    assert design_ir_v2_optional_string is optional_trimmed_text
    assert migration_optional_string is optional_trimmed_text
    assert host_optional_string is optional_trimmed_text
    assert import_draft_optional_text is optional_trimmed_text
    assert part_optional_string is optional_trimmed_text

    assert maybe_float is optional_float
    assert design_ir_optional_float is optional_float
    assert migration_optional_float is optional_float
    assert host_optional_float is optional_float
    assert cello_optional_float is optional_float
    assert part_optional_float is optional_float


def test_keep_separate_helpers_do_not_alias_permissive_contracts() -> None:
    assert manifest_optional_string is not optional_trimmed_text
    assert resource_optional_float is not optional_float


def test_scalar_values_imports_in_clean_python_process() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    code = "\n".join(
        (
            "from application.case01_evidence import _optional_text as application_text",
            "from benchmark_suite.candidate_values import maybe_float as benchmark_float",
            "from schemas.design_ir import _optional_string as schema_text",
            "from tools.part_library import _optional_float as tool_float",
            "from utils.scalar_values import optional_float, optional_trimmed_text",
            "assert application_text is optional_trimmed_text",
            "assert schema_text is optional_trimmed_text",
            "assert benchmark_float is optional_float",
            "assert tool_float is optional_float",
        )
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
        # A cold Windows/OneDrive import loads the application package and
        # optional scientific dependencies; the observed clean-process import
        # is about 26 seconds on the acceptance host.
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("normalizer", TRIMMED_OPTIONAL_STRINGS)
@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        (None, None),
        ("", None),
        (" \t ", None),
        ("  value  ", "value"),
        (0, "0"),
        (False, "False"),
    ),
)
def test_trimmed_optional_strings_share_contract(
    normalizer: OptionalString,
    raw: Any,
    expected: str | None,
) -> None:
    assert normalizer(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        (None, None),
        ("", ""),
        (" \t ", " \t "),
        ("  value  ", "  value  "),
        (0, "0"),
    ),
)
def test_run_manifest_optional_string_preserves_text(
    raw: Any,
    expected: str | None,
) -> None:
    assert manifest_optional_string(raw) == expected


@pytest.mark.parametrize("normalizer", PERMISSIVE_OPTIONAL_FLOATS)
@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        (None, None),
        ("", None),
        (" 2.5 ", 2.5),
        (3, 3.0),
        (True, 1.0),
        ("invalid", None),
        (object(), None),
    ),
)
def test_permissive_optional_floats_share_contract(
    normalizer: OptionalFloat,
    raw: Any,
    expected: float | None,
) -> None:
    assert normalizer(raw) == expected


@pytest.mark.parametrize("normalizer", PERMISSIVE_OPTIONAL_FLOATS)
@pytest.mark.parametrize(("raw", "predicate"), (("nan", math.isnan), ("inf", math.isinf)))
def test_permissive_optional_floats_preserve_non_finite_values(
    normalizer: OptionalFloat,
    raw: str,
    predicate: Callable[[float], bool],
) -> None:
    result = normalizer(raw)
    assert result is not None
    assert predicate(result)


@pytest.mark.parametrize(
    ("raw", "expected"),
    ((None, None), ("", None), (" 2.5 ", 2.5), (True, 1.0)),
)
def test_resource_optional_float_accepts_only_valid_finite_values(
    raw: Any,
    expected: float | None,
) -> None:
    assert resource_optional_float(raw, "measurement") == expected


@pytest.mark.parametrize("raw", (" ", "invalid", object(), float("nan"), float("inf")))
def test_resource_optional_float_rejects_invalid_or_non_finite_values(raw: Any) -> None:
    with pytest.raises(ValueError, match="measurement"):
        resource_optional_float(raw, "measurement")
