# Repeated Test Factory Characterization

**Status:** CAND-007 characterization; no shared factory extracted

## 1. Decision rule

A helper may move to `tests/factories/` only when it builds the same stable
domain object in at least three test modules. Scenario-specific fixtures stay
local, and a shared name is not evidence of shared semantics.

## 2. Factory matrix

| Name | Modules | Domain object and defaults | Nested payload and mutation model | Assertions and scenario meaning | Decision |
| --- | ---: | --- | --- | --- | --- |
| `_complete_design` | 2 | `DesignIR` produced from the same NOT-buffer Verilog, complete sequences, `test_fixture` confidence, and `sequences=complete`; only `design_id` differs | Each call creates a fresh design and fresh nested parts/status mapping; exporter and plasmid tests deliberately mutate part sequences locally | Both need an assembly-complete v1 design, but one asserts BOM/GenBank/SBOL export contracts and the other asserts backbone-specific plasmid export behavior | Exact stable family below the three-module threshold; keep local |
| `_design` | 6 | All return `DesignIRV2`, but outputs, host context, part count/sequences/evidence, constructs, plasmids, orientation, backbone, and parameterization differ | Each constructor creates a fresh object graph; plasmid tests mutate evidence, sequence, and nested construct orientation without leakage | Readiness, sequence analysis/optimization, host optimization, plasmid assembly, and assembly planning assert different scenario contracts | Unrelated same-name scenario helpers; do not merge |
| `_buffer_topology` | 4 | Three modules model the same buffer topology with `copy_number=5`; two use integer `5`, sensitivity uses float `5.0`; the tool-adapter version intentionally omits `copy_number` | Every call creates a fresh dictionary, truth-table list, and row mappings; tests also create changed copies or mutate downstream `DesignIRV2` objects | Simulation/temporal tests assert simulation and hash behavior; sensitivity asserts sweeps; adapter tests only need minimal Verilog/truth-table transport | Conditional `tests/factories/` candidate for the three copy-number modules only; representation must remain explicit. Keep adapter fixture local |
| `_backbone_genbank` | 2 | Both return circular GenBank text, but IDs, sequence sources/lengths, feature coordinates/labels, and optional custom-sequence behavior differ | Returned strings are immutable; each call constructs a fresh `SeqRecord`. Planner coordinates require a 260-base sequence, while plasmid-tool coordinates target a 100-base record | Plasmid tools assert direct insertion and feature shifts; assembly planner asserts method selection, legal windows, and overlap/restriction scenarios | Semantically different and below threshold; keep local |

## 3. Representation and isolation risks

- Python compares `5` and `5.0` as equal, but JSON serialization and therefore
  content-addressed hashes can distinguish them. A future buffer-topology
  factory must accept or otherwise preserve the caller's explicit numeric
  representation; changing all callers to one literal is not a mechanical
  cleanup.
- The nested truth-table rows and all design object graphs must be newly
  allocated per call. A module-level mutable template or shallow copy would let
  scenario mutations leak between tests.
- A generic `DesignIRV2` builder would hide the features under test. The six
  `_design` helpers are scenario declarations, not interchangeable defaults.
- GenBank feature coordinates are coupled to backbone length and insertion
  windows. Sharing only the serialization boilerplate would not create a stable
  domain factory and would obscure those coordinate contracts.

## 4. Go/no-go disposition

**GO, with a narrow gate:** a later bounded phase may extract a buffer-topology
factory for `test_simulation_foundation.py`, `test_temporal_inputs.py`, and
`test_sensitivity_analysis.py` only. It must preserve fresh nested allocation
and make the `copy_number` representation explicit so hash-sensitive tests do
not change accidentally.

**NO-GO:** do not extract `_complete_design`, any general `_design`, the
tool-adapter `_buffer_topology`, or `_backbone_genbank` under the current
evidence. None satisfies both stable semantics and the three-module threshold.

No production code or test factory was changed during this characterization.

## 5. Verification anchors

- `tests/test_repeated_test_factory_contracts.py`
- `tests/test_design_exporters.py`
- `tests/test_plasmid_assembler.py`
- `tests/test_assembly_planner.py`
- `tests/test_readiness_evaluator.py`
- `tests/test_sequence_optimization_phase1.py`
- `tests/test_plasmid_tools.py`
- `tests/test_host_optimization_phase2.py`
- `tests/test_sequence_analysis.py`
- `tests/test_simulation_foundation.py`
- `tests/test_temporal_inputs.py`
- `tests/test_sensitivity_analysis.py`
- `tests/test_tool_adapters_phase9.py`
