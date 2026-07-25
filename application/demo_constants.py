"""Pure constants shared by the canonical demo and deterministic evaluators."""

DEMO_BASELINE_TASK_SET_ID = "exp003_design_tasks_v1"
DEMO_BASELINE_TASK_ID = "cello_a_and_not_b_gfp_v1"
DEMO_BASELINE_CLAIM = (
    "This packet is computational screening evidence for a fixed demo intent. "
    "It is not wet-lab validation and it is not an experimental protocol."
)
DEMO_BASELINE_VERILOG = (
    "module demo_a_and_not_b(input A, input B, output GFP); "
    "assign GFP = A & ~B; "
    "endmodule"
)
DEMO_BASELINE_TRUTH_TABLE = [
    {"A": 0, "B": 0, "GFP": 0},
    {"A": 0, "B": 1, "GFP": 0},
    {"A": 1, "B": 0, "GFP": 1},
    {"A": 1, "B": 1, "GFP": 0},
]
