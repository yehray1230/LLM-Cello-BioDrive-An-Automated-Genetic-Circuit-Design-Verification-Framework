# Codebase Maintenance Baseline
# 程式碼維護基準

**Snapshot date / 盤點日期:** 2026-07-17

**Phase / 階段:** Phase 1 — inventory and classification only

**Behavior changes / 行為變更:** None

## 1. Purpose / 目的

This document records the first maintenance baseline for identifying code-growth,
cross-layer coupling, and duplicate-implementation risks. It is an audit input,
not a refactoring mandate. A candidate listed here must be reviewed for contract
and boundary differences before code is shared or removed.

本文件記錄第一階段的維護基準，用來辨識程式量膨脹、跨層耦合及重複實作風險。
它是後續決策的盤點輸入，不代表列出的函式都必須合併。任何候選在共用或刪除前，
都必須先比較行為契約與所在邊界。

## 2. Snapshot boundary and method / 盤點邊界與方法

The snapshot includes the current working tree, including an in-progress resource
competition and calibration slice. The working tree already contained modified
and untracked implementation, schema, route, template, fixture, test, and model
documentation files before this audit. This phase did not edit those files.

本次盤點包含目前工作樹，也包含進行中的資源競爭與校準工作。盤點開始前，工作樹
已存在尚未提交的程式、schema、route、template、fixture、測試與模型文件變更；
本階段沒有修改這些既有變更。

The audit used:

- the code knowledge graph for architecture, call paths, structural similarity,
  function counts, and complexity signals;
- direct source inspection for candidate behavior confirmation;
- Markdown inventory and heading/keyword checks for documentation coverage;
- Git status only to establish the snapshot boundary.

Current graph snapshot:

| Signal | Count |
| --- | ---: |
| Nodes | 1,958 |
| Edges | 7,088 |
| Functions | 988 |
| Methods | 166 |
| Classes | 115 |
| Routes | 51 |
| `SIMILAR_TO` edges | 21 |

The graph is a discovery aid, not the source of truth. Direct inspection remains
required because structural similarity can miss small clones and can also flag
intentional adapters.

## 3. Classification rules / 分類規則

| Code | Meaning | Default treatment |
| --- | --- | --- |
| `D1` | Confirmed production duplicate or identical core behavior | Prefer one canonical helper after regression coverage exists |
| `D2` | Likely shared-helper family with small contract differences | Compare edge cases before deciding whether to share |
| `A` | Boundary adapter or version-specific translation | Keep local unless a shared implementation preserves the boundary |
| `L` | Legacy-path duplication | Record and defer unless it affects the default interface or correctness |
| `T` | Test fixture/helper duplication | Report only; centralize only stable domain factories used broadly |
| `H` | Size, complexity, or fan-out hotspot; not evidence of duplication | Add change-impact coverage before considering decomposition |

## 4. Structural concentration / 結構集中區

Line counts are descriptive signals only. Large files are not automatically
defects, especially when they contain scientific contracts or many thin routes.

| Area | Current signal | Classification | Maintenance implication |
| --- | --- | --- | --- |
| `app.py` | 3,481 lines; 106 functions; several of the highest-complexity render functions | `L`, `H` | Legacy Streamlit path. Do not start a broad cleanup; prevent new domain logic from landing here. |
| `src/web/routes.py` | 2,724 lines; 109 functions | `H` | Default HTML surface has high route/helper density. Changes need route-service-template-test impact tracking. |
| `src/tools/ode_simulator.py` | 2,514 lines | `H` | Scientific core. Require focused behavior tests before decomposition; size alone is not a split trigger. |
| `application/services.py` | 1,736 lines; central service composition and many service classes | `H` | `create_application_services()` is a high-impact construction boundary and should be recorded as canonical. |
| `src/mcp_server/service.py` | 1,276 lines | `H` | MCP boundary needs contract-level change tracking rather than helper-count reduction alone. |
| `src/api/routes.py` | 886 lines; 62 functions | `A`, `H` | Mostly transport adapters; guard against copying service/domain behavior into routes. |
| `src/web/candidate_views.py` | 874 lines | `H` | Presentation shaping should remain separate from canonical domain evaluation. |
| `src/schemas/resource_calibration.py` | 774 lines; current uncommitted slice | `H` | New schema family should stabilize before extraction decisions are made. |
| `src/tools/tool_adapters.py` | 754 lines | `A`, `H` | Adapter duplication may be intentional when external-tool contracts differ. |

High-value function hotspots:

| Symbol | Signal | Classification | Why it matters |
| --- | --- | --- | --- |
| `benchmark_suite.layout_critic.analyze_layout_issues` | 217 lines; cyclomatic 45; cognitive 201 | `H` | Highest current reasoning complexity; needs characterization tests before any split. |
| `app._render_ode_simulation_tab` | 473 lines; cyclomatic 66; cognitive 196 | `L`, `H` | Very large legacy UI function; defer unless maintenance work touches this path. |
| `src.schemas.design_migrations.migrate_design_ir_v1_to_v2` | 234 lines; 19 outbound dependencies | `A`, `H` | Canonical migration boundary; duplication here would create data drift. |
| `benchmark_suite.resource_parameter_fitting.fit_resource_competition_parameters` | 225 lines; cyclomatic 17; cognitive 20 | `H` | Current scientific fitting slice; preserve diagnostics and provenance while it stabilizes. |
| `benchmark_suite.resource_plate_reader.preprocess_plate_reader_csv` | 210 lines; cyclomatic 13; cognitive 19 | `H` | Multi-stage preprocessing and QC boundary; decomposition must retain traceability. |
| `benchmark_suite.benchmark_controller.evaluate_candidate` | 164 lines; calls multiple scorer families | `H` | Canonical evaluation orchestrator and a likely change-impact anchor. |
| `benchmark_suite.resource_workflow.run_resource_calibration_workflow` | multi-stage orchestration across preprocessing, fitting, and held-out validation | `H` | New cross-module workflow; routes and services should remain thin wrappers around it. |

## 5. Duplicate and parallel-implementation candidates / 重複與平行實作候選

### CAND-001 — Boolean coercion

**Classification:** `D2` / `A`

**Confidence:** High after Phase 4 source and contract comparison

**Priority for later review:** P0

- `benchmark_suite/benchmark_controller.py::_candidate_bool`
- `benchmark_suite/cello_constraint_evaluator.py::_coerce_bool`

The helpers share basic Boolean tokens, but the Cello adapter additionally
recognizes `mapped`, `success`, `successful`, `failed`, and `unmapped`. The
controller helper also performs candidate-key lookup.

**Phase 4 correction:** Do not directly consolidate these helpers. Keep the
external mapping-status vocabulary at the Cello boundary; share a primitive
only if token profiles remain explicit.

**Phase 12 characterization:** The full inventory contains four contracts.
Candidate, state, and ODE defaulted value coercion are an exact consolidation
family. Cello mapping tokens, functional truth-table signal tokens, and strict
catalog validation remain separate boundaries. See
`BOOLEAN_COERCION_CONTRACTS.md` and ADR-0007.

**Phase 13 implementation:** The exact defaulted value contract now lives in
`src/utils/boolean_values.py::defaulted_bool`. Candidate lookup remains a thin
benchmark wrapper, while state and ODE retain compatible private import aliases.
Cello, truth-table, and strict catalog implementations remain local and
unchanged.

### CAND-002 — Candidate numeric accessors

**Classification:** `D2`

**Confidence:** High that code is repeated; medium that one helper contract fits all callers

**Priority for later review:** P0

- `_candidate_float` exists in `benchmark_controller.py`, `kinetic_scorer.py`,
  `functional_scorer.py`, and `temporal_scorer.py`.
- `_candidate_int` exists in `benchmark_controller.py` and `kinetic_scorer.py`.

The direct `try/except` variants match closely, while the functional and temporal
scorers route through `_maybe_float`. Before consolidation, compare handling of
missing keys, `None`, invalid strings, booleans, and non-finite numbers.

**Phase 1 decision:** Shared benchmark-input utility candidate; requires an
explicit coercion contract first.

**Phase 4 decision:** Characterization tests confirm the float and integer
accessors currently share observable behavior. Mechanical extraction to
`benchmark_suite/candidate_values.py` is approved as the next bounded phase;
non-finite behavior must remain unchanged unless handled as a separate
correctness change.

**Phase 5 implementation:** `candidate_float`, `candidate_int`, and
`maybe_float` now live in `benchmark_suite/candidate_values.py`. Existing
scorers retain import aliases for compatibility but no longer contain parallel
function bodies.

### CAND-003 — Score clamping

**Classification:** `D1`/`D2`

**Confidence:** High

**Priority for later review:** P1

Five benchmark modules define `_clamp01`, and the benchmark controller defines
the equivalent `_clamp_score`. The observed implementations clamp a value to
`[0.0, 1.0]` after float conversion.

**Phase 1 decision:** Good low-risk consolidation candidate after the desired
behavior for `NaN`, infinities, and conversion failures is documented.

**Phase 6 decision:** Characterization confirms all six helpers share the same
observable behavior. Mechanical extraction to
`benchmark_suite/score_values.py::clamp01` is approved as the next bounded
phase. The current `NaN -> 1.0` result is preserved for extraction but flagged
for a separate correctness decision.

**Phase 7 implementation:** The six local bodies now reference
`benchmark_suite/score_values.py::clamp01` through compatible import aliases.
The conversion, non-finite, and exception contracts remain unchanged.

### CAND-004 — Optional scalar normalization

**Classification:** `D2`, with some `A` cases

**Confidence:** High that several bodies repeat; low that all boundaries should share one policy

**Priority for later review:** P0

`_optional_string` and `_optional_float` occur across DesignIR schemas,
migrations, services, readiness evaluation, host optimization, run manifests,
and the current resource-calibration schema.

There are meaningful contract differences:

- some string helpers trim whitespace and convert blank strings to `None`;
- the run-manifest helper preserves whitespace;
- some float helpers return `None` for invalid input;
- resource-calibration validation rejects invalid or non-finite values.

**Phase 1 decision:** Do not create one global helper. First define separate
policies for permissive parsing, schema validation, and migration compatibility.

**Phase 8 decision:** Four boundary contracts are now explicit. Trimmed optional
text and permissive optional float are separately approved for future neutral
extraction. Run-manifest exact-text preservation and strict resource-calibration
float validation must remain separate.

**Phase 9 implementation:** The two permissive families now live in
`src/utils/scalar_values.py`. Existing module names are compatible import
aliases. Run-manifest and resource-calibration implementations remain local and
unchanged.

### CAND-005 — Stable JSON hashing

**Classification:** `D1`/`D2`

**Confidence:** High

**Priority for later review:** P1

- `BenchmarkDataset.content_hash()`
- `DesignTaskSet.content_hash()`
- `ScoringProfile.configuration_hash`

The two content-hash methods are identical. The scoring-profile hash uses the
same stable JSON and SHA-256 pattern but intentionally removes one optional
field in a compatibility case.

**Phase 1 decision:** Candidate for a small stable-hash primitive with domain
objects retaining responsibility for selecting the hashed payload.

**Phase 10 characterization:** The compact UTF-8 JSON plus full SHA-256
mechanics are safe to extract only for exact-option callers. Payload selection,
the scoring-profile compatibility exclusion, stable-batch sanitization,
`default=str`, ASCII escaping, prefixes, and digest truncation remain explicit
domain contracts. See `STABLE_JSON_HASH_CONTRACTS.md` and ADR-0006.

**Phase 11 implementation:** The exact mechanics now live in
`src/utils/hashing.py::stable_json_sha256`. Dataset/task-set identity, scoring
configuration, benchmark result payloads, and SQLite full-payload hashes call
the primitive after selecting their own payloads. All intentional variants
remain local and unchanged.

### CAND-006 — Legacy JSON repository listing

**Classification:** `L`, `D1`

**Confidence:** High

**Priority for later review:** P2

- `app.py::_list_host_profiles`
- `app.py::_list_parameter_fit_snapshots`

The functions have the same structure and differ primarily in repository path.

**Phase 1 decision:** Record but defer. The default interface is FastAPI `/web`,
and a legacy-only refactor is not currently worth expanding the change surface.

**Phase 14 characterization:** The wrappers are exact path-only duplicates, but
their contract includes directory creation, filename ordering, broad
exception-to-empty-list fallback, and no record schema validation. A private
app-level path helper is safe only if all behavior remains unchanged. See
`LEGACY_JSON_LISTING_CONTRACT.md` and ADR-0008.

**Phase 15 implementation:** The shared fail-closed flow now lives in
`app.py::_list_json_repository_records`. Host-profile and parameter-fit entry
points remain separate thin wrappers with their original fixed paths. Record
validation and error-reporting behavior remain unchanged.

### CAND-007 — Repeated test factories

**Classification:** `T`

**Confidence:** High for the graph-confirmed pairs

**Priority for later review:** P2

Examples include repeated `_complete_design`, `_design`, `_buffer_topology`, and
`_backbone_genbank` helpers across test modules.

**Phase 1 decision:** Report only. Move a fixture to `tests/factories/` only when
it represents the same stable domain object in at least three test modules.
Keep scenario-specific fixtures local.

**CAND-007 characterization:** `_buffer_topology` has one conditional stable
subfamily across simulation-foundation, temporal-input, and sensitivity tests.
Its `copy_number` representation differs (`5` versus `5.0`) and can affect
serialized hashes, so a future extraction must preserve that choice explicitly.
The tool-adapter variant intentionally omits `copy_number`. `_complete_design`
is an exact two-module family below threshold; `_design` is a collection of
different scenarios; and `_backbone_genbank` has different sequence lengths,
features, coordinates, and parameterization in only two modules. See
`REPEATED_TEST_FACTORY_CONTRACTS.md`. No shared factory was extracted.

### CAND-008 — Service composition recurrence risk

**Classification:** Canonical boundary, not a current duplicate finding

**Confidence:** High

**Priority for documentation:** P0

`application.services.create_application_services()` constructs the application
service graph and directly reaches the major repositories and service classes.
Historical incremental work has already produced duplicate service instances,
which were later consolidated.

**Phase 1 decision:** Record this factory in the future canonical-implementation
map and require new services to be wired through it rather than instantiated in
routes or UI code.

## 6. Documentation coverage and gaps / 文件覆蓋與缺口

| Existing document | What it already covers | Maintenance gap |
| --- | --- | --- |
| `docs/architecture.md` | High-level flow, components, DesignState/DesignIR, agent/tool layers, mock and HITL boundaries | No canonical-symbol registry, ownership/change triggers, or ADR history |
| `docs/workflow.md` | Runtime sequence, inputs/outputs, repair routing, interpretation, export decisions | Describes behavior but not the files/tests that must change together |
| `src/api/README.md` | Endpoint surface, persistence, phase contracts, credentials | Does not map API contracts to Web/MCP/export consumers |
| `docs/model_assumptions.md` | Scientific assumptions and claim boundaries | Not a software change-impact guide |
| `docs/developer/MVP_TEST_PLAN.md` | Broad test gates, claim matrix, runbooks, and accumulated verification evidence | Stable strategy and historical execution records are mixed in one growing file |
| `docs/resource_competition_model_spec.md` | Current resource-model contract | New and still part of the uncommitted working slice; maintenance ownership is not yet registered |

Repository-wide searches found no dedicated architecture-decision record set,
canonical implementation map, change-impact map, or duplication policy.

## 7. Change-impact anchors for Phase 2 / 第二階段的變更影響錨點

The next documentation phase should start from these anchors:

| Capability | Canonical candidate | Downstream surfaces to map |
| --- | --- | --- |
| Application service composition | `create_application_services()` | API dependencies, Web routes, tests, repositories |
| Design v1/v2 compatibility | `migrate_design_ir_v1_to_v2()` and `design_ir_v2_to_v1_payload()` | persistence, API, Web views, exporters, migration tests |
| Candidate evaluation | `evaluate_candidate()` | research service, evaluation service, API, benchmark runner, readiness/claim docs |
| Resource calibration workflow | `run_resource_calibration_workflow()` | schemas, EvaluationService, API, Web, fixtures, model spec |
| Default UI boundary | FastAPI `/web` | templates, Web routes, API/service contracts; Streamlit remains legacy |
| Evidence and claim boundary | evidence governance plus limitations documents | public proof, exports, demos, release gates |

## 8. Phase 1 disposition / 第一階段結論

1. There is enough evidence to justify duplicate-growth controls, but not a broad
   refactor.
2. The first consolidation targets should be benchmark coercion/clamping helpers,
   after their edge-case contract is written.
3. Schema/service scalar helpers must not be merged solely by name because strict
   validation and permissive migration have different responsibilities.
4. Large scientific and orchestration functions are change-risk hotspots, not
   duplicate findings. Characterization tests and impact maps come before splits.
5. Legacy and test duplication should initially remain report-only.
6. The current resource-calibration slice should stabilize before it becomes the
   basis for new shared abstractions.

## 9. Phase 1 completion gate / 第一階段完成條件

- [x] Current working-tree boundary recorded without modifying existing work.
- [x] Structural concentration and high-impact call paths identified.
- [x] Duplicate candidates classified rather than automatically merged.
- [x] Existing documentation coverage and gaps recorded.
- [x] Canonical candidates and Phase 2 inputs identified.
- [x] No production behavior changed.

Phase 2 documentation governance was created from this baseline before automated
CI enforcement:

- [Canonical implementations](CANONICAL_IMPLEMENTATIONS.md)
- [Change-impact map](CHANGE_IMPACT_MAP.md)
- [Duplication policy](DUPLICATION_POLICY.md)
- [Architecture decision records](../adr/README.md)

The next implementation phase is report-only duplicate detection. It should use
these policies to generate a stable baseline before any CI failure rule is
enabled.

## 10. Phase 3 report-only detector / 第三階段僅報告偵測器

Phase 3 added a standard-library AST detector, a machine-readable baseline, a
validated exception registry, and focused tests. The detector reports exact
function bodies, identifier-normalized structural matches, and repeated private
helper names across modules.

The baseline also records group membership. This means a third copy added to an
existing group is reported even though the group's fingerprint and ID remain
unchanged.

Current snapshot result:

| Signal | Count |
| --- | ---: |
| Python files scanned | 141 |
| Functions and methods scanned | 1,632 |
| Exact-body candidate groups | 31 |
| Structural-body candidate groups | 12 |
| Repeated-name candidate groups | 65 |

These counts are inventory signals, not confirmed defects. Candidate findings,
new groups, and new occurrences remain non-blocking in this phase. Only invalid
configuration or incomplete parsing returns an error.

Run the comparison from the repository root:

```powershell
python -m src.scripts.check_duplicate_functions
```

See [`quality/README.md`](../../quality/README.md) for baseline refresh and JSON
report commands.

## 11. Phase 4 P0 contract disposition / 第四階段 P0 契約判定

- Boolean coercion is not a confirmed core duplicate; it contains a Cello
  boundary vocabulary difference and remains separate.
- Candidate float/int accessors are confirmed consolidation candidates with
  characterization coverage.
- No scoring or runtime behavior changed in this phase.
- The next implementation slice may mechanically extract numeric candidate
  accessors while leaving Boolean behavior untouched.

See [Candidate value contracts](CANDIDATE_VALUE_CONTRACTS.md) for the complete
edge-case table and consolidation gate.

## 12. Phase 5 numeric consolidation / 第五階段數值 helper 整併

The reviewed detector comparison showed the intended contraction:

- no new candidate group IDs;
- repeated-name groups for `_candidate_float`, `_candidate_int`, and
  `_maybe_float` disappeared;
- three exact-body groups and one structural-body group disappeared;
- the canonical `maybe_float` replaced three scorer-local members in one
  remaining exact-body family.

Current pre-refresh scan counts changed from the Phase 3 snapshot to:

| Signal | Phase 3 | Phase 5 |
| --- | ---: | ---: |
| Python files scanned | 141 | 142 |
| Functions and methods scanned | 1,632 | 1,626 |
| Exact-body candidate groups | 31 | 28 |
| Structural-body candidate groups | 12 | 11 |
| Repeated-name candidate groups | 65 | 62 |

The added file is the canonical owner; the net function count and duplicate
groups decreased. Boolean helpers were not changed. The reviewed Phase 5 state
is the new report-only baseline.

The code-graph re-index command reported success during this dirty-working-tree
phase but continued returning the pre-extraction helper definitions. Direct
source inspection, AST detection, imports, and tests are therefore the evidence
for this phase. Recheck graph freshness after the changes are committed; do not
use the stale graph snapshot to reintroduce removed helpers.

## 13. Phase 6 score-clamp disposition / 第六階段分數限制判定

- Six score-clamp implementations were confirmed behaviorally equivalent.
- Characterization covers finite values, numeric strings, booleans, both
  infinities, `NaN`, and conversion exceptions.
- Consolidation is approved, but no production helper moved in this phase.
- Non-finite hardening is intentionally separated from duplicate removal.

See [Score clamp contract](SCORE_CLAMP_CONTRACT.md) for the current behavior
table, caller boundaries, and the next extraction gate.

## 14. Phase 7 score-clamp consolidation / 第七階段分數限制整併

The reviewed detector comparison showed:

- no new group IDs or new occurrences;
- the `repeated_name:_clamp01` group disappeared;
- production files increased by one canonical owner while function count fell
  from 1,626 to 1,621;
- exact and structural group counts stayed at 28 and 11 because one-statement
  clamp bodies are below their detection threshold;
- repeated-name groups fell from 62 to 61.

All six compatibility aliases point to the same canonical function. The
reviewed Phase 7 state is the new report-only baseline. Non-finite score policy
was not changed.

## 15. Phase 8 optional-scalar disposition / 第八階段選用純量判定

- Nine trimmed optional-text helpers are behaviorally equivalent.
- Six permissive optional-float helpers are behaviorally equivalent, including
  the benchmark `maybe_float` primitive.
- Run-manifest text preservation is intentionally not equivalent.
- Resource-calibration float parsing is strict, finite-only validation and is
  intentionally not equivalent.
- No production helper moved and no runtime behavior changed in this phase.

See [Optional scalar contracts](OPTIONAL_SCALAR_CONTRACTS.md) for the boundary
matrix and future packaging gate.

## 16. Phase 9 optional-scalar consolidation / 第九階段選用純量整併

The reviewed detector comparison showed:

- no new candidate group IDs;
- repeated-name groups for `_optional_string`, `_optional_text`, and
  `_optional_float` disappeared;
- two exact-body groups disappeared;
- one neutral `optional_float` occurrence replaced five members in a remaining
  permissive-float family;
- files scanned increased from 143 to 144 while functions fell from 1,621 to
  1,608;
- exact groups fell from 28 to 26 and repeated-name groups fell from 61 to 58.

Subprocess import smoke coverage confirms the neutral module resolves through
application, benchmark, schema, and tool entry points. Strict and
preservation-oriented helpers were not changed. The reviewed Phase 9 state is
the new report-only baseline.
