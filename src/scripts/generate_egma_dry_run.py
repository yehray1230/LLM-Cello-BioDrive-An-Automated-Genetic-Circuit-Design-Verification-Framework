from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmark_suite.egma_generator import (
    DEFAULT_DRY_RUN_SEED,
    validate_egma_dry_run_bundle,
    write_egma_dry_run_bundle,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate the visible non-confirmatory EGMA allocation dry run. "
            "This command does not create the future sealed benchmark."
        )
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_DRY_RUN_SEED)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    bundle = write_egma_dry_run_bundle(args.output, seed=args.seed)
    errors = validate_egma_dry_run_bundle(bundle)
    summary = {
        "status": "valid" if not errors else "invalid",
        "path": str(args.output),
        "content_hash": bundle["content_hash"],
        "task_count": len(bundle["tasks"]),
        "group_count": len(bundle["groups"]),
        "confirmatory_eligible": bundle["confirmatory_eligible"],
        "sealed_materialized": bundle["sealed_materialized"],
        "errors": errors,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
