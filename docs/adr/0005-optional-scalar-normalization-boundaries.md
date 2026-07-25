# ADR-0005: Optional Scalar Normalization Boundaries

**Date:** 2026-07-17

**Status:** Accepted

## Context

Application, benchmark, schema, migration, and tool modules accumulated local
helpers for optional text and float conversion. Several bodies were equivalent,
but run manifests must preserve exact text and resource-calibration schemas must
reject invalid or non-finite scientific values with field-specific errors.

A single configurable global converter would reduce line count while obscuring
these trust and provenance boundaries.

## Decision

1. `utils.scalar_values.optional_trimmed_text` owns permissive text conversion
   that trims text and maps absent or blank values to `None`.
2. `utils.scalar_values.optional_float` owns permissive float conversion that
   maps absent or invalid values to `None` and preserves Python non-finite float
   results.
3. Existing modules may retain private import aliases during migration, but must
   not retain parallel function bodies.
4. `benchmark_suite.candidate_values.maybe_float` remains a compatibility alias
   to the neutral optional-float primitive.
5. Run-manifest exact-text preservation remains a separate contract.
6. Resource-calibration finite-only, field-specific validation remains a
   separate domain validator.
7. Changes to whitespace preservation, non-finite handling, or validation errors
   are correctness decisions and must not be hidden inside duplicate cleanup.

## Consequences

- Equivalent permissive behavior has one neutral owner across architectural
  layers.
- Provenance text is not silently normalized.
- Scientific schema validation is not weakened into permissive parsing.
- Application, benchmark, schema, and tool entry points require import smoke
  coverage for the neutral `utils` module.
- New scalar helpers must select one of these explicit contracts or document a
  distinct boundary.

## Implementation anchors

- `src/utils/scalar_values.py`
- `benchmark_suite/candidate_values.py`
- `src/schemas/run_manifest.py::_optional_string`
- `src/schemas/resource_calibration.py::_optional_float`
- `tests/test_optional_scalar_contracts.py`
- `docs/developer/OPTIONAL_SCALAR_CONTRACTS.md`
