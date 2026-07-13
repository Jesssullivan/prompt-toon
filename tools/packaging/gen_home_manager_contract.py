#!/usr/bin/env python3
"""Generate the deterministic C4d Home Manager consumption contract."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prompt_toon.adoption import (  # noqa: E402
    CONTRACT_PATH,
    build_home_manager_contract,
    render_contract,
)

import gen_manifest  # noqa: E402

CONTRACT_FILE = ROOT / CONTRACT_PATH


def rendered_contract() -> str:
    version = gen_manifest.version_from_init()
    policy_digests = gen_manifest.policy_digest_map()
    return render_contract(
        build_home_manager_contract(version=version, policy_digests=policy_digests)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate and byte-diff against packaging/home-manager.json",
    )
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args()

    rendered = rendered_contract()
    if args.check:
        rendered_bytes = rendered.encode("utf-8")
        if not CONTRACT_FILE.exists():
            sys.stderr.write(
                "gen_home_manager_contract: packaging/home-manager.json is missing\n"
            )
            return 1
        committed = CONTRACT_FILE.read_bytes()
        if committed != rendered_bytes:
            sys.stderr.write(
                "gen_home_manager_contract: packaging/home-manager.json has drifted; "
                "run tools/packaging/gen_home_manager_contract.py and commit the result\n"
            )
            return 1
        print("home-manager contract: no drift")
        return 0

    if args.stdout:
        sys.stdout.write(rendered)
        return 0

    CONTRACT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT_FILE.write_text(rendered, encoding="utf-8")
    print(f"wrote {CONTRACT_FILE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
