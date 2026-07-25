# Legacy JSON Listing Contract / 舊版 JSON 列表契約

## 1. Scope / 範圍

The legacy Streamlit ODE workspace lists two JSON-backed record families:

- `app._list_host_profiles` from `outputs/api_data/host_profiles`;
- `app._list_parameter_fit_snapshots` from
  `outputs/api_data/parameter_fit_snapshots`.

Both are thin fail-closed wrappers around `JsonRepository.list`. They are not
the canonical application-service or FastAPI listing boundary.

## 2. Current behavior / 現有行為

| Situation | Result |
| --- | --- |
| Repository directory absent | Constructor creates it; wrapper returns `[]` |
| Valid JSON object files | Returned in filename order |
| Payload IDs differ from filenames | Filename order still wins |
| Invalid JSON syntax | That file is skipped |
| Valid JSON containing a list/scalar | That file is skipped |
| Object missing `profile_id` or `snapshot_id` | Returned without validation |
| UTF-8 decode, filesystem, import, or other exception | Wrapper returns `[]` for the entire listing |

The last rule means a non-JSON read failure after a valid record has been read
still discards the accumulated result. This follows from the broad wrapper
`except Exception`, while `JsonRepository.list` itself only skips
`JSONDecodeError` per file.

## 3. Consumer assumptions / 使用端假設

The ODE Streamlit tab immediately indexes host records by `profile_id` and
snapshot records by `snapshot_id`. The listing wrappers do not enforce those
fields. Consequently a syntactically valid but malformed object can pass the
listing boundary and fail later during UI rendering.

This mismatch is a correctness concern, but adding schema filtering or error
reporting would change observable behavior and is outside duplicate cleanup.

## 4. Consolidation decision / 共用化決策

The two entry-point wrappers now call the private app-level
`_list_json_repository_records` helper with their fixed repository paths. The
helper preserves:

- directory creation on first list;
- filename ordering;
- per-file invalid-JSON/non-object skipping;
- broad fail-closed `[]` behavior;
- lack of record schema validation.

Do not move this behavior into a global utility: `JsonRepository.list` is
already the persistence primitive, and the additional broad exception policy
belongs to the legacy UI boundary.

## 5. Deferred correctness decisions / 延後的正確性議題

Review separately whether the legacy UI should:

1. validate required record IDs before constructing select-box options;
2. retain valid records when one file has an encoding or filesystem error;
3. expose a warning instead of silently returning an empty list;
4. stop creating output directories during a read-only listing operation.

## 6. Verification anchors / 驗證錨點

- `tests/test_legacy_json_listing_contracts.py`
- `tests/test_data_foundation.py`
- `app.py::_list_json_repository_records`
- `app.py::_render_ode_simulation_tab`
- `src/repositories/json_repository.py::JsonRepository.list`
