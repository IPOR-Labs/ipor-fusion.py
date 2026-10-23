"""Shared fixtures for the crosschain tests: ``Chain``, the bridge
infrastructure of one chain (LayerZero endpoint and eid, Stargate
TokenMessaging and USDC pool, CCIP router and selector) with the conftest
fixture that connects to it and the block the tests pin; the transports the
offline transport tests relay over; and the relay assertion the simulations
share. Adding a chain is one ``Chain`` entry plus a ``web3_<name>`` fixture in
``conftest.py``.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from web3 import Web3

from ipor_fusion.crosschain import (
    CcipChain,
    CcipTransport,
    StargateChain,
    StargateTransport,
)
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
    every use of such a chain raises ``NotImplementedError`` with it.
    ``simulated_gas_limit`` is the block gas limit the simulations run under
    where the pinned block's own is too small for a delivery (HyperEVM seals
    3 M small blocks between its 30 M big blocks)."""

    name: str
    chain_id: ChainId
    web3_fixture: str
    block: int
    usdc: str
    usdc_decimals: int
    stargate_asset_id: int
    stargate_shared_decimals: int
    eid: int
    endpoint: str
    token_messaging: str
    stargate_pool: str
    ccip_router: str
    chain_selector: int
    pending: str | None = None
    simulated_gas_limit: int | None = None

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
        usdc_decimals=6,
        stargate_asset_id=1,
        stargate_shared_decimals=6,
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
        usdc_decimals=6,
        stargate_asset_id=1,
        stargate_shared_decimals=6,
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
        usdc_decimals=6,
        stargate_asset_id=1,
        stargate_shared_decimals=6,
        eid=ARBITRUM_EID,
        endpoint=LAYERZERO_ENDPOINT,
        token_messaging=ARBITRUM_TOKEN_MESSAGING,
        stargate_pool=ARBITRUM_STARGATE_USDC_POOL,
        ccip_router=ARBITRUM_CCIP_ROUTER,
        chain_selector=ARBITRUM_CHAIN_SELECTOR,
    ),
    # Next spoke, pre-wired with what is live: the chain, its LayerZero endpoint
    # (eid 30367, not at the address the older chains share), the CCIP router
    # and selector (message lanes to and from the hub chains exist; the
    # Arbitrum -> HyperEVM USDC token lane was closed at the pinned blocks,
    # but opened on 2026-09-29), USDC and a block pinned two minutes before
    # the hub's. Still missing: the Stargate
    # TokenMessaging and USDC pool, the dispatcher and the spoke vault. Fill
    # them, add the spoke to the deployment and drop `pending`; the strict
    # xfail on its lifecycle params then fails until it is removed too.
    "hyperevm": Chain(
        name="hyperevm",
        chain_id=HYPEREVM,
        web3_fixture="web3_hyperevm",
        block=46_745_610,
        usdc=Web3.to_checksum_address("0xb88339CB7199b77E23DB6E890353E22632Ba630f"),
        usdc_decimals=6,
        stargate_asset_id=1,
        stargate_shared_decimals=6,
        eid=30367,
        endpoint=Web3.to_checksum_address("0x3A73033C0b1407574C76BdBAc67f126f6b4a9AA9"),
        token_messaging="",
        stargate_pool="",
        ccip_router=Web3.to_checksum_address(
            "0x13b3332b66389B1467CA6eBd6fa79775CCeF65ec"
        ),
        chain_selector=2442541497099098535,
        simulated_gas_limit=30_000_000,
        pending=(
            "the outbound CCIP USDC token lane was closed at the pinned blocks "
            "(open since 2026-09-29); the hub factory's route policy, dispatcher "
            "and spoke vault are not wired into this deployment; Stargate has "
            "no HyperEVM route"
        ),
    ),
}


#: The chains the offline transport tests relay between: the Ethereum hub
#: and the Base and Arbitrum spokes of the Stargate and CCIP infrastructure.
TRANSPORT_CHAINS = ("ethereum", "base", "arbitrum")
#: Any executor address for the offline transport tests (an executor and its
#: dispatcher share it).
STARGATE_EXECUTOR = Web3.to_checksum_address(
    "0x1d5c9d44f8d556ec7f557ae992401cc770937e6e"
)


def _stargate_chain(chain: Chain) -> StargateChain:
    chain.require_available()
    return StargateChain(
        chain_id=chain.chain_id,
        eid=chain.eid,
        endpoint=Web3.to_checksum_address(chain.endpoint),
        token_messaging=Web3.to_checksum_address(chain.token_messaging),
        stargate_pool=Web3.to_checksum_address(chain.stargate_pool),
        token=Web3.to_checksum_address(chain.usdc),
        asset_id=chain.stargate_asset_id,
        local_decimals=chain.usdc_decimals,
        shared_decimals=chain.stargate_shared_decimals,
    )


def _ccip_chain(chain: Chain) -> CcipChain:
    chain.require_available()
    # The real path mints through CCTP, so no holder is impersonated: the
    # simulator funds the synthetic token source by a storage override.
    return CcipChain(
        chain_id=chain.chain_id,
        chain_selector=chain.chain_selector,
        router=Web3.to_checksum_address(chain.ccip_router),
        token=Web3.to_checksum_address(chain.usdc),
        local_decimals=chain.usdc_decimals,
    )


def stargate_transport() -> StargateTransport:
    """The Stargate transport over :data:`TRANSPORT_CHAINS`."""
    return StargateTransport(_stargate_chain(CHAINS[n]) for n in TRANSPORT_CHAINS)


def ccip_transport() -> CcipTransport:
    """The CCIP transport over :data:`TRANSPORT_CHAINS`."""
    return CcipTransport(_ccip_chain(CHAINS[n]) for n in TRANSPORT_CHAINS)


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
                f"selector=0x{selector} (reason={call.revert_reason})"
            )
