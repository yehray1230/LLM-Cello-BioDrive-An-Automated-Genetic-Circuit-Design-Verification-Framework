# Score clamp contract
# 分數範圍限制契約

**Status / 狀態:** Phase 7 implemented canonical contract

## 1. Scope / 範圍

This record compares `_clamp_score` in the benchmark controller with the five
benchmark `_clamp01` helpers used by Cello, functional, semantic, static
plausibility, and temporal evaluation. It records current behavior without
changing score semantics.

## 2. Current contract / 現有契約

All six helpers currently evaluate the equivalent expression:

```python
max(0.0, min(1.0, float(value)))
```

| Input | Current result |
| --- | --- |
| Finite value below `0.0` | `0.0` |
| Finite value from `0.0` through `1.0` | converted float |
| Finite value above `1.0` | `1.0` |
| Numeric string | converted and clamped |
| Boolean | `0.0` or `1.0` |
| Negative infinity | `0.0` |
| Positive infinity | `1.0` |
| `NaN` | `1.0` |
| `None` or incompatible object | propagates `TypeError` |
| Invalid numeric string | propagates `ValueError` |

The `NaN` result follows from Python's ordered `min`/`max` behavior; it is not
evidence that a non-finite score is scientifically valid or high quality.

## 3. Caller boundaries / 呼叫邊界

| Area | Uses the clamp for |
| --- | --- |
| Benchmark controller | component, dimension, weighted, and optional scores |
| Cello evaluator | assignment, orthogonality, normalized score, and toxicity-derived score |
| Functional scorer | aggregate logic score, fold-change score, and margin score |
| Semantic evaluator | parsed semantic-faithfulness score |
| Static plausibility evaluator | final structural plausibility score |
| Temporal scorer | final temporal score |

These are all computational benchmark scores with the same `[0.0, 1.0]`
output contract. No transport, schema-validation, or external status vocabulary
difference was found inside the clamp operation itself.

## 4. Decision / 判定

Mechanical consolidation is implemented in
`benchmark_suite/score_values.py::clamp01`. The benchmark controller and five
evaluators retain their previous private names as import aliases, so existing
call sites remain compatible without retaining parallel function bodies.

The first extraction must preserve the table above. In particular, changing
`NaN` from `1.0` to an error, default, or lower score is a separate correctness
change that requires caller-level policy and regression coverage. It must not be
hidden inside the duplicate-removal patch.

## 5. Maintenance rule / 維護規則

- add benchmark score clamping behavior only in `score_values.py`;
- keep strict schema validation and evidence/readiness interpretation outside
  the clamp primitive;
- run this characterization suite and affected scorer/controller regressions
  after contract edits;
- open a separate decision if non-finite scores should be rejected or
  conservatively downgraded.
