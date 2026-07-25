# Canonical Implementations
# 權威實作索引

**Status / 狀態:** Active maintenance policy

## 1. Purpose / 目的

This document answers one maintenance question: **where should a behavior be
implemented or changed first?** It records architectural ownership, not every
function in the repository.

本文件回答一個維護問題：**某項行為應優先在哪裡實作或修改？** 它記錄架構上的
權威來源，而不是建立所有函式的人工登記簿。

Terms used here:

- **Canonical:** owns the behavior or contract.
- **Adapter:** converts transport, UI, version, or external-tool input to the
  canonical contract; it must not invent parallel domain behavior.
- **Consumer:** reads canonical output and may format it without changing its
  meaning.
- **Legacy:** maintained for compatibility or backup, but not a target for new
  domain behavior.

## 2. Canonical map / 權威實作對照

| Capability | Canonical implementation | Allowed adapters and consumers | Must not become a second implementation |
| --- | --- | --- | --- |
| Default user interface | `src/api/main.py` plus `src/web/routes.py` and `src/web/templates/` | JSON routes in `src/api/routes.py`; launch scripts | `app.py` is legacy Streamlit and does not require feature parity |
| Assembly artifact HTTP response adapter | `src/api/downloads.py::assembly_artifact_file_response` | API v2 and Web assembly-download route wrappers | Route-local 404 detail, filename, or media-type response construction; filesystem authorization remains in `AssemblyDeliverableService.artifact` |
| Agent/search orchestration | `src/workflows/reflexion_controller.py::run_reflexion_workflow` | `RunService`, `ResearchService`, API/Web run routes | UI or route-local repair/search loops |
| Application service composition | `application/services.py::create_application_services` | `src/api/dependencies.py::get_services`; test dependency overrides | Constructing parallel service graphs inside routes, templates, or feature modules |
| Lazy package export compatibility | `src/utils/lazy_exports.py::install_lazy_exports` plus declarative maps in `application/__init__.py` and `src/schemas/__init__.py` | Existing `from application import ...` and `from schemas import ...` callers | Eager aggregate imports in package `__init__.py` files or copied module-level `__getattr__` implementations |
| Settings and Cello configuration | `application/settings.py::SettingsService` | API and Web settings routes; `CelloWrapper` reads resolved configuration | Partial settings stores that silently omit command/UCF/sensor/device state |
| Search/runtime state | `src/schemas/state.py::DesignState` and `SearchNode` | controller, agents, run presentation | Using persistent DesignIR records as mutable search state |
| Candidate reader/export representation | `src/schemas/design_ir.py::DesignIR` and `topology_to_design_ir` | candidate views, revisions, comparison, BOM/GenBank/SBOL3 exporters | UI/export code reparsing independent topology fields |
| Persistent design/revision representation | `src/schemas/design_ir_v2.py::DesignIRV2` | `DesignService`, repository implementations, API/Web design views | Route-local persistence payloads that bypass schema validation |
| Design v1/v2 compatibility | `src/schemas/design_migrations.py` | `DesignService` and import/export adapters | New migration logic in API, Web, exporters, or repositories |
| Design repository selection | `src/repositories/factory.py::create_design_repository` | SQLite and PostgreSQL repository implementations | Environment/backend selection in routes or services other than the composition root |
| Draft and small JSON record persistence | `src/repositories/json_repository.py::JsonRepository` as wired by `create_application_services` | import drafts, benchmark/calibration snapshots, registries | Direct path-based JSON writes from routes or templates |
| Run lifecycle persistence | `src/mcp_server/run_store.py::RunStore` | `RunService`, `ResearchService`, API/Web/MCP consumers | Separate run-status/event/artifact stores per interface |
| Persisted MCP result topology access | `src/mcp_server/result_access.py::best_topology_from_result` | MCP diagnosis/comparison services and design explanation through compatibility aliases | Consumer-local direct-versus-summary precedence or unreviewed `data.best_topology` fallback |
| Workflow/evaluator evidence contract | `src/schemas/workflow_evidence.py` (`WorkflowEvidenceEnvelopeV1`, `SimulationEvidenceV1`, `ODETraceEvidenceV1`, `is_valid_ode_trace`, and `project_ode_trace_rows`) | EXP-011 service-response adapter and evaluator; Web and legacy Streamlit trace-display gates; future evidence consumers through explicit versioned adapters | Script-, route-, or UI-local payload normalization, ODE/simulation completeness, or partial numeric-series projection rules |
| Candidate evaluation orchestration | `benchmark_suite/benchmark_controller.py::evaluate_candidate` | `EvaluationService`, `ResearchService`, benchmark runner, workflow controller | Route/UI recomputation of weighted totals, grades, or component semantics |
| Benchmark candidate numeric conversion | `benchmark_suite/candidate_values.py` | Benchmark controller and functional, kinetic, temporal, and static-plausibility scorers | Scorer-local copies of candidate float/int or permissive float conversion |
| Benchmark score clamping | `benchmark_suite/score_values.py::clamp01` | Benchmark controller and Cello, functional, semantic, static-plausibility, and temporal evaluators | Scorer-local `[0.0, 1.0]` clamp bodies |
| Permissive optional scalar conversion | `src/utils/scalar_values.py` | Application, benchmark, schema, migration, and tool modules through compatible aliases | Local copies of trimmed optional text or permissive optional float conversion |
| Package-version provenance lookup | `src/utils/package_metadata.py::package_version` | Plasmid tooling, assembly planning, and primer design through compatibility aliases | Consumer-local metadata lookup, broad exception swallowing, or inconsistent missing-package sentinels |
| Compact UTF-8 JSON SHA-256 | `src/utils/hashing.py::stable_json_sha256` | Dataset/task-set identity, scoring configuration, benchmark result payloads, and SQLite full-payload hashes | Local copies using identical JSON options; payload selection or compatibility policy inside the shared helper |
| Defaulted Boolean compatibility coercion | `src/utils/boolean_values.py::defaulted_bool` | Candidate dictionary wrapper, persisted state, and ODE compatibility payloads | Adding Cello mapping tokens, truth-table signal tokens, or strict catalog validation to the neutral primitive |
| Readiness evaluation | `benchmark_suite/readiness_evaluator.py::evaluate_readiness` | services, reports, Web/API presentation | Treating one aggregate score as experimental readiness or reinterpreting null domains |
| Resource calibration workflow | `benchmark_suite/resource_workflow.py::run_resource_calibration_workflow` | `EvaluationService`, API routes, Web routes/templates | Duplicating preprocessing/fitting/held-out orchestration in presentation layers |
| Resource model contract | `src/schemas/resource_calibration.py` plus `docs/resource_competition_model_spec.md` | resource workflow, analysis modules, API schemas | Silent schema variants in fixtures, routes, or templates |
| Public evidence governance | `src/schemas/evidence_governance.py` and `src/scripts/verify_evidence_manifest.py` | tracked evidence manifests, public proof command | UI text or docs manually assigning stronger claim states |
| Export claim boundary | `src/exporters/claim_boundary.py` | BOM/GenBank/SBOL3/project-package exports and headers | Exporter-specific biological claims without the shared boundary payload |
| GenBank text formatting primitives | `src/exporters/genbank_formatting.py` | Linear-construct and plasmid GenBank exporters through compatibility aliases | Exporter-local missing-sequence scans, ORIGIN wrapping, locus normalization, whitespace collapse, or qualifier escaping |
| Canonical EXP-003 task contract | `benchmark_suite/task_sets/exp003_design_tasks_v1.json` | design-task runner, demo baseline, benchmark reports | Reusing candidate-scoring fixtures as the design-task source of truth |
| Logic design skill catalog | canonical logic skill JSON plus `src/tools/skill_retriever.py` | agents and maintenance guide | Markdown copies treated as runtime data |

## 3. Pending canonical helpers / 尚未指定權威來源的 helper

The following families were identified in the maintenance baseline but are not
yet canonicalized:

| Family | Current state | Required decision before extraction |
| --- | --- | --- |
| Specialized Boolean coercion | Cello, functional truth-table, and strict catalog contracts characterized in `BOOLEAN_COERCION_CONTRACTS.md` and ADR-0007 | Keep these domain vocabularies and validation errors separate from `defaulted_bool` |
| Legacy Streamlit JSON listing | `JsonRepository.list` plus `app.py::_list_json_repository_records` | Keep persistence behavior in the repository and broad fail-closed policy in the private UI helper; domain wrappers only select fixed paths |
| Repeated test factories | CAND-007 characterized in `REPEATED_TEST_FACTORY_CONTRACTS.md`; no shared owner exists yet | Only the three-module buffer-topology subfamily is eligible for a later bounded extraction, with explicit `copy_number` representation and fresh nested payloads |

Until those contracts are decided, do not introduce a global utility solely to
reduce line count.

## 4. Change rule / 修改規則

Before adding behavior:

1. Search this map by capability.
2. Inspect the canonical implementation and its callers.
3. Reuse or extend the canonical contract when the semantics match.
4. Add a thin adapter when only transport, version, UI, or external-tool format
   differs.
5. Update `CHANGE_IMPACT_MAP.md` when a new downstream surface appears.
6. Add or amend an ADR when architectural ownership changes.

If the canonical location is unclear, record the ambiguity in the pull request
or maintenance report; do not resolve it by creating another implementation.

## 5. Related documents / 相關文件

- [Codebase maintenance baseline](CODEBASE_MAINTENANCE_BASELINE.md)
- [Change-impact map](CHANGE_IMPACT_MAP.md)
- [Duplication policy](DUPLICATION_POLICY.md)
- [Architecture decision records](../adr/README.md)
- [Candidate value contracts](CANDIDATE_VALUE_CONTRACTS.md)
- [Score clamp contract](SCORE_CLAMP_CONTRACT.md)
- [Optional scalar contracts](OPTIONAL_SCALAR_CONTRACTS.md)
