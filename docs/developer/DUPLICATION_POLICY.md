# Duplication Policy
# 重複實作治理政策

**Status / 狀態:** Active policy; automated enforcement not yet enabled

## 1. Goal / 目標

Prevent new parallel implementations from silently expanding maintenance cost
while preserving legitimate adapters, compatibility code, local test scenarios,
and scientific clarity.

本政策的目標是阻止新的平行實作在不易察覺的情況下增加維護成本，同時保留合理的
adapter、相容性程式、局部測試情境與清楚的科學實作。

The goal is not zero duplication. The goal is one owner for each behavior whose
semantics must stay consistent.

## 2. Scope / 適用範圍

| Area | Policy |
| --- | --- |
| `application/`, `src/`, `benchmark_suite/`, production root modules | Exact or behaviorally equivalent new implementations require review and a canonical-owner decision |
| API/Web/MCP adapters | Similar shape is allowed when transport contracts differ; domain decisions must delegate to canonical code |
| Schema and migration code | Similar names are not enough to merge; strict validation and permissive compatibility parsing may remain separate |
| `app.py` legacy Streamlit | Report duplication, but do not require broad cleanup unless correctness or the default Web interface is affected |
| `tests/` | Report repeated factories; centralize only stable domain objects used in at least three modules |
| Generated files and fixtures | Exclude from clone enforcement; validate through their generator/schema contract |

## 3. Classification / 分類

Use the baseline codes:

- `D1`: confirmed duplicate core behavior;
- `D2`: likely shared-helper family with contract differences;
- `A`: boundary/version adapter;
- `L`: legacy-path duplication;
- `T`: test helper/fixture duplication;
- `H`: size or complexity hotspot, not itself duplication.

Every automated candidate remains a candidate until direct source and contract
inspection confirms its class.

## 4. Before adding a helper / 新增 helper 前

1. Search the code graph by intent, not only the proposed name.
2. Search for common behavior words such as parse, normalize, validate, migrate,
   clamp, hash, serialize, list, load, save, and convert.
3. Check `CANONICAL_IMPLEMENTATIONS.md`.
4. Compare null, invalid, boundary, version, and error behavior.
5. Choose one:
   - reuse the canonical function;
   - extend it without weakening existing callers;
   - add a thin adapter;
   - add a separate implementation with a documented contract difference.

Copying a private helper into another module because importing it feels awkward
is a signal to review module ownership, not permission to duplicate it.

## 5. Review rules / 審查規則

| Finding | Current Phase 2 treatment | Future report-only/CI treatment |
| --- | --- | --- |
| New exact production clone | Must be resolved or justified in review | CI failure after a stable baseline exists |
| High structural similarity | Manual contract comparison | Warning/report |
| Same helper name across modules | Manual behavior comparison | Report, not automatic failure |
| Adapter duplication | Allowed with explicit boundary difference | Allow-list entry only if detector cannot distinguish it |
| Legacy duplication | Defer unless correctness changes | Report only |
| Test fixture duplication | Keep local unless broadly stable | Report only |
| Historical accepted duplicate | Document reason and removal/review trigger | Machine-readable exception in Phase 3 |

Phase 3 uses `quality/duplication_baseline.json` and
`quality/duplication_exceptions.json`. JSON keeps the maintenance command on the
Python standard library while still validating every exception field. The
detector is report-only: candidate findings and baseline drift do not fail CI.

Run the current comparison with:

```powershell
python -m src.scripts.check_duplicate_functions
```

## 6. Exception requirements / 例外要求

An intentional duplicate must record:

- both symbols or paths;
- classification (`A`, `L`, `T`, or justified `D2`);
- the contract/boundary difference;
- why a shared implementation would be harmful or premature;
- the canonical implementation, if one exists;
- a concrete review trigger, such as v1 removal or legacy UI retirement.

An exception must not use “easier”, “temporary”, or “different module” as its
only reason.

## 7. Safe consolidation gate / 安全合併條件

Do not consolidate until all are true:

- [ ] Inputs, outputs, exceptions, null handling, and side effects are compared.
- [ ] Callers and downstream tests are identified.
- [ ] Characterization tests cover current behavior.
- [ ] The shared location has a clear architectural owner.
- [ ] The change does not create a low-level utility that depends on a higher
      application or UI layer.
- [ ] Compatibility and scientific interpretation remain unchanged.
- [ ] The maintenance baseline and canonical map are updated.

## 8. Initial candidates / 初始候選

The authoritative initial candidate list remains in
`CODEBASE_MAINTENANCE_BASELINE.md`. Phase 2 does not refactor those candidates.
The first future contract decisions should cover:

1. Boolean coercion;
2. candidate float/int accessors;
3. score clamping;
4. optional scalar policies by boundary;
5. stable JSON hashing.

## 9. Related documents / 相關文件

- [Codebase maintenance baseline](CODEBASE_MAINTENANCE_BASELINE.md)
- [Canonical implementations](CANONICAL_IMPLEMENTATIONS.md)
- [Change-impact map](CHANGE_IMPACT_MAP.md)
- [Architecture decision records](../adr/README.md)
- [Duplicate-function quality records](../../quality/README.md)
