"""Shared fixtures for the crosschain tests.

Three levels, so a deployment with the hub on another chain or with several
spokes is data, not code:

- ``Chain``: the bridge infrastructure of one chain (LayerZero endpoint and
  eid, Stargate TokenMessaging and USDC pool, CCIP router and selector), plus
  the conftest fixture that connects to it and the block the tests pin.
- ``Deployment``: one hub vault with its executors, fuses and roles, and the
  ``Spoke``s (chain + spoke vault) it may deploy into.
- The lifecycle tests are parametrized over ``(deployment, spoke)`` pairs.

``POC`` is the mainnet POC deployment (Ethereum hub, Base and Arbitrum spokes)
as read on 2026-09-24: the hub vault grants one Stargate and one CCIP
executor, each with a dispatcher at the same address on every spoke, and every
fuse was built with the keccak-derived POC market id. Adding a chain is one
``Chain`` entry plus a ``web3_<name>`` fixture in ``conftest.py``.

Block pinning: pin every chain of a deployment within a couple of minutes of
each other and the hub LAST (latest timestamp), so a spoke observation is never
in the hub's future and stays inside the executors' 1 h staleness window.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

import pytest
from web3 import Web3

from ipor_fusion import (
    CcipChain,
    CcipTransport,
    CrosschainTransport,
    LaneFuses,
    StargateChain,
    StargateTransport,
)
from ipor_fusion.crosschain import CrosschainTransportKind
from ipor_fusion.fuses.crosschain import crosschain_market_id
from ipor_fusion.types import ChainId

ETHEREUM = ChainId(1)
BASE = ChainId(8453)
ARBITRUM = ChainId(42161)
HYPEREVM = ChainId(999)

# LayerZero V2 endpoint on Ethereum, Base and Arbitrum; newer chains such as
# HyperEVM got a different address, so it is a per-chain field.
LAYERZERO_ENDPOINT = Web3.to_checksum_address(
    "0x1a44076050125825900e736c501f859c50fE728c"
)

ETHEREUM_USDC = Web3.to_checksum_address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
BASE_USDC = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
ARBITRUM_USDC = Web3.to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
ETHEREUM_EID = 30101
BASE_EID = 30184
ARBITRUM_EID = 30110
ETHEREUM_TOKEN_MESSAGING = Web3.to_checksum_address(
    "0x6d6620eFa72948C5f68A3C8646d58C00d3f4A980"
)
BASE_TOKEN_MESSAGING = Web3.to_checksum_address(
    "0x5634c4a5FEd09819E3c46D86A965Dd9447d86e47"
)
ARBITRUM_TOKEN_MESSAGING = Web3.to_checksum_address(
    "0x19cFCE47eD54a88614648DC3f19A5980097007dD"
)
ETHEREUM_STARGATE_USDC_POOL = Web3.to_checksum_address(
    "0xc026395860Db2d07ee33e05fE50ed7bD583189C7"
)
BASE_STARGATE_USDC_POOL = Web3.to_checksum_address(
    "0x27a16dc786820B16E5c9028b75B99F6f604b5d26"
)
ARBITRUM_STARGATE_USDC_POOL = Web3.to_checksum_address(
    "0xe8CDF27AcD73a434D661C84887215F7598e7d0d3"
)
ETHEREUM_CCIP_ROUTER = Web3.to_checksum_address(
    "0x80226fc0Ee2b096224EeAc085Bb9a8cba1146f7D"
)
BASE_CCIP_ROUTER = Web3.to_checksum_address(
    "0x881e3A65B4d4a04dD529061dd0071cf975F58bCD"
)
ARBITRUM_CCIP_ROUTER = Web3.to_checksum_address(
    "0x141fa059441E0ca23ce184B6A78bafD2A517DdE8"
)
ETHEREUM_CHAIN_SELECTOR = 5009297550715157269
BASE_CHAIN_SELECTOR = 15971525489660198786
ARBITRUM_CHAIN_SELECTOR = 4949039107694359620


@dataclass(frozen=True)
class Chain:
    """Bridge infrastructure of one chain, independent of any deployment.
    ``pending`` names what is still missing on a chain the stack will grow to;
    every use of such a chain raises ``NotImplementedError`` with it."""

    name: str
    chain_id: ChainId
    web3_fixture: str
    block: int
    usdc: str
    eid: int
    endpoint: str
    token_messaging: str
    stargate_pool: str
    ccip_router: str
    chain_selector: int
    pending: str | None = None

    def require_available(self) -> None:
        if self.pending:
            raise NotImplementedError(f"{self.name}: {self.pending}")


CHAINS: dict[str, Chain] = {
    "ethereum": Chain(
        name="ethereum",
        chain_id=ETHEREUM,
        web3_fixture="web3_eth",
        block=26_045_913,
        usdc=ETHEREUM_USDC,
        eid=ETHEREUM_EID,
        endpoint=LAYERZERO_ENDPOINT,
        token_messaging=ETHEREUM_TOKEN_MESSAGING,
        stargate_pool=ETHEREUM_STARGATE_USDC_POOL,
        ccip_router=ETHEREUM_CCIP_ROUTER,
        chain_selector=ETHEREUM_CHAIN_SELECTOR,
    ),
    "base": Chain(
        name="base",
        chain_id=BASE,
        web3_fixture="web3_base",
        block=51_723_200,
        usdc=BASE_USDC,
        eid=BASE_EID,
        endpoint=LAYERZERO_ENDPOINT,
        token_messaging=BASE_TOKEN_MESSAGING,
        stargate_pool=BASE_STARGATE_USDC_POOL,
        ccip_router=BASE_CCIP_ROUTER,
        chain_selector=BASE_CHAIN_SELECTOR,
    ),
    "arbitrum": Chain(
        name="arbitrum",
        chain_id=ARBITRUM,
        web3_fixture="web3_arb",
        block=508_376_600,
        usdc=ARBITRUM_USDC,
        eid=ARBITRUM_EID,
        endpoint=LAYERZERO_ENDPOINT,
        token_messaging=ARBITRUM_TOKEN_MESSAGING,
        stargate_pool=ARBITRUM_STARGATE_USDC_POOL,
        ccip_router=ARBITRUM_CCIP_ROUTER,
        chain_selector=ARBITRUM_CHAIN_SELECTOR,
    ),
    # Next spoke, pre-wired with what is live: the chain, its LayerZero endpoint
    # (eid 30367, not at the address the older chains share), the CCIP router
    # and selector (message lanes to and from the hub chains exist; USDC has
    # no pool on them yet, see test_crosschain_readiness.py), USDC and a block
    # pinned two minutes before the hub's. Still missing: the Stargate
    # TokenMessaging and USDC pool, the dispatcher and the spoke vault. Fill
    # them, add the spoke to the deployment and drop `pending`; the strict
    # xfail on its lifecycle params then fails until it is removed too.
    "hyperevm": Chain(
        name="hyperevm",
        chain_id=HYPEREVM,
        web3_fixture="web3_hyperevm",
        block=46_745_610,
        usdc=Web3.to_checksum_address("0xb88339CB7199b77E23DB6E890353E22632Ba630f"),
        eid=30367,
        endpoint=Web3.to_checksum_address("0x3A73033C0b1407574C76BdBAc67f126f6b4a9AA9"),
        token_messaging="",
        stargate_pool="",
        ccip_router=Web3.to_checksum_address(
            "0x13b3332b66389B1467CA6eBd6fa79775CCeF65ec"
        ),
        chain_selector=2442541497099098535,
        pending=(
            "Stargate TokenMessaging and USDC pool, a CCIP pool for USDC, the "
            "dispatcher and the spoke vault are not wired yet"
        ),
    ),
}


@dataclass(frozen=True)
class Spoke:
    """A chain a deployment's executors may deploy into, with its spoke vault
    (``None`` while the chain is pending)."""

    chain: Chain
    remote_vault: str | None

    @property
    def name(self) -> str:
        return self.chain.name

    @property
    def chain_id(self) -> ChainId:
        return self.chain.chain_id


@dataclass(frozen=True)
class TransportDeployment:
    """One transport's executor generation on the hub and the two fuses that
    drive it. A new generation always comes from a new factory, so the factory
    pins the ABI generation the SDK mirrors."""

    executor: str
    factory: str
    supply_fuse: str
    command_fuse: str


@dataclass(frozen=True)
class Deployment:
    """One hub vault, its executors (one per transport, each created by its own
    factory), the fuses registered on the vault, the attestation roles, and the
    spokes. Executors and dispatchers share an address, so the executor address
    is also the dispatcher address on every spoke."""

    hub: Chain
    vault: str
    owner: str  # holds ATOMIST, ALPHA, FUSE_MANAGER and UPDATE_MARKETS_BALANCES
    balance_proposer: str
    balance_approver: str
    market_id: int
    transports: dict[CrosschainTransportKind, TransportDeployment]
    claim_fuse: str
    spokes: tuple[Spoke, ...]
    #: Spokes the deployment will grow to; their chains are still ``pending``.
    planned_spokes: tuple[Spoke, ...] = ()

    @property
    def chains(self) -> list[Chain]:
        return [self.hub, *(spoke.chain for spoke in self.spokes)]

    def executor(self, transport_kind: CrosschainTransportKind) -> str:
        return self.transports[transport_kind].executor

    def factory(self, transport_kind: CrosschainTransportKind) -> str:
        return self.transports[transport_kind].factory

    def lane_fuses(self, transport_kind: CrosschainTransportKind) -> LaneFuses:
        transport = self.transports[transport_kind]
        return LaneFuses(
            supply=Web3.to_checksum_address(transport.supply_fuse),
            command=Web3.to_checksum_address(transport.command_fuse),
            claim=Web3.to_checksum_address(self.claim_fuse),
        )

    def transport(self, transport_kind: CrosschainTransportKind) -> CrosschainTransport:
        return _TRANSPORTS[transport_kind](self)


POC = Deployment(
    hub=CHAINS["ethereum"],
    vault=Web3.to_checksum_address("0x0Aa75BfD30Ae2d4061FF8261453b933Ab5959991"),
    owner=Web3.to_checksum_address("0x3F48E1FfC0Ea0b1f908770Ec3372eA0F50dA2af2"),
    balance_proposer=Web3.to_checksum_address(
        "0x4F56543f62aB0186bA390e754EFC6c0870Dc5df5"
    ),
    balance_approver=Web3.to_checksum_address(
        "0xCeE5C4272E246A424AeDE992c987966736E0F63b"
    ),
    market_id=crosschain_market_id("IPOR_FUSION_CROSSCHAIN_USDC_POC_V1"),
    # The Stargate executor exposes no interface version; its factory pins it.
    transports={
        CrosschainTransportKind.STARGATE_LAYERZERO: TransportDeployment(
            executor=Web3.to_checksum_address(
                "0x1d5c9d44f8d556ec7f557ae992401cc770937e6e"
            ),
            factory=Web3.to_checksum_address(
                "0xb7894a9081d9060ced0b2e33714cdb075de67b9e"
            ),
            supply_fuse=Web3.to_checksum_address(
                "0x9455e228b821b7f6695aa5a1cdcac9d331be7a13"
            ),
            command_fuse=Web3.to_checksum_address(
                "0x2413227ce2d96d871a78503651b90779d39ebc07"
            ),
        ),
        CrosschainTransportKind.CHAINLINK_CCIP: TransportDeployment(
            executor=Web3.to_checksum_address(
                "0xb99ab307ce3df269b9f8763657fa128bbd38e2aa"
            ),
            factory=Web3.to_checksum_address(
                "0xf360e8b00694c03fdc33dc2c54396fa41fabaadf"
            ),
            supply_fuse=Web3.to_checksum_address(
                "0x79613512f64a8360c1dfd17d93f2efd02fd023b5"
            ),
            command_fuse=Web3.to_checksum_address(
                "0xbcb72b216dd685dfebe952c95e9210c6e4b8c8c6"
            ),
        ),
    },
    claim_fuse=Web3.to_checksum_address("0xc4f6ab5938dd1509d03d3817be69c17440e04399"),
    spokes=(
        Spoke(
            CHAINS["base"],
            Web3.to_checksum_address("0x7411cb6cA68Dfedf5a8d09146Ee277B11ddfb578"),
        ),
        Spoke(
            CHAINS["arbitrum"],
            Web3.to_checksum_address("0x174bfA12935AC416caA7d27397Bd4f3b15175980"),
        ),
    ),
    planned_spokes=(Spoke(CHAINS["hyperevm"], remote_vault=None),),
)

# Kept for the offline transport tests and as the single POC executor address.
STARGATE_EXECUTOR = POC.executor(CrosschainTransportKind.STARGATE_LAYERZERO)

#: The transports every deployment is exercised on.
TRANSPORT_KINDS = (
    CrosschainTransportKind.STARGATE_LAYERZERO,
    CrosschainTransportKind.CHAINLINK_CCIP,
)


def _transport_name(transport_kind: CrosschainTransportKind) -> str:
    return (
        "stargate"
        if transport_kind == CrosschainTransportKind.STARGATE_LAYERZERO
        else "ccip"
    )


#: Every (deployment, spoke, transport) triple the lifecycle test runs. A
#: planned spoke is expected to raise NotImplementedError; `strict` turns that
#: into a failure the day it stops raising, so the marker cannot be forgotten.
LIFECYCLES = [
    *[
        pytest.param(
            POC,
            spoke,
            transport_kind,
            id=f"{_transport_name(transport_kind)}-{POC.hub.name}-to-{spoke.name}",
        )
        for transport_kind in TRANSPORT_KINDS
        for spoke in POC.spokes
    ],
    *[
        pytest.param(
            POC,
            spoke,
            transport_kind,
            id=f"{_transport_name(transport_kind)}-{POC.hub.name}-to-{spoke.name}",
            marks=pytest.mark.xfail(
                raises=NotImplementedError, strict=True, reason=spoke.chain.pending
            ),
        )
        for transport_kind in TRANSPORT_KINDS
        for spoke in POC.planned_spokes
    ],
]


def _stargate_chain(chain: Chain) -> StargateChain:
    chain.require_available()
    return StargateChain(
        chain_id=chain.chain_id,
        eid=chain.eid,
        endpoint=Web3.to_checksum_address(chain.endpoint),
        token_messaging=Web3.to_checksum_address(chain.token_messaging),
        stargate_pool=Web3.to_checksum_address(chain.stargate_pool),
        token=Web3.to_checksum_address(chain.usdc),
    )


def _ccip_chain(chain: Chain) -> CcipChain:
    chain.require_available()
    # CCTP mints on the real path; the Stargate pools hold enough USDC on every
    # chain to stand in as the token source.
    return CcipChain(
        chain_id=chain.chain_id,
        chain_selector=chain.chain_selector,
        router=Web3.to_checksum_address(chain.ccip_router),
        token=Web3.to_checksum_address(chain.usdc),
        token_source=Web3.to_checksum_address(chain.stargate_pool),
    )


def stargate_transport(deployment: Deployment = POC) -> StargateTransport:
    """The Stargate transport over every chain of ``deployment``."""
    return StargateTransport(_stargate_chain(c) for c in deployment.chains)


def ccip_transport(deployment: Deployment = POC) -> CcipTransport:
    """The CCIP transport over every chain of ``deployment``."""
    return CcipTransport(_ccip_chain(c) for c in deployment.chains)


_TRANSPORTS = {
    CrosschainTransportKind.STARGATE_LAYERZERO: stargate_transport,
    CrosschainTransportKind.CHAINLINK_CCIP: ccip_transport,
}


def assert_relay_success(results, *, expected_failures: Collection[str] = ()) -> None:
    """Fail with the first reverted call of the first failing chain. Calls
    labelled in ``expected_failures`` are deliberate reverts (a fail-closed
    read, a refused proposal) that stay in the replayed call list."""
    for chain_id, result in sorted(results.items()):
        for call in result.failed_calls:
            if call.label in expected_failures:
                continue
            selector = bytes(call.return_data[:4]).hex()
            raise AssertionError(
                f"chain {chain_id}: {call.label!r} reverted: {call.error} "
                f"selector=0x{selector} (reason={result.revert_reason})"
            )
