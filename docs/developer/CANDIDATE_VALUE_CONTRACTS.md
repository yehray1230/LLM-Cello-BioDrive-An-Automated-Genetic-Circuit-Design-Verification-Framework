# Candidate value contracts
# 候選資料值轉換契約

**Status / 狀態:** Phase 5 implemented canonical contract

## 1. Scope / 範圍

This record compares the P0 Boolean and numeric helper families identified by
the maintenance baseline. It describes current behavior and the safe next
consolidation boundary; it does not change scoring behavior.

## 2. Candidate numeric accessors / 候選數值讀取

The four `_candidate_float` implementations and two `_candidate_int`
implementations currently have the same observable conversion behavior.

| Input case | Float result | Integer result |
| --- | --- | --- |
| Missing key or `None` | supplied default | supplied default |
| Numeric string, including surrounding whitespace | Python `float(...)` result | Python `int(...)` result |
| Boolean | `0.0` or `1.0` | `0` or `1` |
| Invalid string or incompatible object | supplied default | supplied default |
| `NaN` | preserved as `NaN` | supplied default |
| Infinity | preserved as infinity | currently raises `OverflowError` |
| Finite fractional number | preserved | truncated toward zero by `int(...)` |

**Decision:** implemented in `benchmark_suite/candidate_values.py`. Its
`maybe_float` name is now a compatibility alias to the neutral
`utils.scalar_values.optional_float` implementation. Benchmark controller,
functional, kinetic, temporal, and static-plausibility scorers reference these
canonical functions without parallel bodies.

The extraction preserves the table above, including non-finite behavior.
Changing that behavior requires a separate correctness decision and
caller-level regression tests.

The extraction may expose value-level `maybe_float` plus dictionary accessors,
but it must not introduce dependencies on scorers or application/UI layers.

## 3. Boolean coercion / Boolean 轉換

The helpers share basic behavior for `None`, booleans, ordinary truthy/falsy
objects, and these case-insensitive string tokens:

- true: `1`, `true`, `yes`, `y`;
- false: `0`, `false`, `no`, `n`;
- unknown strings: supplied default.

They are not one contract. The Cello adapter additionally recognizes external
mapping-status vocabulary:

- true: `mapped`, `success`, `successful`;
- false: `failed`, `unmapped`.

**Decision:** do not directly consolidate the two Boolean helpers. Keep Cello
status vocabulary at the Cello boundary. A future shared primitive is allowed
only if the accepted token profiles are explicit and the Cello adapter remains
responsible for selecting its external-status profile.

This corrects the Phase 1 assumption that the two helpers were behaviorally
identical.

Phase 12 expanded this inventory beyond benchmark candidates. The authoritative
four-family decision now lives in `BOOLEAN_COERCION_CONTRACTS.md` and ADR-0007;
candidate, state, and ODE defaulted value coercion form the only exact
consolidation family.

## 4. Characterization coverage / 特性測試

`tests/test_candidate_value_contracts.py` locks the current edge cases across
every implementation. These tests are intentionally contract-focused so a
later mechanical extraction can prove that it did not change evaluation
semantics.

## 5. Maintenance rule / 維護規則

After numeric extraction:

- add benchmark-level permissive numeric conversion only in
  `benchmark_suite/candidate_values.py`;
- keep strict schema validation and external-tool adapters outside this helper;
- rerun characterization and affected scorer regressions after contract edits;
- treat changes to Boolean tokens or non-finite numeric behavior as separate
  contract changes;
- leave Boolean helpers separate unless an explicit token-profile design is
  adopted.
