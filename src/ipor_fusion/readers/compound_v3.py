from dataclasses import dataclass

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.core.multicall import Multicall3
from ipor_fusion.types import Amount

COMET_FACTOR_SCALE = 10**18
COMET_PRICE_SCALE = 10**8


@dataclass(frozen=True, slots=True)
class CometAssetInfo:
    """A collateral listing of a Comet; the factors are scaled by 1e18."""

    asset: ChecksumAddress
    price_feed: ChecksumAddress
    scale: int
    borrow_collateral_factor: int
    liquidate_collateral_factor: int


@dataclass(frozen=True, slots=True)
class CometAccountValue:
    """An account's position in the Comet's own prices, scaled by 1e8.

    The unit is the Comet's price unit: USD on USD-priced Comets, ETH on a WETH
    Comet. ``liquidation_limit`` is the collateral at its liquidation factors;
    the account is liquidatable when ``debt`` exceeds it.
    """

    collateral: int
    liquidation_limit: int
    debt: int


def _asset_info_decoder(
    value: tuple[int, str, str, int, int, int, int, int],
) -> CometAssetInfo:
    _, asset, price_feed, scale, borrow_factor, liquidate_factor, _, _ = value
    return CometAssetInfo(
        asset=Web3.to_checksum_address(asset),
        price_feed=Web3.to_checksum_address(price_feed),
        scale=scale,
        borrow_collateral_factor=borrow_factor,
        liquidate_collateral_factor=liquidate_factor,
    )


class CompoundV3FuseReader(ContractWrapper):
    """Reader for the immutables of an IPOR Fusion Compound V3 fuse.

    A Fusion market is bound to one Comet per chain through its fuses, so the
    Comet behind a market is a property of its fuse.
    """

    def comet(self) -> Call[ChecksumAddress]:
        return self._view(
            "COMET()", output_types=["address"], decoder=Web3.to_checksum_address
        )


class CompoundV3Reader(ContractWrapper):
    """Reader for Compound V3 (Comet) on-chain state."""

    def balance_of(self, account: ChecksumAddress) -> Call[Amount]:
        return self._view(
            "balanceOf(address)", account, output_types=["uint256"], decoder=Amount
        )

    def borrow_balance_of(self, account: ChecksumAddress) -> Call[Amount]:
        return self._view(
            "borrowBalanceOf(address)",
            account,
            output_types=["uint256"],
            decoder=Amount,
        )

    def collateral_balance_of(
        self, account: ChecksumAddress, asset: ChecksumAddress
    ) -> Call[Amount]:
        return self._view(
            "collateralBalanceOf(address,address)",
            account,
            asset,
            output_types=["uint128"],
            decoder=Amount,
        )

    def base_scale(self) -> Call[int]:
        return self._view("baseScale()", output_types=["uint256"])

    def base_token_price_feed(self) -> Call[ChecksumAddress]:
        return self._view(
            "baseTokenPriceFeed()",
            output_types=["address"],
            decoder=Web3.to_checksum_address,
        )

    def get_price(self, price_feed: ChecksumAddress) -> Call[int]:
        return self._view("getPrice(address)", price_feed, output_types=["uint256"])

    def num_assets(self) -> Call[int]:
        return self._view("numAssets()", output_types=["uint8"])

    def get_asset_info(self, index: int) -> Call[CometAssetInfo]:
        return self._view(
            "getAssetInfo(uint8)",
            index,
            output_types=[
                "(uint8,address,address,uint64,uint64,uint64,uint64,uint128)"
            ],
            decoder=_asset_info_decoder,
        )

    def read_account_value(
        self, ctx: Web3Context, account: ChecksumAddress
    ) -> CometAccountValue:
        """Value ``account``'s collateral and debt as the Comet does, over every
        collateral it lists rather than the vault's substrates: a revoked
        substrate does not take collateral out of the account (three batches)."""
        multicall = Multicall3(ctx)
        batch = multicall.batch()
        count = batch.add(self.num_assets())
        base_scale = batch.add(self.base_scale())
        base_feed = batch.add(self.base_token_price_feed())
        debt = batch.add(self.borrow_balance_of(account))
        batch.run()

        infos = multicall.aggregate(
            [self.get_asset_info(i) for i in range(count.value)]
        )

        batch = multicall.batch()
        base_price = batch.add(self.get_price(base_feed.value))
        balances = [
            batch.add(self.collateral_balance_of(account, info.asset)) for info in infos
        ]
        prices = [batch.add(self.get_price(info.price_feed)) for info in infos]
        batch.run()

        collateral = 0
        liquidation_limit = 0
        for info, balance, price in zip(infos, balances, prices, strict=True):
            value = balance.value * price.value // info.scale
            collateral += value
            liquidation_limit += (
                value * info.liquidate_collateral_factor // COMET_FACTOR_SCALE
            )
        return CometAccountValue(
            collateral=collateral,
            liquidation_limit=liquidation_limit,
            debt=debt.value * base_price.value // base_scale.value,
        )
