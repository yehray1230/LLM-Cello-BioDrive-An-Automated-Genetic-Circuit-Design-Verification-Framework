# Change-Impact Map
# 變更影響對照

**Status / 狀態:** Active maintenance policy

## 1. Purpose / 目的

Use this map before implementation and again before declaring a change complete.
It identifies the layers that commonly need to move together. It is not a rule
that every listed test or document must change; unchanged items should still be
considered and deliberately ruled out.

本對照表應在實作前與完成判定前各檢查一次，用來找出通常需要同步的程式層、測試
與文件。表中項目不代表每次都必須修改，但必須確認其不受影響，而不是直接忽略。

## 2. Cross-layer map / 跨層影響表

| Change trigger | Canonical code | Likely consumers | Focused verification anchors | Documentation/claim checks |
| --- | --- | --- | --- | --- |
| Add or change an application service | `application/services.py::create_application_services` and affected service class | `src/api/dependencies.py`, API routes, Web routes, research/run services, test overrides | `tests/test_api_foundation.py`, `tests/test_v2_research_workspace.py`, feature-specific service tests | canonical map; ADR-0003; API README when persistence or public contract changes |
| Change DesignIR v1 fields or parsing | `src/schemas/design_ir.py` | topology conversion, candidate views, comparison, revisions, exporters | `tests/test_design_ir.py`, `tests/test_design_exporters.py`, `tests/test_candidate_routes.py` | architecture DesignIR boundary; workflow export rules |
| Change DesignIR v2 fields or validation | `src/schemas/design_ir_v2.py` | `DesignService`, repositories, API/Web design views, migrations | `tests/test_data_foundation.py`, `tests/test_v2_research_workspace.py`, revision/import tests | ADR-0002; API persistence/contracts |
| Change v1/v2 compatibility | `src/schemas/design_migrations.py` | imports, persistence migration, demo baseline, Web/API views, exporters | `tests/test_data_foundation.py`, `tests/test_external_design_import.py`, round-trip exporter tests | architecture and API migration notes; compatibility claim must remain explicit |
| Change repository backend or persistence routing | `src/repositories/factory.py`, repository protocols/implementations, `create_application_services` | `DesignService`, startup, tests, PostgreSQL/SQLite configuration | `tests/test_data_foundation.py`, repository factory tests in `tests/test_v2_research_workspace.py` | ADR-0003; `src/api/README.md` persistence section |
| Change run lifecycle, status, events, feedback, or artifacts | `src/mcp_server/run_store.py`, `RunService`, `ResearchService` | API, Web monitor, MCP service, project-package/report consumers | `tests/test_api_foundation.py`, `tests/test_mcp_server.py`, run-monitor tests | API/MCP READMEs; workflow result interpretation |
| Change search, repair, pause, or HITL routing | `src/workflows/reflexion_controller.py` and state schemas | agents, RunService, legacy demo, Web decision history | `tests/test_reflexion_architecture.py`, `tests/test_self_healing_phase4b.py` | architecture agent/HITL sections; workflow repair rules |
| Change candidate scoring or grade semantics | `benchmark_suite/benchmark_controller.py::evaluate_candidate` and affected scorer | workflow controller, evaluation/research services, benchmark runner, UI score views | `tests/test_external_tools_and_skill_loop.py`, `tests/test_research_evaluation.py`, scorer-specific tests | evaluation metrics, workflow interpretation, limitations/claim wording |
| Change readiness domains or blocker rules | `benchmark_suite/readiness_evaluator.py::evaluate_readiness` | services, reports, Web/API readiness display, demo baseline | `tests/test_readiness_evaluator.py`, feature readiness tests, demo baseline tests | limitations, model assumptions, roadmap only after verified behavior changes |
| Change ODE/SSA simulation contracts | `src/tools/ode_simulator.py` and simulation schemas | SimulationService, research runs, API/Web forms and plots, adapters | simulation/temporal/stochastic tests, `tests/test_v2_research_workspace.py` | model assumptions, workflow interpretation, API contracts |
| Change resource calibration inputs, fitting, validation, or analysis | resource schema plus `benchmark_suite/resource_workflow.py` and stage modules | EvaluationService, API schemas/routes, Web routes/templates, fixtures | `tests/test_resource_calibration_m0.py` through `tests/test_resource_model_analysis_m6.py` | resource model spec, model assumptions, roadmap boundary, ADR-0004 |
| Change settings or credentials | `application/settings.py::SettingsService` | API settings routes, Web settings routes/template, Cello wrapper, connection tests | `tests/test_settings.py`, relevant Web/API tests | API credential boundary; never expose stored secrets in docs or payloads |
| Change package-version provenance or missing-package behavior | `src/utils/package_metadata.py::package_version` | plasmid reports, assembly plans, primer-design deliverables | `tests/test_package_metadata_contract.py`, `tests/test_plasmid_tools.py`, `tests/test_assembly_planner.py`, `tests/test_assembly_deliverables.py` | dependency/licensing documentation when package availability or naming changes |
| Change external Cello/mock behavior | `src/tools/cello_wrapper.py` and artifact parser | workflow, agents, evaluators, settings, UI notices, exports | Cello wrapper/parser tests, claim-boundary tests, workflow tests | QUICKSTART mock/real section, workflow, architecture, limitations, ADR-0004 |
| Change BOM/GenBank/SBOL3/project-package export | exporters, `src/exporters/genbank_formatting.py`, `ExportService`, claim-boundary exporter | API download routes, Web downloads, MCP artifacts | `tests/test_design_exporters.py`, `tests/test_plasmid_assembler.py`, `tests/test_genbank_formatting_contract.py`, API export tests, claim-boundary tests | workflow export rules, limitations, evidence/rights metadata |
| Change evidence eligibility, licensing, or public proof | evidence governance schema and verifier script | evidence manifests, exporters, public docs, release proof | `tests/test_evidence_governance.py`, `tests/test_design_exporters.py`, `tests/test_stage_f_verification.py` | evidence governance spec, licensing decision, limitations, ADR-0004 |
| Change FastAPI/Web navigation or user workflow | `src/web/routes.py`, templates, API/service contract | launcher, browser workflow, candidate/run/design views | route-specific tests and browser verification for permission/navigation behavior | QUICKSTART and workflow; ADR-0001 |
| Change assembly artifact download lookup or HTTP response | `AssemblyDeliverableService.artifact`, `src/api/downloads.py` | API v2 and Web download routes | `tests/test_assembly_download_contract.py`, `tests/test_assembly_deliverables.py`, API/Web integration tests | preserve path-containment checks, 404 detail, filename, media type, and export claim headers where applicable |
| Change MCP tools or response shapes | `src/mcp_server/service.py`, `src/mcp_server/result_access.py`, run store, MCP schemas | external MCP clients, diagnosis/comparison, explanations, artifacts, capability discovery | `tests/test_mcp_server.py`, `tests/test_mcp_result_access.py`, `tests/test_phase11_evidence_report.py`, tool-capability tests | MCP README and API/MCP contract notes |
| Change workflow/evaluator evidence normalization, completeness, or numeric projection | `src/schemas/workflow_evidence.py` | EXP-011 runner, service-response adapters, simulation and temporal evaluators, Web/legacy Streamlit trace displays | `tests/test_workflow_evidence_contracts.py`, `tests/test_exp011_reproducibility.py`, `tests/test_app_ode_charts.py`, `tests/test_web_ode_trace_contract.py` | canonical map; freeze the contract version and source hash before any future live run |

## 3. Completion checklist / 完成檢查

For a change that touches any row above:

- [ ] Canonical implementation was changed or deliberately left unchanged.
- [ ] Adapters remain thin and do not duplicate domain decisions.
- [ ] Persistence and migration compatibility were considered.
- [ ] Focused regression tests cover the changed contract and at least one
      failure/unsupported path where relevant.
- [ ] Web, API, MCP, exporter, and legacy surfaces were checked according to
      their actual responsibility.
- [ ] Scientific assumptions and public claims did not become stronger without
      evidence.
- [ ] Documentation was updated only after implementation and verification agree.
- [ ] New architectural ownership is recorded in the canonical map or an ADR.

## 4. Stop conditions / 停止條件

Stop and split the change when:

- a route or template begins implementing evaluator, migration, or persistence
  rules;
- a compatibility change cannot state which version owns the canonical value;
- a refactor would alter scientific behavior without characterization tests;
- an apparently shared helper needs incompatible error or null semantics;
- a claim/document update is ahead of executable evidence.

## 5. Related documents / 相關文件

- [Canonical implementations](CANONICAL_IMPLEMENTATIONS.md)
- [Duplication policy](DUPLICATION_POLICY.md)
- [Codebase maintenance baseline](CODEBASE_MAINTENANCE_BASELINE.md)
- [Architecture decision records](../adr/README.md)
