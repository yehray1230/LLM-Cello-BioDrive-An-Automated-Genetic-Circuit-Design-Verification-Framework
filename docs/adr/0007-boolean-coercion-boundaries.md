# ADR-0007: Boolean Coercion Boundaries

**Date:** 2026-07-18

**Status:** Accepted

## Context

The codebase contains Boolean conversion at candidate, persisted-state,
simulation, Cello, functional truth-table, and catalog-validation boundaries.
Some implementations are exact duplicates, while others deliberately differ
in accepted tokens, default behavior, truthiness fallback, and error type.

A single configurable converter would make these domain choices difficult to
see and could turn invalid metadata or failed external mappings into valid
truthy values.

## Decision

1. Candidate, state, and ODE compatibility coercion form one exact defaulted
   value-level contract and may share a neutral primitive in a later mechanical
   extraction.
2. Candidate dictionary lookup remains in the benchmark layer.
3. Cello retains explicit mapping-status tokens and may delegate only the basic
   defaulted behavior.
4. Functional truth-table signal conversion remains a separate no-default
   contract with `high` and `on` true tokens.
5. Agent and workflow-kit catalog validators remain strict and retain their own
   domain error types.
6. Token-set changes, broader mapping-status recognition, and stricter handling
   of non-string objects are correctness changes, not duplicate cleanup.

## Consequences

- One exact permissive family can later be consolidated without widening its
  vocabulary.
- Cello success/failure language stays visible at the external-tool boundary.
- Truth-table semantics and catalog validation cannot be accidentally weakened
  through a generic truthiness helper.
- New Boolean readers must choose and document a boundary contract.

## Implementation anchors

- `src/utils/boolean_values.py`
- `docs/developer/BOOLEAN_COERCION_CONTRACTS.md`
- `tests/test_boolean_coercion_contracts.py`
- `benchmark_suite/benchmark_controller.py`
- `benchmark_suite/cello_constraint_evaluator.py`
- `benchmark_suite/functional_scorer.py`
- `src/schemas/state.py`
- `src/tools/ode_simulator.py`
- `src/catalog/agent_catalog.py`
- `src/catalog/workflow_kit_catalog.py`
