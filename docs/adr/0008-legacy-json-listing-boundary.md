# ADR-0008: Legacy JSON Listing Boundary

**Date:** 2026-07-18

**Status:** Accepted

## Context

The legacy Streamlit ODE workspace contains duplicate wrappers for listing host
profiles and parameter-fit snapshots. Each constructs a `JsonRepository` at a
fixed relative directory, calls `list`, and converts every exception into an
empty list.

The underlying repository already owns filename ordering and per-file JSON
handling. The wrappers add a legacy UI fail-closed policy and currently perform
no record-schema validation.

## Decision

1. `JsonRepository.list` remains the persistence primitive.
2. Host-profile and parameter-fit wrappers retain separate public/private entry
   names because their UI consumers and record IDs differ.
3. A later mechanical extraction may share only one private app-level helper
   parameterized by repository path.
4. Extraction must preserve directory creation, filename ordering, invalid JSON
   and non-object skipping, broad exception-to-empty-list behavior, and lack of
   schema filtering.
5. Schema validation, partial-result recovery, user-visible warnings, and
   read-only directory semantics require separate correctness decisions.
6. This legacy boundary must not become a new global repository abstraction.

## Consequences

- Exact wrapper duplication can be removed without changing legacy UI output.
- Persistence behavior stays in `JsonRepository`, while fail-closed presentation
  policy remains visible in `app.py`.
- Known silent-failure and malformed-record risks are documented instead of
  being accidentally changed during cleanup.

## Implementation anchors

- `app.py::_list_json_repository_records`
- `docs/developer/LEGACY_JSON_LISTING_CONTRACT.md`
- `tests/test_legacy_json_listing_contracts.py`
- `app.py::_list_host_profiles`
- `app.py::_list_parameter_fit_snapshots`
- `src/repositories/json_repository.py::JsonRepository.list`
