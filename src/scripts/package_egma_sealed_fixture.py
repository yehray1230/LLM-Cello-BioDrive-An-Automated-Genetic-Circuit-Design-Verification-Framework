from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from benchmark_suite.egma_sealing import (
    read_external_key,
    scan_for_secret_material,
    seal_synthetic_payload,
    synthetic_throwaway_payload,
    validate_sealed_manifest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Package only the EGMA synthetic throwaway sealing fixture. "
            "This command refuses Slice 4 visible rows and does not create a "
            "confirmatory holdout."
        )
    )
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument(
        "--key-file",
        type=Path,
        required=True,
        help="Path to an existing 32-byte key outside --package-dir.",
    )
    parser.add_argument(
        "--package-id",
        default="egma-synthetic-sealed-fixture-v1",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    key = read_external_key(args.key_file, package_dir=args.package_dir)
    manifest = seal_synthetic_payload(
        synthetic_throwaway_payload(),
        key=key,
        package_dir=args.package_dir,
        package_id=args.package_id,
    )
    errors = validate_sealed_manifest(manifest)
    secret_hits = scan_for_secret_material(
        [args.package_dir],
        secret=key,
        additional_blobs={
            "environment_snapshot": json.dumps(dict(os.environ), sort_keys=True),
        },
    )
    summary = {
        "status": "valid" if not errors and not secret_hits else "invalid",
        "package_dir": str(args.package_dir),
        "package_id": manifest["package_id"],
        "manifest_sha256": manifest["commitments"]["manifest_sha256"],
        "ciphertext_sha256": manifest["cipher"]["ciphertext_sha256"],
        "fixture_only": manifest["fixture_only"],
        "confirmatory_eligible": manifest["confirmatory_eligible"],
        "sealed_materialized": manifest["sealed_materialized"],
        "provider_calls": 0,
        "paid_cost_usd": 0.0,
        "secret_hits": secret_hits,
        "errors": errors,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["status"] == "valid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
