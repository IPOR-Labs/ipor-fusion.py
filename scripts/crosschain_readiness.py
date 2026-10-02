#!/usr/bin/env python3
"""Read-only readiness report for native USDC over CCIP between Arbitrum (hub)
and HyperEVM (spoke).

One pinned block per chain, read through the SDK wrappers only; nothing is
signed, sent or simulated. The report separates what Chainlink controls
(``transport_ready``: the USDC token lane in both directions) from what IPOR
controls (``factories_ready``: the USDC asset and route configuration of the
crosschain factories), and lists per-chain re-pin candidates only when both
real token lanes serve the pair. Block heights of different chains are never
compared.

Provider URLs come from ``ARBITRUM_PROVIDER_URL`` and ``HYPEREVM_PROVIDER_URL``
(environment or ``.env``) and are never printed; errors are reported by stage
and exception class only, because provider errors embed the URL.

Exit codes: 0 when the observation is complete (ready or blocked), 2 for a
configuration problem (missing URL, unreachable RPC, wrong chain id), 3 when
the observation is incomplete (a read failed or a block changed under it). A
nonzero exit never reports ``ready``.

    uv run python scripts/crosschain_readiness.py --output readiness-report.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from dotenv import load_dotenv
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector, keccak
from web3 import Web3
from web3.types import RPCEndpoint

from ipor_fusion import ERC20, Web3Context, ccip_token_lane
from ipor_fusion.core.contract import Call
from ipor_fusion.crosschain import CcipCrosschainFactory
from ipor_fusion.types import ChainId

SCHEMA = "ipor-fusion.crosschain-readiness/2"
EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_INCOMPLETE = 3

USDC_SYMBOL = "USDC"
USDC_ASSET_ID = keccak(text=USDC_SYMBOL)
USDC_DECIMALS = 6
#: The transport canary's asset: Chainlink's BurnMint test token, 18 decimals.
TESTTR_SYMBOL = "TESTTR"
TESTTR_ASSET_ID = keccak(text=TESTTR_SYMBOL)
TESTTR_DECIMALS = 18
SUPPORTED_ON_RAMP_PREFIX = "OnRamp 2."
RPC_TIMEOUT_S = 30.0
#: Two snapshots taken further apart than this are not offered as a pin pair.
MAX_SNAPSHOT_SKEW_S = 900
#: The factory generation the SDK wrappers were verified against.
EXPECTED_FACTORY_INTERFACE_VERSION = 1
#: HyperEVM's small blocks; Chainlink's executors on this lane send into them
#: (`eth_usingBigBlocks` false, observed 2026-10-02), so a delivery whose gas
#: limit exceeds this is never executed by them and needs manual execution
#: (`OffRamp.execute` from an address on big blocks). The route's message gas
#: limit applies to every non-token message: commands, recall requests and
#: deployment tickets alike.
HYPEREVM_SMALL_BLOCK_GAS_LIMIT = 3_000_000
#: Receiver-side gas of a dispatcher creation on HyperEVM (v2 simulation,
#: 2026-10-02); a message gas limit below this leaves tickets to manual
#: execution with a higher `gasLimitOverride` whatever the executor does.
DISPATCHER_DEPLOYMENT_GAS = 5_700_000

#: The pilot-v2 CCIP crosschain factory (contracts source ``810e260`` plus the
#: three 5-minute patches), at one CREATE3 address on both chains. The first
#: pilot pair (``0x3a745…``) keeps the old recall accounting and is retired
#: from this probe.
FACTORY = Web3.to_checksum_address("0x3BB74623A229Ff463bDe6B5b267B7c8d9086fd4b")
#: keccak of the creation codes the pilot-v2 factories store; a different value
#: means another build at this address.
EXPECTED_CREATION_CODE_HASHES = {
    "executor": "0x11e28748e1cda094e2b80620b85bfb01e008172a69f2592be72493caad285cd1",
    "dispatcher": "0xcc2edfa5791f504e730114ed80fcd535a927e5e11e53045a2584bb1e161d5e03",
}
#: The canary's creator EOA: `createExecutor` sender on the hub, so it must be
#: an allowed creator, hold native gas, and sit on HyperEVM big blocks (its
#: vault clone there needs ~9 M gas).
CREATOR = Web3.to_checksum_address("0x533ac556E288625B267bD71B7928E0a8B46DcE82")


@dataclass(frozen=True)
class ChainSpec:
    """Public on-chain identities of one side of the pair."""

    name: str
    chain_id: ChainId
    chain_selector: int
    env_var: str
    router: ChecksumAddress
    usdc: ChecksumAddress
    usdc_usd_feed: ChecksumAddress
    testtr: ChecksumAddress


ARBITRUM = ChainSpec(
    name="arbitrum",
    chain_id=ChainId(42161),
    chain_selector=4949039107694359620,
    env_var="ARBITRUM_PROVIDER_URL",
    router=Web3.to_checksum_address("0x141fa059441E0ca23ce184B6A78bafD2A517DdE8"),
    usdc=Web3.to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831"),
    # Chainlink data feed "USDC / USD" on Arbitrum One.
    usdc_usd_feed=Web3.to_checksum_address(
        "0x50834F3163758fcC1Df9973b6e91f0F0F0434aD3"
    ),
    testtr=Web3.to_checksum_address("0x83cB78b9009d48C57F29A453dd5bc774b1545682"),
)
HYPEREVM = ChainSpec(
    name="hyperevm",
    chain_id=ChainId(999),
    chain_selector=2442541497099098535,
    env_var="HYPEREVM_PROVIDER_URL",
    router=Web3.to_checksum_address("0x13b3332b66389B1467CA6eBd6fa79775CCeF65ec"),
    usdc=Web3.to_checksum_address("0xb88339CB7199b77E23DB6E890353E22632Ba630f"),
    # Chainlink data feed "USDC / USD" on HyperEVM (hyperliquid-mainnet).
    usdc_usd_feed=Web3.to_checksum_address(
        "0xA0Adc43ce7AfE3EE7d7eac3C994E178D0620223B"
    ),
    testtr=Web3.to_checksum_address("0x3DAc7a0294B6399468F908A7bB2B0c7f15ae71A6"),
)
HUB, SPOKE = ARBITRUM, HYPEREVM
CHAINS: tuple[ChainSpec, ...] = (HUB, SPOKE)

#: Facts about the rehearsal that must not be mistaken for live state.
SIMULATION_GOVERNANCE = {
    "factory_asset_enablement": (
        "IPOR-owned governance: the factory OWNER schedules and, after CONFIG_DELAY, "
        "executes assetConfig(keccak('USDC')) on each chain. The pinned test suite "
        "simulates those two calls; this report only ever states the live value."
    ),
    "token_delivery": (
        "The simulated lifecycle credits USDC from a storage-funded synthetic source "
        "and impersonates the CCIP Router on delivery; live delivery depends on the "
        "token lanes reported under transport_ready."
    ),
}


class ReadinessConfigError(Exception):
    """A problem with the environment, not with the chains."""


class BlockNumberMismatch(Exception):
    """The provider answered a block request with a different block number."""


def _view(
    ctx: Web3Context, to: ChecksumAddress, signature: str, output_types: list[str]
) -> Any:
    return Call(
        to=to,
        data=function_signature_to_4byte_selector(signature),
        output_types=output_types,
        ctx=ctx,
    ).call()


def _block_facts(block: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "number": int(block["number"]),
        "hash": "0x" + bytes(block["hash"]).hex(),
        "timestamp": int(block["timestamp"]),
    }


def _read_token(ctx: Web3Context, spec: ChainSpec) -> dict[str, Any]:
    token = ERC20(ctx, spec.usdc)
    symbol = token.symbol().call()
    decimals = int(token.decimals().call())
    return {
        "address": spec.usdc,
        "symbol": symbol,
        "decimals": decimals,
        "is_native_usdc": symbol == USDC_SYMBOL and decimals == USDC_DECIMALS,
    }


def _read_lane(
    ctx: Web3Context,
    spec: ChainSpec,
    peer: ChainSpec,
    token: ChecksumAddress | None = None,
) -> dict[str, Any]:
    lane = ccip_token_lane(ctx, spec.router, token or spec.usdc, peer.chain_selector)
    on_ramp_supported = bool(
        lane.on_ramp_version
        and lane.on_ramp_version.startswith(SUPPORTED_ON_RAMP_PREFIX)
    )
    return {
        "direction": f"{spec.name}->{peer.name}",
        "router": spec.router,
        "destination_selector": peer.chain_selector,
        "message_lane": lane.message_lane,
        "on_ramp": lane.on_ramp,
        "on_ramp_version": lane.on_ramp_version,
        "on_ramp_supported": on_ramp_supported,
        "pool": lane.pool,
        "pool_version": lane.pool_version,
        "token_lane": lane.token_lane,
        "ready": lane.message_lane and on_ramp_supported and lane.token_lane,
    }


#: Human text for each factory gate, keyed like ``factory["gates"]``.
FACTORY_GATE_TEXT = {
    "interface_version_supported": (
        f"factory interface version is not {EXPECTED_FACTORY_INTERFACE_VERSION}"
    ),
    "router_matches": "factory CCIP_ROUTER differs from the chain's Router",
    "creation_codes_configured": "factory creation codes not configured",
    "creator_authorized": "creation is restricted and the canary creator is not allowed",
    "creation_codes_expected": "stored creation codes are not the pilot-v2 build",
    "creator_big_blocks": "the canary creator is not on HyperEVM big blocks",
    "usdc_asset_ready": "USDC asset not enabled",
    "route_to_peer_ready": "route to peer not enabled",
}


def _using_big_blocks(ctx: Web3Context, address: ChecksumAddress) -> bool:
    """HyperEVM's ``eth_usingBigBlocks``: whether ``address`` sends into the
    30 M-gas big blocks."""
    response = ctx.web3.provider.make_request(
        RPCEndpoint("eth_usingBigBlocks"), [address]
    )
    if "result" not in response:
        raise RuntimeError("eth_usingBigBlocks unavailable")
    return bool(response["result"])


def _read_factory(ctx: Web3Context, spec: ChainSpec, peer: ChainSpec) -> dict[str, Any]:
    """The factory's USDC asset, its route to ``peer``, the creation gates and
    the canary creator's standing, each read once; ``gates`` holds every
    boolean ``ready`` is made of."""
    factory = CcipCrosschainFactory(ctx, FACTORY)
    route = factory.ccip_route(peer.chain_id).call()
    token, shared_decimals, enabled = factory.asset_config(USDC_ASSET_ID).call()
    testtr_token, testtr_decimals, testtr_enabled = factory.asset_config(
        TESTTR_ASSET_ID
    ).call()
    testtr_ready = bool(
        testtr_enabled
        and testtr_token == spec.testtr
        and testtr_decimals == TESTTR_DECIMALS
    )
    peer_chain_id = int(factory.chain_id_of_selector(peer.chain_selector).call())
    owner = _view(ctx, FACTORY, "OWNER()", ["address"])
    router = factory.ccip_router().call()
    restricted = bool(factory.creation_restricted().call())
    creator_allowed = bool(factory.is_allowed_creator(CREATOR).call())
    creation_code_hashes = {
        "executor": "0x" + bytes(factory.executor_creation_code_hash().call()).hex(),
        "dispatcher": "0x"
        + bytes(factory.dispatcher_creation_code_hash().call()).hex(),
    }
    creator: dict[str, Any] = {
        "address": CREATOR,
        "is_allowed_creator": creator_allowed,
        "native_balance_wei": int(
            ctx.web3.eth.get_balance(CREATOR, block_identifier=ctx.default_block)
        ),
    }
    asset_ready = bool(
        enabled and token == spec.usdc and shared_decimals == USDC_DECIMALS
    )
    route_ready = bool(
        route.enabled
        and route.chain_selector == peer.chain_selector
        and peer_chain_id == peer.chain_id
        and route.peer == FACTORY
    )
    interface_version = int(factory.factory_interface_version().call())
    gates = {
        "interface_version_supported": (
            interface_version == EXPECTED_FACTORY_INTERFACE_VERSION
        ),
        "router_matches": router == spec.router,
        "creation_codes_configured": bool(factory.creation_codes_configured().call()),
        "creation_codes_expected": creation_code_hashes
        == EXPECTED_CREATION_CODE_HASHES,
        "creator_authorized": (not restricted) or creator_allowed,
        "usdc_asset_ready": asset_ready,
        "route_to_peer_ready": route_ready,
    }
    if spec is SPOKE:
        creator["using_big_blocks"] = _using_big_blocks(ctx, CREATOR)
        gates["creator_big_blocks"] = creator["using_big_blocks"]
    return {
        "address": FACTORY,
        "interface_version": interface_version,
        "owner": owner,
        "creation_restricted": restricted,
        "creation_code_hashes": creation_code_hashes,
        "creator": creator,
        "config_delay_seconds": int(_view(ctx, FACTORY, "CONFIG_DELAY()", ["uint256"])),
        "ccip_router": router,
        "usdc_asset": {
            "asset_id": "0x" + USDC_ASSET_ID.hex(),
            "token": token,
            "shared_decimals": int(shared_decimals),
            "enabled": bool(enabled),
            "ready": asset_ready,
        },
        "testtr_asset": {
            "asset_id": "0x" + TESTTR_ASSET_ID.hex(),
            "token": testtr_token,
            "shared_decimals": int(testtr_decimals),
            "enabled": bool(testtr_enabled),
            "ready": testtr_ready,
        },
        "route_to_peer": {
            "peer_chain_id": int(peer.chain_id),
            "enabled": route.enabled,
            "chain_selector": route.chain_selector,
            "peer": route.peer,
            "peer_is_factory": route.peer == FACTORY,
            "fee_token": route.fee_token,
            "message_gas_limit": route.message_gas_limit,
            "token_gas_limit": route.token_gas_limit,
            "max_fee": route.max_fee,
            "selector_maps_to_peer": peer_chain_id == int(peer.chain_id),
            "ready": route_ready,
            # The small-block constraint is on deliveries INTO HyperEVM: this
            # route's messages are executed on the peer, so it binds the hub
            # factory's route to the spoke, not the spoke's route back.
            "manual_execution_required": (
                peer is SPOKE
                and route.message_gas_limit > HYPEREVM_SMALL_BLOCK_GAS_LIMIT
            ),
            "dispatcher_deployment_manual": (
                peer is SPOKE
                and (
                    route.message_gas_limit > HYPEREVM_SMALL_BLOCK_GAS_LIMIT
                    or route.message_gas_limit < DISPATCHER_DEPLOYMENT_GAS
                )
            ),
        },
        "gates": gates,
        "ready": all(gates.values()),
    }


def _read_feed(
    ctx: Web3Context, spec: ChainSpec, block_timestamp: int
) -> dict[str, Any]:
    feed = spec.usdc_usd_feed
    _, answer, _, updated_at, _ = _view(
        ctx,
        feed,
        "latestRoundData()",
        ["uint80", "int256", "uint256", "uint256", "uint80"],
    )
    return {
        "address": feed,
        "description": _view(ctx, feed, "description()", ["string"]),
        "decimals": int(_view(ctx, feed, "decimals()", ["uint8"])),
        "answer": int(answer),
        "updated_at": int(updated_at),
        "age_seconds": block_timestamp - int(updated_at),
        "note": (
            "Informational: the vault price middleware ignores feed timestamps, so "
            "age is not enforced on chain."
        ),
    }


def snapshot_chain(
    ctx: Web3Context, spec: ChainSpec, peer: ChainSpec
) -> dict[str, Any]:
    """Every fact about ``spec``'s side of the pair at ``ctx.default_block``.

    ``ctx.default_block`` must be a block number: the block is read before and
    after the calls, it must carry that number, and the snapshot is complete
    only if its hash is unchanged.
    """
    if not isinstance(ctx.default_block, int):
        raise ReadinessConfigError(f"{spec.name}: default_block must be pinned")
    snapshot: dict[str, Any] = {
        "chain_id": int(spec.chain_id),
        "chain_selector": spec.chain_selector,
        "role": "hub" if spec is HUB else "spoke",
        "errors": [],
    }
    block = None
    try:
        facts = _block_facts(ctx.web3.eth.get_block(ctx.default_block))
        if facts["number"] != ctx.default_block:
            raise BlockNumberMismatch(f"{facts['number']} != {ctx.default_block}")
        block = snapshot["block"] = facts
    except Exception as exc:  # noqa: BLE001 - reported by class name only
        snapshot["errors"].append({"stage": "block", "type": type(exc).__name__})
    if block is not None:
        _read_sections(ctx, spec, peer, snapshot)
        _confirm_block(ctx, snapshot)
    snapshot["complete"] = not snapshot["errors"]
    return snapshot


def _read_sections(
    ctx: Web3Context, spec: ChainSpec, peer: ChainSpec, snapshot: dict[str, Any]
) -> None:
    timestamp = snapshot["block"]["timestamp"]
    sections: list[tuple[str, Callable[[], Any]]] = [
        ("usdc", lambda: _read_token(ctx, spec)),
        ("token_lane_out", lambda: _read_lane(ctx, spec, peer)),
        ("factory", lambda: _read_factory(ctx, spec, peer)),
        ("usdc_usd_feed", lambda: _read_feed(ctx, spec, timestamp)),
    ]
    for name, read in sections:
        try:
            snapshot[name] = read()
        except Exception as exc:  # noqa: BLE001 - reported by class name only
            snapshot["errors"].append({"stage": name, "type": type(exc).__name__})
    # The TESTTR lane is reported, never gating: the verdict is about USDC, and
    # a test token that is disabled or unreadable must not hide a ready pair.
    try:
        snapshot["testtr_lane_out"] = _read_lane(ctx, spec, peer, spec.testtr)
    except Exception as exc:  # noqa: BLE001 - reported by class name only
        snapshot["testtr_lane_out"] = {
            "error": type(exc).__name__,
            "informational": True,
        }


def _confirm_block(ctx: Web3Context, snapshot: dict[str, Any]) -> None:
    try:
        after = _block_facts(ctx.web3.eth.get_block(ctx.default_block))
    except Exception as exc:  # noqa: BLE001 - reported by class name only
        snapshot["errors"].append(
            {"stage": "block_recheck", "type": type(exc).__name__}
        )
        return
    if after["hash"] != snapshot["block"]["hash"]:
        snapshot["errors"].append(
            {"stage": "block_recheck", "type": "BlockHashChanged"}
        )


def build_report(hub: dict[str, Any], spoke: dict[str, Any]) -> dict[str, Any]:
    """Combine two chain snapshots into the readiness verdict.

    ``transport_ready`` needs the native USDC identity on both chains and the
    token lane in both directions; ``factories_ready`` needs every factory
    gate on both chains; ``ready`` needs both plus a complete observation.
    ``observed_blocks`` is always present; ``repin_candidates`` only when the
    transport is ready and the two snapshots are close in time, with the hub
    at or after the spoke (the lifecycle's clock rule). Candidates are
    observations, not pins: an unchanged hash during the reads is not
    finality, and an earlier block may predate the lane opening, so choose a
    final block pair, re-run this probe pinned to it and confirm the same
    facts before changing any acceptance pin. Nothing re-pins automatically.
    """
    complete = hub["complete"] and spoke["complete"]
    chains = (hub, spoke)
    tokens_ok = all(s.get("usdc", {}).get("is_native_usdc") for s in chains)
    lanes_ok = all(s.get("token_lane_out", {}).get("ready") for s in chains)
    transport_ready = complete and tokens_ok and lanes_ok
    factories_ready = complete and all(
        s.get("factory", {}).get("ready") for s in chains
    )
    candidates, repin_status = _repin_candidates(hub, spoke, transport_ready)
    return {
        "schema": SCHEMA,
        "generated_at": _now(),
        "pair": {"hub": HUB.name, "spoke": SPOKE.name, "asset": USDC_SYMBOL},
        "complete": complete,
        "ready": complete and transport_ready and factories_ready,
        "transport_ready": transport_ready,
        "factories_ready": factories_ready,
        "blocked_by": _blockers(hub, spoke),
        "chains": {HUB.name: hub, SPOKE.name: spoke},
        "observed_blocks": {s["role"]: s.get("block") for s in chains},
        "repin_candidates": candidates,
        "repin_status": repin_status,
        "simulation_governance": SIMULATION_GOVERNANCE,
    }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _blockers(hub: dict[str, Any], spoke: dict[str, Any]) -> list[str]:
    """One line per failed readiness gate, in evaluation order."""
    blockers: list[str] = []
    for snapshot in (hub, spoke):
        if snapshot["errors"]:
            failed = ", ".join(
                f"{e['stage']} ({e['type']})" for e in snapshot["errors"]
            )
            blockers.append(f"{snapshot['role']} observation incomplete: {failed}")
    for spec, snapshot in zip((HUB, SPOKE), (hub, spoke), strict=True):
        blockers.extend(_token_blockers(spec, snapshot.get("usdc")))
        blockers.extend(_lane_blockers(snapshot.get("token_lane_out")))
        blockers.extend(_factory_blockers(spec, snapshot.get("factory")))
    return blockers


def _token_blockers(spec: ChainSpec, token: dict[str, Any] | None) -> list[str]:
    if token is None or token["is_native_usdc"]:
        return []
    return [
        f"token {token['address']} on {spec.name} is not native USDC "
        f"(symbol={token['symbol']!r}, decimals={token['decimals']})"
    ]


def _lane_blockers(lane: dict[str, Any] | None) -> list[str]:
    if lane is None or lane["ready"]:
        return []
    return [
        f"Chainlink USDC token lane {lane['direction']} not serving the pair "
        f"(message_lane={lane['message_lane']}, token_lane={lane['token_lane']}, "
        f"on_ramp_version={lane['on_ramp_version']})"
    ]


def _factory_blockers(spec: ChainSpec, factory: dict[str, Any] | None) -> list[str]:
    if factory is None:
        return []
    return [
        f"factory on {spec.name}: {FACTORY_GATE_TEXT[gate]}"
        for gate, passed in factory["gates"].items()
        if not passed
    ]


def _repin_candidates(
    hub: dict[str, Any], spoke: dict[str, Any], transport_ready: bool
) -> tuple[dict[str, Any] | None, str]:
    """Per-chain pin candidates and a one-line status. Heights are never
    compared; only the snapshot timestamps are."""
    if not transport_ready:
        return None, "not eligible: transport not ready"
    hub_block, spoke_block = hub["block"], spoke["block"]
    skew = hub_block["timestamp"] - spoke_block["timestamp"]
    if skew < 0:
        return None, f"not eligible: hub snapshot precedes spoke snapshot by {-skew} s"
    if skew > MAX_SNAPSHOT_SKEW_S:
        return (
            None,
            f"not eligible: snapshot skew {skew} s exceeds {MAX_SNAPSHOT_SKEW_S} s",
        )
    candidates = {
        HUB.name: hub_block,
        SPOKE.name: spoke_block,
        "hub_after_spoke_seconds": skew,
        "note": (
            "Observations only, not final and not pins: choose a final block "
            "pair, re-run this probe pinned to it and confirm the same facts "
            "before changing acceptance pins. Heights are per chain."
        ),
    }
    return candidates, "eligible"


def connect(spec: ChainSpec) -> Web3Context:
    """A context pinned to the latest block of ``spec``'s chain, from the env."""
    url = os.environ.get(spec.env_var)
    if not url:
        raise ReadinessConfigError(f"{spec.env_var} is not set")
    web3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": RPC_TIMEOUT_S}))
    try:
        chain_id = web3.eth.chain_id
        head = web3.eth.block_number
    except Exception as exc:  # noqa: BLE001 - never echo provider errors
        raise ReadinessConfigError(
            f"{spec.env_var}: RPC unreachable ({type(exc).__name__})"
        ) from None
    if chain_id != spec.chain_id:
        raise ReadinessConfigError(
            f"{spec.env_var}: chain id {chain_id}, expected {spec.chain_id}"
        )
    ctx = Web3Context(web3=web3, chain_id=spec.chain_id)
    ctx.default_block = head
    return ctx


def config_error_report(message: str) -> dict[str, Any]:
    """The report written when no observation could be made, so a stale
    ``--output`` file can never be mistaken for a current result."""
    return {
        "schema": SCHEMA,
        "generated_at": _now(),
        "pair": {"hub": HUB.name, "spoke": SPOKE.name, "asset": USDC_SYMBOL},
        "complete": False,
        "ready": False,
        "transport_ready": False,
        "factories_ready": False,
        "blocked_by": [f"configuration error: {message}"],
        "chains": {},
        "observed_blocks": {},
        "repin_candidates": None,
        "repin_status": "not eligible: no observation",
        "error": {"type": ReadinessConfigError.__name__, "message": message},
        "simulation_governance": SIMULATION_GOVERNANCE,
    }


def run(
    make_context: Callable[[ChainSpec], Web3Context] | None = None,
) -> tuple[dict[str, Any], int, str]:
    """Produce ``(report, exit_code, summary)``. ``make_context`` defaults to
    :func:`connect`; a configuration error yields :func:`config_error_report`.

    The spoke head is captured first and the hub head last, so the hub
    snapshot is never older than the spoke snapshot (the order
    ``repin_candidates`` requires).
    """
    make = make_context or connect
    try:
        contexts = {spec.name: make(spec) for spec in (SPOKE, HUB)}
    except ReadinessConfigError as exc:
        report = config_error_report(str(exc))
        return report, EXIT_CONFIG, _summary(report)
    hub = snapshot_chain(contexts[HUB.name], HUB, SPOKE)
    spoke = snapshot_chain(contexts[SPOKE.name], SPOKE, HUB)
    report = build_report(hub, spoke)
    code = EXIT_OK if report["complete"] else EXIT_INCOMPLETE
    return report, code, _summary(report)


def _summary(report: dict[str, Any]) -> str:
    lines = [
        f"complete={report['complete']} ready={report['ready']} "
        f"transport_ready={report['transport_ready']} "
        f"factories_ready={report['factories_ready']}"
    ]
    for name, chain in report["chains"].items():
        block = chain.get("block")
        if block:
            lines.append(f"{name}: block {block['number']} ts {block['timestamp']}")
    lines.append(f"repin: {report['repin_status']}")
    lines.extend(f"blocked_by: {reason}" for reason in report["blocked_by"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--output", help="write the JSON report to this path")
    parser.add_argument(
        "--env-file", default=".env", help="dotenv file to load (default: .env)"
    )
    args = parser.parse_args(argv)
    try:
        load_dotenv(args.env_file)
        report, code, summary = run()
    except Exception as exc:  # noqa: BLE001 - keep stale reports from looking current
        kind = type(exc).__name__
        report = config_error_report(f"probe failed ({kind})")
        report["blocked_by"] = [f"probe error: {kind}"]
        report["error"] = {
            "type": kind,
            "message": "readiness probe failed before completion",
        }
        code = EXIT_INCOMPLETE
        summary = _summary(report)
    print(summary)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
        print(f"report written to {args.output}")
    return code


if __name__ == "__main__":
    sys.exit(main())
