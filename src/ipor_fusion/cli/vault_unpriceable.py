"""Tokens a balance fuse prices via the PriceOracleMiddleware that it cannot price.

A balance fuse that prices a token through the vault's PriceOracleMiddleware
reverts when ``getAssetPrice`` reverts. That blocks the market: its
``updateMarketsBalances`` and every ``execute`` touching it revert, and its
booked balance freezes. The tokens are derived from granted substrates, not
from current positions, so latent exposure is reported before a position
triggers it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.types import ChecksumAddress

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call
from ipor_fusion.core.multicall import Multicall3
from ipor_fusion.core.oracle import PriceOracleMiddleware
from ipor_fusion.core.plasma_vault import BalanceFuse
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.readers.lending_health import AAVE_V3_MARKET_IDS
from ipor_fusion.readers.morpho import MorphoPositionBreakdown
from ipor_fusion.substrates import decode_substrate

_ZERO = Web3.to_checksum_address("0x" + "00" * 20)

_ASSET_SELECTOR = function_signature_to_4byte_selector("asset()")
_GET_SILOS_SELECTOR = function_signature_to_4byte_selector("getSilos()")
# PUSH4 getPriceOracle(): AaveV3BalanceFuse resolves Aave's own oracle through
# the PoolAddressesProvider; AaveV3WithPriceOracleMiddlewareBalanceFuse never
# does. Both expose the same public getters, so bytecode is the only on-chain
# discriminator.
_PUSH4_GET_PRICE_ORACLE = b"\x63" + function_signature_to_4byte_selector(
    "getPriceOracle()"
)

_ERC4626_MARKET_IDS = frozenset(
    range(IporFusionMarkets.ERC4626_0001, IporFusionMarkets.ERC4626_0020 + 1)
)


@dataclass(frozen=True)
class MiddlewarePricedToken:
    """A token the balance fuse of ``market_id`` prices via the middleware.

    ``via`` says how the token derives from a granted substrate.
    ``zero_balance_reverts`` is True when the fuse prices it before any
    balance check, so a missing price reverts ``balanceOf()`` with no
    position; otherwise only a non-zero position does, and third parties can
    create one (a transfer, or a deposit on the vault's behalf).
    ``position_open`` says whether the vault holds a non-zero amount of the
    token in that position now; ``None`` when it was not read.
    ``morpho_collateral`` marks a Morpho collateral token, which
    MorphoOnlyLiquidityBalanceFuse never prices.
    ``price_source`` is the middleware's explicit source for the token, set
    on flagged tokens only; ``None`` means none (or unreadable).
    """

    market_id: int
    token: ChecksumAddress
    via: str
    zero_balance_reverts: bool
    position_open: bool | None = None
    morpho_collateral: bool = False
    price_source: ChecksumAddress | None = None


def _address_call(ctx: Web3Context, to: ChecksumAddress, selector: bytes) -> Call:
    return Call(
        to=to,
        data=selector,
        output_types=["address"],
        decoder=Web3.to_checksum_address,
        ctx=ctx,
    )


def _silos_call(ctx: Web3Context, config: ChecksumAddress) -> Call:
    return Call(
        to=config,
        data=_GET_SILOS_SELECTOR,
        output_types=["address", "address"],
        decoder=lambda *silos: [Web3.to_checksum_address(s) for s in silos],
        ctx=ctx,
    )


def _substrate_addresses(market_id: int, subs: list[bytes]) -> list[ChecksumAddress]:
    addresses = []
    for sub in subs:
        info = decode_substrate(sub, market_id)
        if info.address and int(info.address, 16):
            addresses.append(Web3.to_checksum_address(info.address))
    return addresses


def _plain_tokens(
    market_substrates: dict[int, list[bytes]], underlying: ChecksumAddress
) -> list[MiddlewarePricedToken]:
    """Substrates that are the priced token itself (no reads needed)."""
    tokens = [
        MiddlewarePricedToken(
            IporFusionMarkets.ERC20_VAULT_BALANCE,
            token,
            "granted ERC20 substrate",
            zero_balance_reverts=True,
        )
        for token in _substrate_addresses(
            IporFusionMarkets.ERC20_VAULT_BALANCE,
            market_substrates.get(IporFusionMarkets.ERC20_VAULT_BALANCE, []),
        )
        if token != underlying
    ]
    tokens += [
        MiddlewarePricedToken(
            IporFusionMarkets.DOLOMITE,
            token,
            "granted Dolomite asset substrate",
            zero_balance_reverts=False,
        )
        for token in _substrate_addresses(
            IporFusionMarkets.DOLOMITE,
            market_substrates.get(IporFusionMarkets.DOLOMITE, []),
        )
    ]
    return tokens


def _loan_leg_open(pb: MorphoPositionBreakdown) -> bool | None:
    """The balance fuse prices the loan token only for supply minus borrow.
    Both legs non-zero and equal here may still differ at the fuse's accrual,
    so that case is unknown."""
    if pb.supply_assets == pb.borrow_assets:
        return None if pb.supply_assets else False
    return True


def _morpho_tokens(
    morpho_positions: dict[int, list[MorphoPositionBreakdown]] | None,
) -> list[MiddlewarePricedToken]:
    """Loan and collateral tokens of every whitelisted Morpho market.

    ``supply`` and ``supplyCollateral`` are permissionless in ``onBehalf``.
    """
    return [
        MiddlewarePricedToken(
            market_id,
            token,
            f"{leg} token of Morpho market {pb.market_id}",
            zero_balance_reverts=False,
            position_open=position_open,
            morpho_collateral=leg == "collateral",
        )
        for market_id, breakdowns in (morpho_positions or {}).items()
        for pb in breakdowns
        for leg, token, position_open in (
            ("loan", pb.loan_token, _loan_leg_open(pb)),
            ("collateral", pb.collateral_token, pb.collateral > 0),
        )
        if token != _ZERO
    ]


def _vault_asset_tokens(
    ctx: Web3Context, market_substrates: dict[int, list[bytes]]
) -> list[MiddlewarePricedToken]:
    """``asset()`` of granted ERC4626 vaults (Euler V2, generic ERC4626) and
    of both silos of every granted Silo Config. All three fuses price it
    before reading the balance."""
    vaults: list[tuple[int, ChecksumAddress, str]] = []
    configs: list[ChecksumAddress] = []
    for market_id, subs in market_substrates.items():
        if market_id == IporFusionMarkets.EULER_V2:
            vaults += [
                (market_id, v, f"asset() of granted Euler vault {v}")
                for v in _substrate_addresses(market_id, subs)
            ]
        elif market_id in _ERC4626_MARKET_IDS:
            vaults += [
                (market_id, v, f"asset() of granted ERC4626 vault {v}")
                for v in _substrate_addresses(market_id, subs)
            ]
        elif market_id == IporFusionMarkets.SILO_V2:
            configs += _substrate_addresses(market_id, subs)

    multicall = Multicall3(ctx)
    if configs:
        silo_pairs = multicall.try_aggregate([_silos_call(ctx, c) for c in configs])
        vaults += [
            (
                IporFusionMarkets.SILO_V2,
                silo,
                f"asset() of silo {silo} of granted Silo Config {config}",
            )
            for config, silos in zip(configs, silo_pairs, strict=True)
            for silo in silos or ()
            if silo != _ZERO
        ]
    if not vaults:
        return []
    assets = multicall.try_aggregate(
        [_address_call(ctx, vault, _ASSET_SELECTOR) for _, vault, _ in vaults]
    )
    return [
        MiddlewarePricedToken(
            market_id,
            asset,
            via,
            zero_balance_reverts=True,
        )
        for (market_id, _, via), asset in zip(vaults, assets, strict=True)
        if asset is not None and asset != _ZERO
    ]


def _aave_v3_middleware_tokens(
    ctx: Web3Context,
    balance_fuses: list[BalanceFuse],
    market_substrates: dict[int, list[bytes]],
) -> list[MiddlewarePricedToken]:
    """Granted assets of Aave V3 markets whose balance fuse is the
    middleware-priced variant; it prices every asset before reading it."""
    tokens = []
    for bf in balance_fuses:
        if bf.market_id not in AAVE_V3_MARKET_IDS:
            continue
        code = bytes(ctx.web3.eth.get_code(Web3.to_checksum_address(bf.fuse)))
        if not code or _PUSH4_GET_PRICE_ORACLE in code:
            continue
        tokens += [
            MiddlewarePricedToken(
                bf.market_id,
                asset,
                "granted asset (middleware-priced Aave V3 balance fuse)",
                zero_balance_reverts=True,
            )
            for asset in _substrate_addresses(
                bf.market_id, market_substrates.get(bf.market_id, [])
            )
        ]
    return tokens


def _unpriceable(
    ctx: Web3Context, oracle: PriceOracleMiddleware, tokens: Iterable[ChecksumAddress]
) -> dict[ChecksumAddress, ChecksumAddress | None]:
    """Tokens whose ``getAssetPrice`` reverts, mapped to their explicit
    price source (``None`` when there is none).

    The revert is the signal, not a zero source: on Ethereum the middleware
    falls back to the Chainlink Feed Registry and a PriceOracleMiddlewareManager
    falls through to the global middleware, while a configured source can
    still revert (``UnexpectedPriceResult``). A failed sub-call in a
    successful Multicall3 batch is a deterministic revert at that block; a
    transport failure raises instead of flagging.
    """
    ordered = sorted(set(tokens))
    reads = Multicall3(ctx).try_aggregate(
        [
            call
            for token in ordered
            for call in (
                oracle.get_source_of_asset_price(token),
                oracle.get_asset_price(token),
            )
        ]
    )
    return {
        token: source if source != _ZERO else None
        for token, source, price in zip(ordered, reads[::2], reads[1::2], strict=True)
        if price is None
    }


def fetch_unpriceable_priced_tokens(
    ctx: Web3Context,
    oracle_address: str,
    underlying: str,
    balance_fuses: list[BalanceFuse],
    market_substrates: dict[int, list[bytes]],
    morpho_positions: dict[int, list[MorphoPositionBreakdown]] | None,
) -> list[MiddlewarePricedToken]:
    """Every middleware-priced token of a granted substrate the middleware
    cannot price, in market order."""
    priced = [
        *_plain_tokens(market_substrates, Web3.to_checksum_address(underlying)),
        *_morpho_tokens(morpho_positions),
        *_vault_asset_tokens(ctx, market_substrates),
        *_aave_v3_middleware_tokens(ctx, balance_fuses, market_substrates),
    ]
    if not priced:
        return []
    oracle = PriceOracleMiddleware(ctx, Web3.to_checksum_address(oracle_address))
    unpriceable = _unpriceable(ctx, oracle, (p.token for p in priced))
    flagged = {
        (p.market_id, p.token, p.via): replace(p, price_source=unpriceable[p.token])
        for p in priced
        if p.token in unpriceable
    }
    return [flagged[key] for key in sorted(flagged)]
