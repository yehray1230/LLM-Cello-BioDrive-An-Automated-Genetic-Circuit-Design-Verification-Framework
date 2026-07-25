# ADR-0006: Stable JSON Hash Boundaries

**Date:** 2026-07-17

**Status:** Accepted

## Context

Several benchmark, persistence, provenance, and simulation modules independently
serialize payloads and compute SHA-256. Some bodies are identical, but their
callers select different identity fields. Other implementations deliberately
use `default=str`, ASCII escaping, domain list ordering, prefixes, digest
truncation, or volatile-data sanitization.

Treating all of these as one configurable utility would couple identity policy
to serialization mechanics and make compatibility changes difficult to see.

## Decision

1. Domain owners retain responsibility for payload projection, field exclusion,
   list ordering, sanitization, prefixes, and digest truncation.
2. Only the exact compact UTF-8 JSON plus full SHA-256 sequence is eligible for
   a neutral shared primitive.
3. Scoring profiles continue to omit `biophysical_weights` only when its value
   is `None`; an explicitly empty mapping remains hash material.
4. `stable_batch_hash` remains domain-owned because its volatile-value removal
   is part of reproducibility policy.
5. `default=str` and `ensure_ascii=True` implementations remain separate until
   their acceptance and representation differences receive explicit decisions.
6. The current non-finite-number behavior is characterized but not changed.
7. Any extraction must prove exact digest preservation; stricter JSON or new
   payload membership requires a separate compatibility decision.

## Consequences

- Exact duplicates can later share a small mechanics-only primitive.
- Identity boundaries remain visible beside their domain models.
- Existing hashes, persisted revisions, seeds, benchmark packets, and resource
  identifiers are not silently invalidated by cleanup.
- New hash producers must document both payload selection and serialization
  contract.

## Implementation anchors

- `src/utils/hashing.py`
- `docs/developer/STABLE_JSON_HASH_CONTRACTS.md`
- `tests/test_stable_json_hash_contracts.py`
- `benchmark_suite/dataset.py`
- `benchmark_suite/design_task_dataset.py`
- `benchmark_suite/scoring_profiles.py`
- `application/design_task_benchmark.py`
- `src/schemas/run_manifest.py`
- `src/schemas/simulation.py`
- `src/repositories/sqlite_repository.py`
