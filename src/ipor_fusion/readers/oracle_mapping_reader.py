"""Revert-safe on-chain reads for the oracle mapping: the single network seam
(:class:`OracleMappingReader`) and the feed-probe wrappers the SDK does not
expose elsewhere."""

from __future__ import annotations

from functools import cached_property
from typing import TypeVar

from eth_abi import decode
from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import BlockIdentifier

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.core.erc20 import ERC20
from ipor_fusion.core.oracle import (
    PriceOracleMiddleware,
    PriceOracleMiddlewareManager,
    price_oracle_middleware,
)
from ipor_fusion.types import (
    Amount,
    Decimals,
    Price,
)

# keccak256("AssetPriceSourceUpdated(address,address)") — both args are in the
# log data (neither is indexed), so only topic0 is used to filter.
# Base-middleware event only; managers emit AssetPriceSourceAdded/Removed
# instead, but expose getConfiguredAssets(), so the replay never runs on one.
ASSET_PRICE_SOURCE_UPDATED_TOPIC = (
    "0xe6c35d0425da27d8f991ada353619254c33e5094fc7e19154e02feb391937390"
)


def _addr(value: str) -> ChecksumAddress:
    return Web3.to_checksum_address(value)


class _Erc4626Feed(ContractWrapper):
    def vault(self) -> Call[ChecksumAddress]:
        return self._view("vault()", output_types=["address"], decoder=_addr)


class _MorphoFeed(ContractWrapper):
    def morpho_oracle(self) -> Call[ChecksumAddress]:
        return self._view("morphoOracle()", output_types=["address"], decoder=_addr)

    def collateral_token(self) -> Call[ChecksumAddress]:
        return self._view("collateralToken()", output_types=["address"], decoder=_addr)

    def loan_token(self) -> Call[ChecksumAddress]:
        return self._view("loanToken()", output_types=["address"], decoder=_addr)


class _MorphoOracle(ContractWrapper):
    def price(self) -> Call[int]:
        return self._view("price()", output_types=["uint256"], decoder=int)


class _DualXrefFeed(ContractWrapper):
    def asset_x(self) -> Call[ChecksumAddress]:
        return self._view("ASSET_X()", output_types=["address"], decoder=_addr)

    def asset_x_asset_y_feed(self) -> Call[ChecksumAddress]:
        return self._view(
            "ASSET_X_ASSET_Y_ORACLE_FEED()", output_types=["address"], decoder=_addr
        )

    def asset_y_usd_feed(self) -> Call[ChecksumAddress]:
        return self._view(
            "ASSET_Y_USD_ORACLE_FEED()", output_types=["address"], decoder=_addr
        )


class _Aggregator(ContractWrapper):
    def decimals(self) -> Call[Decimals]:
        return self._view("decimals()", output_types=["uint8"], decoder=Decimals)

    def description(self) -> Call[str]:
        return self._view("description()", output_types=["string"])

    def latest_round_data(self) -> Call[tuple[int, int, int, int, int]]:
        return self._view(
            "latestRoundData()",
            output_types=["uint80", "int256", "uint256", "uint256", "uint80"],
        )

    def version(self) -> Call[int]:
        return self._view("version()", output_types=["uint256"], decoder=int)

    def aggregator(self) -> Call[ChecksumAddress]:
        return self._view("aggregator()", output_types=["address"], decoder=_addr)

    def phase_id(self) -> Call[int]:
        return self._view("phaseId()", output_types=["uint16"], decoder=int)


class _Erc4626Vault(ContractWrapper):
    def asset(self) -> Call[ChecksumAddress]:
        return self._view("asset()", output_types=["address"], decoder=_addr)

    def decimals(self) -> Call[Decimals]:
        return self._view("decimals()", output_types=["uint8"], decoder=Decimals)

    def convert_to_assets(self, shares: int) -> Call[Amount]:
        return self._view(
            "convertToAssets(uint256)",
            shares,
            output_types=["uint256"],
            decoder=Amount,
        )


T = TypeVar("T")


class OracleMappingReader:
    """Revert-safe on-chain reads against a fixed ``Web3Context`` (and block)."""

    def __init__(self, ctx: Web3Context, oracle: ChecksumAddress):
        self._ctx = ctx
        self._oracle_addr = oracle
        self._oracle = PriceOracleMiddleware(oracle)
        self._manager = PriceOracleMiddlewareManager(oracle)

    @cached_property
    def _pricing_oracle(self) -> PriceOracleMiddleware:
        """The oracle wrapper matching its ``getAssetPrice`` ABI; probed on
        first use, since most readers in a mapping never read a price."""
        return price_oracle_middleware(self._ctx, self._oracle_addr)

    def _safe(self, call: Call[T]) -> T | None:
        try:
            return call.call(self._ctx)
        except Exception:
            return None

    # -- enumeration -------------------------------------------------------
    def configured_assets(self) -> list[ChecksumAddress] | None:
        return self._safe(self._manager.get_configured_assets())

    def asset_source_events(
        self, to_block: BlockIdentifier
    ) -> list[tuple[int, ChecksumAddress, ChecksumAddress]]:
        """Replay ``AssetPriceSourceUpdated`` logs as ``(block, asset, source)``."""
        logs = self._ctx.get_logs(
            contract_address=self._oracle_addr,
            topics=[ASSET_PRICE_SOURCE_UPDATED_TOPIC],
            to_block=to_block,
        )
        out: list[tuple[int, ChecksumAddress, ChecksumAddress]] = []
        for log in logs:
            asset, source = decode(["address", "address"], bytes(log["data"]))
            out.append((int(log["blockNumber"]), _addr(asset), _addr(source)))
        return out

    # -- oracle reads (reuse SDK wrappers) ----------------------------------
    def source_of(self, asset: ChecksumAddress) -> ChecksumAddress | None:
        return self._safe(self._oracle.get_source_of_asset_price(asset))

    def asset_price(self, asset: ChecksumAddress) -> Price | None:
        try:
            return self._pricing_oracle.get_asset_price(asset).call(self._ctx)
        except Exception:
            return None

    # -- oracle variant probes ---------------------------------------------
    def underlying_middleware(self) -> ChecksumAddress | None:
        """Manager variant: the global middleware zero-source assets delegate to."""
        return self._safe(self._manager.get_price_oracle_middleware())

    def chainlink_feed_registry(self) -> ChecksumAddress | None:
        return self._safe(self._oracle.chainlink_feed_registry())

    def delegate(self, oracle: ChecksumAddress) -> OracleMappingReader:
        """Sibling reader bound to another oracle on the same context/block."""
        return OracleMappingReader(self._ctx, oracle)

    # -- token metadata ----------------------------------------------------
    def symbol(self, token: ChecksumAddress) -> str | None:
        return self._safe(ERC20(token).symbol())

    def token_decimals(self, token: ChecksumAddress) -> Decimals | None:
        return self._safe(ERC20(token).decimals())

    # -- feed probes -------------------------------------------------------
    def feed_decimals(self, source: ChecksumAddress) -> Decimals | None:
        return self._safe(_Aggregator(source).decimals())

    def feed_description(self, source: ChecksumAddress) -> str | None:
        return self._safe(_Aggregator(source).description())

    def feed_latest_round_data(
        self, source: ChecksumAddress
    ) -> tuple[int, int, int, int, int] | None:
        return self._safe(_Aggregator(source).latest_round_data())

    def feed_version(self, source: ChecksumAddress) -> int | None:
        return self._safe(_Aggregator(source).version())

    def feed_aggregator(self, source: ChecksumAddress) -> ChecksumAddress | None:
        return self._safe(_Aggregator(source).aggregator())

    def feed_phase_id(self, source: ChecksumAddress) -> int | None:
        return self._safe(_Aggregator(source).phase_id())

    def feed_vault(self, source: ChecksumAddress) -> ChecksumAddress | None:
        return self._safe(_Erc4626Feed(source).vault())

    def feed_morpho_oracle(self, source: ChecksumAddress) -> ChecksumAddress | None:
        return self._safe(_MorphoFeed(source).morpho_oracle())

    def feed_collateral_token(self, source: ChecksumAddress) -> ChecksumAddress | None:
        return self._safe(_MorphoFeed(source).collateral_token())

    def feed_loan_token(self, source: ChecksumAddress) -> ChecksumAddress | None:
        return self._safe(_MorphoFeed(source).loan_token())

    def morpho_oracle_price(self, morpho_oracle: ChecksumAddress) -> int | None:
        return self._safe(_MorphoOracle(morpho_oracle).price())

    def feed_asset_x(self, source: ChecksumAddress) -> ChecksumAddress | None:
        return self._safe(_DualXrefFeed(source).asset_x())

    def feed_asset_x_asset_y_feed(
        self, source: ChecksumAddress
    ) -> ChecksumAddress | None:
        return self._safe(_DualXrefFeed(source).asset_x_asset_y_feed())

    def feed_asset_y_usd_feed(self, source: ChecksumAddress) -> ChecksumAddress | None:
        return self._safe(_DualXrefFeed(source).asset_y_usd_feed())

    # -- ERC4626 vault reads ----------------------------------------------
    def vault_asset(self, vault: ChecksumAddress) -> ChecksumAddress | None:
        return self._safe(_Erc4626Vault(vault).asset())

    def vault_decimals(self, vault: ChecksumAddress) -> Decimals | None:
        return self._safe(_Erc4626Vault(vault).decimals())

    def vault_convert_to_assets(
        self, vault: ChecksumAddress, shares: int
    ) -> Amount | None:
        return self._safe(_Erc4626Vault(vault).convert_to_assets(shares))
