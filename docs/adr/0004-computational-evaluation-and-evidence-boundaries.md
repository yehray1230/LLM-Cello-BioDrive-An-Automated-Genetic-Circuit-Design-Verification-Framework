# ADR-0004: Computational Evaluation and Evidence Boundaries

**Date:** 2026-07-17

**Status:** Accepted

## Context

The project combines LLM proposals, deterministic and heuristic evaluators,
reduced simulation, optional external tools, mock fallbacks, resource-model
diagnostics, readiness summaries, and public evidence governance. A numeric score
or completed workflow can be misread as experimental validation if presentation
layers or documents strengthen its meaning.

## Decision

1. `evaluate_candidate()` owns candidate scoring orchestration and component
   aggregation.
2. `evaluate_readiness()` reports domain readiness and blockers; it does not
   convert missing experimental domains into successful evidence.
3. Mock Cello output is workflow scaffolding, not external mapping evidence.
   External Cello status and artifacts remain explicitly labeled.
4. Resource calibration, fitting, sensitivity, and model-comparison workflows
   remain diagnostic research-preview evidence unless stronger validation gates
   are separately met.
5. Evidence eligibility and public claim states are decided by the evidence
   governance schema and verifier, not by UI text, route success, or aggregate
   score.
6. API, Web, MCP, exporters, demos, and documentation may format canonical
   decisions but must preserve uncertainty, unsupported states, provenance, and
   rights metadata.

## Consequences

- Scoring changes require claim and interpretation review, not only unit tests.
- Successful execution does not imply biological buildability or wet-lab
  validation.
- Public proof verifies governance reproducibility, not experimental truth.
- External-tool absence and unsupported evidence remain visible machine-readable
  outcomes.
- New evaluators must state their evidence level and failure/unsupported behavior.

## Implementation anchors

- `benchmark_suite/benchmark_controller.py::evaluate_candidate`
- `benchmark_suite/readiness_evaluator.py::evaluate_readiness`
- `benchmark_suite/resource_workflow.py::run_resource_calibration_workflow`
- `src/tools/cello_wrapper.py`
- `src/schemas/evidence_governance.py`
- `src/scripts/verify_evidence_manifest.py`
- `src/exporters/claim_boundary.py`
- `docs/evidence_governance_spec.md`
- `docs/limitations.md`
