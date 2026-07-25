# Architecture Decision Records
# 架構決策紀錄

Architecture Decision Records (ADRs) preserve why a boundary exists so later
implementation work does not accidentally recreate a competing architecture.

ADR 記錄架構邊界存在的原因，避免後續實作因只看到局部程式而建立另一套競爭架構。

## Status values / 狀態

- `Proposed`: under review; not yet binding.
- `Accepted`: current architectural policy.
- `Superseded`: replaced by a named ADR; retained for history.
- `Deprecated`: still present for compatibility but must not receive new domain
  behavior.

## Index / 索引

| ADR | Status | Decision |
| --- | --- | --- |
| [ADR-0001](0001-fastapi-web-is-the-default-interface.md) | Accepted | FastAPI `/web` is the default interface; Streamlit is maintenance-only |
| [ADR-0002](0002-design-representation-and-migration-boundaries.md) | Accepted | Separate search state, DesignIR reader/export, DesignIR v2 persistence, and migration ownership |
| [ADR-0003](0003-application-service-composition-and-persistence.md) | Accepted | Use one application composition root and explicit persistence owners |
| [ADR-0004](0004-computational-evaluation-and-evidence-boundaries.md) | Accepted | Separate computational evaluation, readiness, external-tool evidence, and public claim governance |
| [ADR-0005](0005-optional-scalar-normalization-boundaries.md) | Accepted | Centralize permissive scalar conversion while preserving provenance and strict validation boundaries |

## Creating or changing an ADR / 新增或修改 ADR

Use the next four-digit number and include:

1. title, date, and status;
2. context and forces;
3. decision;
4. consequences;
5. implementation anchors;
6. superseded ADR, when applicable.

Amend wording when clarifying the same decision. Create a new ADR when ownership,
compatibility direction, default interface, or trust boundary changes.

## Related maintenance documents / 相關維護文件

- [Canonical implementations](../developer/CANONICAL_IMPLEMENTATIONS.md)
- [Change-impact map](../developer/CHANGE_IMPACT_MAP.md)
- [Duplication policy](../developer/DUPLICATION_POLICY.md)
- [Codebase maintenance baseline](../developer/CODEBASE_MAINTENANCE_BASELINE.md)
