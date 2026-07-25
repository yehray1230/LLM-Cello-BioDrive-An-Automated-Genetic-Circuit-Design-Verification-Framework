# Stable JSON Hash Contracts / 穩定 JSON 雜湊契約

## 1. Purpose / 目的

This document separates two responsibilities that must not be collapsed during
duplicate cleanup:

1. selecting the domain payload whose changes should alter an identity; and
2. serializing that selected payload deterministically and computing SHA-256.

The first responsibility remains with the domain owner. Only the second can be
a small shared primitive when its options are identical.

## 2. Common compact UTF-8 contract / 共通緊密 UTF-8 契約

The dataset, design-task, scoring-profile, benchmark-runner, repository, run
manifest, and simulation implementations all use this core sequence, with the
exceptions recorded below:

- JSON object keys are sorted;
- insignificant whitespace is removed with separators `(",", ":")`;
- JSON text is encoded as UTF-8;
- SHA-256 returns a lowercase 64-character hexadecimal digest.

The main content/configuration family also uses `ensure_ascii=False`, so Unicode
characters remain unescaped before UTF-8 encoding. Changing any of these
serialization choices changes existing identifiers and is a compatibility
change, not routine refactoring.

Python's current JSON default permits `NaN`, `Infinity`, and `-Infinity` tokens.
The hashes therefore remain deterministic for those Python values, although the
serialized text is not strict RFC JSON. Rejecting non-finite values is a future
correctness and interoperability decision and must not be hidden in extraction.

## 3. Domain-owned payload selection / 領域擁有的 payload 選擇

| Owner | Payload contract | Must remain local |
| --- | --- | --- |
| `BenchmarkDataset.content_hash` | Full `asdict(dataset)` | Which dataset fields define content identity |
| `DesignTaskSet.content_hash` | Full `asdict(task_set)` | Which task-set fields define content identity |
| `ScoringProfile.configuration_hash` | Full profile, except omit `biophysical_weights` only when it is `None` | Legacy compatibility rule; an explicitly empty mapping is hash material |
| `benchmark_suite.runner._payload_hash` | Caller-selected benchmark result payload | Result packet composition |
| `sqlite_repository.canonical_payload_hash` | Full repository payload | `_content_hash` separately excludes `revision` |
| `simulation.canonical_payload_hash` | Caller-selected simulation payload | Parameter, scenario, configuration, seed, and result composition |
| `run_manifest.payload_sha256` | Caller-selected provenance payload | Manifest/artifact selection |
| `design_task_benchmark.stable_batch_hash` | Explicit stable result projection, then recursive volatile-value sanitization | Timestamp, run-ID, path, result-field, and runner compatibility policy |

Payload projection, field exclusion, sanitization, prefixes, digest truncation,
and stable ordering of domain lists are not responsibilities of a generic hash
helper.

## 4. Intentional serialization variants / 刻意保留的序列化變體

- Run-manifest and simulation helpers pass `default=str`. This accepts values
  that the plain JSON family rejects and therefore requires an explicit future
  decision before consolidation with that family.
- `CalibrationContext.fingerprint` uses `ensure_ascii=True` and excludes
  `context_id`. Its digest representation and payload boundary stay local.
- Resource fitting, plate-reader, and validation fingerprints currently rely on
  the default `ensure_ascii=True`; some also sort domain lists, add prefixes, or
  truncate the digest. Those are externally visible ID contracts.
- File checksums in the web exporter and Cello wrapper hash bytes or text, not a
  canonical JSON payload, and are outside this family.

## 5. Consolidation decision / 共用化決策

The shared mechanics primitive is:

```python
stable_json_sha256(payload)
```

Its migration scope is limited to callers that already use
`ensure_ascii=False`, `sort_keys=True`, separators `(",", ":")`, UTF-8, no
custom JSON fallback, and a full 64-character SHA-256 digest. Each caller must
prepare its payload first. The extraction must preserve exact hashes with
golden/characterization tests.

The first migration includes dataset/task-set identity, scoring configuration,
benchmark result payloads, and SQLite full-payload hashes. The scoring-profile
compatibility exclusion and repository revision exclusion still happen before
the primitive is called.

Do not add flags for payload exclusions, compatibility fields, sanitization,
prefixes, or truncation. A highly configurable helper would conceal domain
policy and make future hash changes harder to review.

## 6. Verification anchors / 驗證錨點

- `tests/test_stable_json_hash_contracts.py`
- `src/utils/hashing.py`
- `tests/test_exp003_design_task_benchmark.py`
- `tests/test_design_task_dataset.py`
- `tests/test_research_evaluation.py`
- `tests/test_simulation_foundation.py`
- `tests/test_resource_calibration_m0.py`
