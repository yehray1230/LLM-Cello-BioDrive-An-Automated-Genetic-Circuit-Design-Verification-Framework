# ADR-0002: Design Representation and Migration Boundaries

**Date:** 2026-07-17

**Status:** Accepted

## Context

Search/orchestration state, candidate inspection/export, and persistent design
revision history have different responsibilities. Treating them as one mutable
payload would couple agent routing, UI presentation, exporters, repositories,
and migrations. The repository currently supports DesignIR v1 and v2 and must
preserve compatibility without letting every consumer implement conversion.

## Decision

1. `DesignState` and `SearchNode` own mutable search, routing, scores, pause state,
   and candidate selection.
2. `DesignIR` v1 is the canonical reader/export representation for one selected
   candidate. Candidate UI and exporters consume it instead of reparsing topology
   fields independently.
3. `DesignIRV2` is the canonical persistent design/revision representation used
   by `DesignService` and design repositories.
4. `src/schemas/design_migrations.py` exclusively owns v1/v2 compatibility and
   conversion direction.
5. Routes, templates, repositories, and exporters may call migration/schema
   functions but must not reproduce field mapping locally.
6. Strict current-schema validation and permissive legacy migration may use
   different scalar-normalization policies when their contracts require it.

## Consequences

- Schema changes require migration and round-trip impact review.
- Removing a field from one representation does not authorize silently dropping
  provenance or compatibility data.
- Duplicate-looking parsing helpers are not merged until strict versus permissive
  behavior is compared.
- UI and exporter consistency improves because they share a selected candidate
  representation.

## Implementation anchors

- `src/schemas/state.py`
- `src/schemas/design_ir.py`
- `src/schemas/design_ir_v2.py`
- `src/schemas/design_migrations.py`
- `application/services.py::DesignService`
- `tests/test_data_foundation.py`
- `tests/test_design_ir.py`
- `tests/test_design_exporters.py`
