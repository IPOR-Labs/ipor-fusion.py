#!/usr/bin/env python3
"""Regenerate `src/ipor_fusion/data/ipor_abi_addresses.json` from an ipor-abi checkout.

The SDK ships the Fusion deployment registry inside the wheel so that
`ipor_fusion.addresses` works offline and deterministically. Run this after
ipor-abi changes and commit the result:

    git clone https://github.com/IPOR-Labs/ipor-abi /tmp/ipor-abi
    python scripts/sync_ipor_abi_addresses.py /tmp/ipor-abi

Only `mainnet/mainnet-<chain>-fusion/addresses.json` files are imported; the
chain id for each directory comes from `scripts/shared_utils.py` in ipor-abi.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from web3 import Web3

OUT = (
    Path(__file__).resolve().parents[1] / "src/ipor_fusion/data/ipor_abi_addresses.json"
)
REPOSITORY = "https://github.com/IPOR-Labs/ipor-abi"


def _chain_ids(abi_root: Path) -> dict[str, int]:
    text = (abi_root / "scripts/shared_utils.py").read_text(encoding="utf-8")
    block = re.search(r"CHAIN_IDS\s*=\s*\{(.*?)\}", text, re.DOTALL)
    if block is None:
        raise SystemExit("CHAIN_IDS not found in ipor-abi scripts/shared_utils.py")
    return {
        name: int(cid)
        for name, cid in re.findall(r'"([a-z0-9-]+)"\s*:\s*"(\d+)"', block.group(1))
    }


def main(abi_root: Path) -> None:
    chain_ids = _chain_ids(abi_root)
    git = shutil.which("git") or "git"
    commit = subprocess.run(  # noqa: S603 - fixed argv, path from the CLI user
        [git, "-C", str(abi_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    chains: dict[str, dict] = {}
    for path in sorted(abi_root.glob("mainnet/mainnet-*-fusion/addresses.json")):
        chain = path.parent.name.removeprefix("mainnet-").removesuffix("-fusion")
        if chain not in chain_ids:
            print(
                f"skip {path.parent.name}: no chain id in shared_utils.py",
                file=sys.stderr,
            )
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        contracts = {
            name: Web3.to_checksum_address(address)
            for name, address in sorted(raw.items())
            if isinstance(address, str) and address.startswith("0x")
        }
        chains[str(chain_ids[chain])] = {
            "name": chain,
            "directory": str(path.parent.relative_to(abi_root)),
            "contracts": contracts,
        }
    payload = {
        "source": {
            "repository": REPOSITORY,
            "commit": commit,
            "synced_at": datetime.now(timezone.utc).date().isoformat(),
        },
        "chains": dict(sorted(chains.items(), key=lambda kv: int(kv[0]))),
    }
    OUT.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(Path.cwd())}: {len(chains)} chains @ {commit[:12]}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]).resolve())
