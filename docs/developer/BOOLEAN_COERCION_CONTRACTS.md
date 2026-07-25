# Boolean Coercion Contracts / 布林轉換契約

## 1. Purpose / 目的

Boolean-looking values enter the project through candidate fixtures, persisted
state, simulator compatibility payloads, Cello outputs, truth tables, and
catalog metadata. Similar helper names do not imply interchangeable behavior.
This document defines the four current boundaries before any consolidation.

## 2. Defaulted compatibility coercion / 帶預設值的相容轉換

Current owners:

- `benchmark_controller._candidate_bool` (dictionary lookup plus coercion);
- `schemas.state._coerce_bool`;
- `tools.ode_simulator._coerce_bool`.

All three use the same value-level behavior:

| Input | Result |
| --- | --- |
| `None` | supplied default |
| `bool` | unchanged |
| case-insensitive `1`, `true`, `yes`, `y` | `True` |
| case-insensitive `0`, `false`, `no`, `n` | `False` |
| unknown or blank string | supplied default |
| other object | Python `bool(value)` |

This exact family is implemented by
`utils.boolean_values.defaulted_bool`. Candidate dictionary lookup remains a
wrapper owned by the benchmark layer; state and ODE keep compatible private
import aliases.

## 3. Cello mapping coercion / Cello mapping 轉換

`cello_constraint_evaluator._coerce_bool` extends the defaulted token vocabulary:

- true: `mapped`, `success`, `successful`;
- false: `failed`, `unmapped`.

Other status-like strings, including `mapping_failed` and `not_mapped`, are not
Boolean tokens and currently return the supplied default. Separately,
`_is_cello_failure` recognizes a broader failure-status vocabulary. These two
responsibilities must not be silently made equivalent.

**Decision:** Cello retains the external-status adapter. It may call a shared
defaulted primitive only after handling its explicit Cello tokens.

## 4. Functional truth-table coercion / 功能真值表轉換

`functional_scorer._as_bool` interprets signal-level truth-table cells:

- string true tokens: `1`, `true`, `yes`, `high`, `on`;
- every other string, including `0`, `false`, `low`, `off`, unknown strings,
  mapping statuses, and blank strings: `False`;
- non-string values use Python `bool(value)`.

There is no supplied default and no invalid-string state. This is a compact
truth-level convention, not the defaulted compatibility contract.

## 5. Strict catalog validation / 嚴格 catalog 驗證

`agent_catalog._as_bool` and `workflow_kit_catalog._as_bool` accept:

- actual booleans;
- string true tokens `true`, `yes`, `1`;
- string false tokens `false`, `no`, `0`.

All other inputs are rejected. Numeric `1` and `0` are invalid even though the
corresponding strings are accepted. Each catalog raises its own domain error
type so validation reports retain the correct owner.

**Decision:** keep the catalog validators local in this slice. Do not weaken
strict metadata validation into permissive truthiness, and do not hide domain
error selection inside a configurable global helper.

## 6. Maintenance rule / 維護規則

- Select the contract from the data boundary, not the desired helper name.
- Adding or removing string tokens is a behavior change requiring caller-level
  tests.
- Do not use Python `bool("false")`; every string boundary above has explicit
  semantics.
- Do not expand Cello Boolean tokens merely to match failure classification.
- Reuse `defaulted_bool` only for the exact defaulted family and preserve domain
  wrappers when they own lookup or payload selection.

## 7. Verification anchors / 驗證錨點

- `tests/test_boolean_coercion_contracts.py`
- `src/utils/boolean_values.py`
- `tests/test_candidate_value_contracts.py`
- `tests/test_research_evaluation.py`
- `tests/test_simulation_foundation.py`
- `tests/test_agent_catalog.py`
- `tests/test_workflow_kit_catalog.py`
