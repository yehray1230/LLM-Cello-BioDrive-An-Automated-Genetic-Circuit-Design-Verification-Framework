# Optional scalar contracts
# 選用純量轉換契約

**Status / 狀態:** Phase 9 implemented neutral contracts

## 1. Scope / 範圍

This record classifies helpers that convert optional string- and float-like
values. Similar names do not imply one contract: provenance preservation,
permissive compatibility parsing, and strict scientific schema validation have
different responsibilities.

Score clamping and optional benchmark scores are outside this scope because they
also assign score semantics.

## 2. Trimmed optional text / 去除空白的選用文字

Nine helpers currently share this observable contract:

- `None`, empty text, and whitespace-only text become `None`;
- other values are converted with `str(...)`, trimmed, and returned;
- `0` becomes `"0"` and `False` becomes `"False"`.

The family appears in Case 01 evidence, application services, readiness
findings, DesignIR v1/v2, design migration, host optimization, import drafts,
and the part library.

**Decision:** implemented in
`utils/scalar_values.py::optional_trimmed_text`. Application, benchmark, schema,
migration, and tool modules retain compatible private aliases without parallel
function bodies. Import behavior is covered by an independent Python subprocess
smoke test.

## 3. Permissive optional float / 寬鬆選用浮點數

Six helpers, including benchmark `maybe_float`, share this contract:

- `None`, blank/invalid strings, and incompatible objects become `None`;
- numeric strings, numbers, and booleans use Python `float(...)` conversion;
- `NaN` and infinities are preserved rather than rejected.

The family appears in candidate benchmark values, DesignIR conversion, design
migration, host optimization, Cello artifact parsing, and the part library.

**Decision:** implemented in `utils.scalar_values.optional_float`.
`benchmark_suite/candidate_values.py::maybe_float` remains a compatibility alias
to the neutral function. The ownership decision is recorded by ADR-0005.

## 4. Preserve-exact optional string / 保留原文的選用文字

`src/schemas/run_manifest.py::_optional_string` is intentionally different:

- only `None` becomes `None`;
- empty strings and surrounding whitespace are preserved;
- all non-`None` values use `str(...)` without trimming.

Run manifests are provenance records. Normalizing stored tool versions or
artifact hashes during generic scalar cleanup could change the recorded input
or hash material.

**Decision:** keep this contract separate and give it a preservation-oriented
name if it is ever made public. Do not route it through the trimmed-text helper.

## 5. Strict resource-calibration optional float / 嚴格資源校正浮點數

`src/schemas/resource_calibration.py::_optional_float` is a validation boundary:

- `None` and the exact empty string become `None`;
- valid finite numeric values are returned;
- invalid, whitespace-only, `NaN`, and infinite values raise field-specific
  `ValueError` messages.

**Decision:** keep this domain validator separate. Its field name, finite-value
requirement, and error contract must not be weakened by a permissive utility.

## 6. Maintenance rule / 維護規則

- maintain trimmed text and permissive float as two explicit functions, not one
  generic configurable converter;
- keep run-manifest preservation and resource validation separate;
- retain import/packaging smoke coverage for application, benchmark, schema,
  and tool entry points;
- preserve non-finite float behavior during extraction; hardening is a separate
  correctness decision;
- update ADR-0005 if ownership or boundary responsibilities change.
