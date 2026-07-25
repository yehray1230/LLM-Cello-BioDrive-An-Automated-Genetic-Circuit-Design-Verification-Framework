# Pre-release staging manifest

> Status: Phase 0 scope freeze
> Snapshot date: 2026-07-25
> Local branch: `codex/next-development`
> Local HEAD: `f8ebe00b192db14ca5aa1f7467b00ec4790abf5b`
> Local `origin/main`: `f8ebe00b192db14ca5aa1f7467b00ec4790abf5b`
> GitHub repository: `yehray1230/LLM-Orchestrated-Genetic-Circuit-Design-Workflow-Built-Around-Cello`

This manifest freezes the intended publication scope before staging. It is a
classification document, not evidence that any file has been staged, tested,
committed, pushed, accepted, or scientifically validated.

## 1. Snapshot and counting boundary

`git status --porcelain=v1 -uall` reported 1,118 entries before the local-log
ignore repair:

- 186 modified or untracked publication candidates outside
  `.codex_test_logs/`;
- at least 932 local evidence, database, environment, encrypted fixture,
  pytest-temp, or verification-log entries under `.codex_test_logs/`.

Some historical `.codex_test_logs/` paths could not be enumerated because they
exceed the Windows filename limit. The rule `.codex_test_logs/** -> KEEP_LOCAL`
covers the complete subtree, including entries Git could not enumerate.

The local Git fetch attempt timed out and its two newly created Git processes
were stopped. A connector-backed GitHub repository check showed
`f8ebe00b192db14ca5aa1f7467b00ec4790abf5b` as the latest repository commit,
matching the local tracking ref. Remote freshness must be checked again before
push and merge.

## 2. Classification vocabulary

| Code | Meaning |
| --- | --- |
| `KEEP_LOCAL` | Never stage; local evidence, secrets, runtime state, or temp |
| `C0` | Release governance and repository hygiene |
| `C1` | Shared contracts, schemas, normalization, and maintenance governance |
| `C2` | Resource competition and calibration M0-M6 |
| `C3` | Export, adapters, MCP result access, workflow evidence, and safety |
| `C4` | EGMA and EXP-011 offline research-preview contracts |
| `C5` | Integration, case-study packaging, public docs, and generated context |
| `H1` | Stage by hunk across the named target commits |
| `HOLD` | Do not stage until provenance, license, or product decision passes |

Rules are applied in this order: `KEEP_LOCAL`, `HOLD`, `H1`, then `C0..C5`.
No file may be staged merely because it matches a broad directory name.

## 3. KEEP_LOCAL

```text
.codex_test_logs/**
local_plans_private/**
outputs/**
tmp/**
tmp_pytest/**
pytest_temp*/**
.pytest_cache/**
.ruff_cache/**
.llm_cache/**
.ode_cache/**
**/__pycache__/**
venv/**
.venv/**
chroma_db/**
```

`.codex_test_logs/**` is the only currently visible untracked subtree in this
group. The other patterns remain preventive gates.

## 4. C0 — Release governance and hygiene

```text
.github/workflows/ci.yml
.github/workflows/mutation.yml
.gitignore
THIRD_PARTY_NOTICES.md
pyproject.toml
requirements-dev.txt
docs/developer/PRE_RELEASE_EXECUTION_RECORD.md
docs/developer/PRE_RELEASE_QUALITY_BASELINE.md
docs/developer/PRE_RELEASE_TEST_AND_PUBLISH_PLAN.md
docs/developer/PRE_RELEASE_STAGING_MANIFEST.md
```

`.gitignore` is included prospectively because Phase 1 must add
`.codex_test_logs/`.

Focused checks:

```text
git status --short --untracked-files=all
git check-ignore -v .codex_test_logs
git diff --check
```

## 5. C1 — Shared contracts and maintenance governance

### Production and maintenance files

```text
application/__init__.py
benchmark_suite/candidate_values.py
benchmark_suite/score_values.py
docs/adr/**
docs/developer/BOOLEAN_COERCION_CONTRACTS.md
docs/developer/CANDIDATE_VALUE_CONTRACTS.md
docs/developer/CANONICAL_IMPLEMENTATIONS.md
docs/developer/CHANGE_IMPACT_MAP.md
docs/developer/CODEBASE_MAINTENANCE_BASELINE.md
docs/developer/DUPLICATION_POLICY.md
docs/developer/LEGACY_JSON_LISTING_CONTRACT.md
docs/developer/OPTIONAL_SCALAR_CONTRACTS.md
docs/developer/REPEATED_TEST_FACTORY_CONTRACTS.md
docs/developer/SCORE_CLAMP_CONTRACT.md
docs/developer/STABLE_JSON_HASH_CONTRACTS.md
quality/**
src/repositories/sqlite_repository.py
src/schemas/__init__.py
src/schemas/design_ir.py
src/schemas/design_ir_v2.py
src/schemas/design_migrations.py
src/schemas/import_draft.py
src/schemas/state.py
src/scripts/check_duplicate_functions.py
src/utils/boolean_values.py
src/utils/hashing.py
src/utils/lazy_exports.py
src/utils/package_metadata.py
src/utils/scalar_values.py
```

### Tests

```text
tests/test_boolean_coercion_contracts.py
tests/test_candidate_value_contracts.py
tests/test_duplicate_function_checker.py
tests/test_import_boundaries.py
tests/test_legacy_json_listing_contracts.py
tests/test_optional_scalar_contracts.py
tests/test_package_metadata_contract.py
tests/test_repeated_test_factory_contracts.py
tests/test_score_clamp_contracts.py
tests/test_stable_json_hash_contracts.py
```

## 6. C2 — Resource competition and calibration M0-M6

### Production, schemas, fixtures, and docs

```text
benchmark_suite/resource_model_analysis.py
benchmark_suite/resource_parameter_fitting.py
benchmark_suite/resource_plate_reader.py
benchmark_suite/resource_validation.py
benchmark_suite/resource_workflow.py
docs/resource_competition_model_spec.md
src/quality/plate_reader.py
src/schemas/resource_calibration.py
src/schemas/simulation.py
src/tools/ode_simulator.py
src/web/templates/resource_calibrations.html
tests/fixtures/resource_calibration/**
```

### Tests

```text
tests/test_app_ode_charts.py
tests/test_plate_reader_calibration_exp022.py
tests/test_resource_calibration_m0.py
tests/test_resource_calibration_workflow_m5.py
tests/test_resource_competition_m1.py
tests/test_resource_model_analysis_m6.py
tests/test_resource_parameter_fitting_m3.py
tests/test_resource_plate_reader_m2.py
tests/test_resource_validation_m4.py
tests/test_web_ode_trace_contract.py
```

## 7. C3 — Export, adapters, MCP, workflow evidence, and safety

### Production and schemas

```text
src/api/downloads.py
src/exporters/assembly_deliverables.py
src/exporters/export_result.py
src/exporters/genbank_exporter.py
src/exporters/genbank_formatting.py
src/exporters/pdf_exporter.py
src/exporters/plasmid_assembler.py
src/exporters/plasmid_tools.py
src/mcp_server/result_access.py
src/mcp_server/serializers.py
src/schemas/host_optimization.py
src/schemas/workflow_evidence.py
src/tools/assembly_planner.py
src/tools/cello_artifact_parser.py
src/tools/part_library.py
src/tools/primer_designer.py
src/utils/safety_checker.py
src/web/candidate_views.py
```

### Tests

```text
tests/test_assembly_download_contract.py
tests/test_design_exporters.py
tests/test_genbank_formatting_contract.py
tests/test_mcp_result_access.py
tests/test_phase9_adapter_conformance_matrix.py
tests/test_phase9_biological_tool_adapters.py
tests/test_safety_boundary.py
tests/test_safety_checker_phase8_lite.py
tests/test_scientific_data_integration.py
tests/test_workflow_evidence_contracts.py
```

## 8. C4 — EGMA and EXP-011 offline research-preview contracts

### Production, protocols, fixtures, and scripts

```text
benchmark_suite/datasets/validated_circuits_v1.json
benchmark_suite/egma_boolean.py
benchmark_suite/egma_claim_audit.py
benchmark_suite/egma_contracts.py
benchmark_suite/egma_feedback.py
benchmark_suite/egma_generator.py
benchmark_suite/egma_sealing.py
benchmark_suite/egma_topology.py
benchmark_suite/egma_validation.py
benchmark_suite/protocols/egma-benchmark-dry-run-v1.schema.json
benchmark_suite/protocols/egma-claim-audit-v1.schema.json
benchmark_suite/protocols/egma-feedback-trace-v1.schema.json
benchmark_suite/protocols/egma-result-v1.schema.json
benchmark_suite/protocols/egma-sealed-manifest-v1.schema.json
benchmark_suite/protocols/egma-task-v1.schema.json
benchmark_suite/protocols/egma-topology-v1.schema.json
src/scripts/generate_egma_dry_run.py
src/scripts/package_egma_sealed_fixture.py
src/scripts/run_exp011_benchmark.py
tests/fixtures/egma/task_contract_cases.json
```

### Tests

```text
tests/test_egma_benchmark_contract.py
tests/test_egma_feedback_contract.py
tests/test_egma_formal_audits.py
tests/test_egma_generator.py
tests/test_egma_sealed_package.py
tests/test_exp011_reproducibility.py
tests/test_model_routing.py
```

These files remain limited to offline research-preview claims. Neither this
classification nor a passing test promotes them to a confirmatory benchmark.

## 9. C5 — Integration, case-study packaging, and public docs

### Application, packaging, templates, and docs

```text
application/case01_evidence.py
application/demo_baseline.py
application/demo_constants.py
application/design_task_benchmark.py
docs/architecture.md
docs/case_studies/CASE_STUDY_PACKAGE.md
docs/future_roadmap.md
docs/model_assumptions.md
llms-full.txt
scripts/run_case_study_package.py
src/web/templates/base.html
src/web/templates/candidate_detail.html
src/web/templates/share_summary.html
```

### Case-study generated public artifacts

```text
docs/case_studies/output/case_study_manifest.json
docs/case_studies/output/cs1_inducible_reporter.csv
docs/case_studies/output/cs1_inducible_reporter.gb
docs/case_studies/output/cs1_inducible_reporter.ttl
docs/case_studies/output/cs2_toggle_switch.csv
docs/case_studies/output/cs2_toggle_switch.gb
docs/case_studies/output/cs2_toggle_switch.ttl
docs/case_studies/output/cs3_polycistronic_operon.csv
docs/case_studies/output/cs3_polycistronic_operon.gb
docs/case_studies/output/cs3_polycistronic_operon.ttl
```

### Tests

```text
tests/test_phase10_11_publication_acceptance.py
tests/test_phase10_case_study_package.py
tests/test_phase11_evidence_report.py
tests/test_phase1_real_data_benchmark.py
tests/test_v2_research_workspace.py
```

## 10. H1 — Files that require hunk-level staging

These files contain or connect more than one release slice. They must not be
assigned by whole-file staging until their diff has been reviewed.

| File | Reviewed decision |
| --- | --- |
| `app.py` | Stage whole file in C5; changes are legacy integration/ODE presentation cleanup |
| `application/services.py` | Keep as H1; split resource workflow into C2 and export/safety wiring into C3 |
| `benchmark_suite/benchmark_controller.py` | Stage whole file in C1; shared weight/value contract extraction |
| `benchmark_suite/cello_constraint_evaluator.py` | Stage whole file in C1; shared boolean coercion extraction |
| `benchmark_suite/dataset.py` | Stage whole file in C4; literature-fixture provenance validation |
| `benchmark_suite/design_task_dataset.py` | Stage whole file in C4; stable task-set serialization boundary |
| `benchmark_suite/functional_scorer.py` | Stage whole file in C1; shared boolean/Verilog helpers |
| `benchmark_suite/kinetic_scorer.py` | Stage whole file in C1; shared simulation-input helper |
| `benchmark_suite/readiness_evaluator.py` | Stage whole file in C3; optional readiness-score boundary |
| `benchmark_suite/runner.py` | Stage whole file in C4; calibration metrics, capability evidence, and stable hashing |
| `benchmark_suite/scoring_profiles.py` | Stage whole file in C1; shared serialization boundary |
| `benchmark_suite/semantic_evaluator.py` | Stage whole file in C1; shared list normalization |
| `benchmark_suite/static_plausibility_evaluator.py` | Stage whole file in C1; shared Verilog parsing helpers |
| `benchmark_suite/temporal_scorer.py` | Stage whole file in C1; shared trace-value helpers |
| `src/api/main.py` | Stage whole file in C3; safe validation-error response |
| `src/api/routes.py` | Keep as H1; split resource endpoints into C2 and import/export/safety changes into C3 |
| `src/api/schemas.py` | Stage whole file in C2; resource workflow request schema |
| `src/api/v2_routes.py` | Stage whole file in C3; assembly artifact access boundary |
| `src/mcp_server/explainer.py` | Keep as H1; split ODE evidence into C2 and Cello claim boundary into C4 |
| `src/mcp_server/service.py` | Stage whole file in C3; result access, safety, and quick-run boundary |
| `src/tools/tool_adapters.py` | Stage whole file in C3; external/scientific adapter implementations |
| `src/utils/llm_utils.py` | Stage whole file in C4; model-routing cache/provenance support |
| `src/web/routes.py` | Keep as H1; split resource UI into C2, download/safety into C3, and presentation cleanup into C5 |

Before staging each file:

1. inspect `git diff -- <file>`;
2. map every hunk to exactly one target;
3. keep tests and docs in the same target slice;
4. if a hunk cannot be separated safely, merge the dependent slices and record
   the reason instead of creating an artificial commit boundary.

### Final commit-boundary decision

For publication, C2 through C5 are merged into one integration commit. The four
remaining H1 files (`application/services.py`, `src/api/routes.py`,
`src/mcp_server/explainer.py`, and `src/web/routes.py`) connect resource,
adapter, evidence, safety, export, and presentation behavior through shared
service and route contracts. Splitting those hunks would create intermediate
commits that are not independently representative of the tested application.

C1 remains a separate shared-contract/maintenance commit. C0 remains a
separate release-governance and CI-quality commit. This preserves reviewable
boundaries without inventing an untested intermediate state.

## 11. HOLD — Provenance or product decision required

The following files have a provisional target above but remain blocked from
staging until the named review is complete:

| Path | Provisional target | Review decision |
| --- | --- | --- |
| `benchmark_suite/datasets/validated_circuits_v1.json` | C4 | Approved only as a literature-curated engineering fixture. Version 1.2.1 narrows the license scope to project-authored structure/annotations and marks source rights plus parameter review pending. Evidence promotion remains blocked. |
| `benchmark_suite/protocols/exp022_real_pilot_protocol.json` | C2 | Approved as a public protocol template. It records raw input `not_received`, gate `not_started`, no automatic promotion, and no real-pilot result. |
| `docs/case_studies/output/**` | C5 | Approved after fixing the injected GenBank record date. A fresh generation matched all 10 tracked artifacts byte-for-byte; outputs remain computational research-preview artifacts. |
| `llms-full.txt` | C5 | Deferred approval: stage only after the source generator runs and a second run produces no drift. Never hand-edit. |

`benchmark_suite/protocols/exp022_real_pilot_protocol.json` is not otherwise
listed in C2 because this HOLD rule takes precedence.

## 12. Manifest completeness gate

Phase 0 passes only when:

- [x] `.codex_test_logs/**` is covered by `KEEP_LOCAL`.
- [x] all current non-local status entries match exactly one rule or an explicit
  `H1`/`HOLD` record;
- [x] all H1 diffs have a target decision;
- [x] all HOLD reviews have evidence and a release decision;
- [x] each target has a focused test list;
- [x] remote freshness was checked for Phase 0; a second check remains required
  immediately before push;
- [x] no staging, commit, push, or claim promotion occurred during
  classification.

The file-classification, H1 decision, HOLD decision, focused-test, and local-log
conditions are satisfied. Remote freshness was confirmed through the GitHub
connector at `f8ebe00b192db14ca5aa1f7467b00ec4790abf5b` after the shell fetch
timed out, and must still be rechecked immediately before push. No staging,
commit, push, or claim promotion occurred during classification.
