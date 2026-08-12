"""CLI for the frozen offline software-contract validation matrix."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Sequence

# This command's entire process is offline by contract. Set the LiteLLM flag
# before importing application services so no later import can refresh pricing.
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "true"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application.offline_contract_validation import (  # noqa: E402
    run_offline_contract_validation,
)
from application.services import create_application_services  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run deterministic software-contract cases without external Cello; "
            "this is not an end-to-end agent/Cello validation."
        )
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    args = parser.parse_args(argv)

    data_dir = args.data_dir or args.output_dir / "runtime_data"
    services = create_application_services(data_dir)
    packet = run_offline_contract_validation(
        services,
        output_dir=args.output_dir,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(packet["summary"], ensure_ascii=False, indent=2))
    print(f"stable_result_hash={packet['stable_result_hash']}")
    return 0 if packet["summary"]["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
