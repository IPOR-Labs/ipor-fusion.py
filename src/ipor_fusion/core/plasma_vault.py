from __future__ import annotations

from dataclasses import dataclass

from eth_abi import decode, encode as abi_encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from hexbytes import HexBytes
from web3 import Web3
from web3.types import LogReceipt

from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.fuses.base import FuseAction
from ipor_fusion.types import Amount, MarketId, Decimals, Shares


@dataclass(slots=True)
class BalanceFuse:
    """Mapping between a market and its balance-tracking fuse contract."""

    market_id: MarketId
    fuse: ChecksumAddress


def _market_id_list_decoder(value: list) -> list[MarketId]:
    return [MarketId(v) for v in value]


def _address_list_decoder(value: list) -> list[ChecksumAddress]:
    return [Web3.to_checksum_address(item) for item in value]


class PlasmaVault(ContractWrapper):
    """ERC-4626 vault that batches and executes FuseAction sequences on-chain."""

    # ── Pure encoders ───────────────────────────────────────────────────────
    # Static `encode_*_calldata` helpers mirror `FusionFactory.encode_clone_calldata`:
    # they produce selector + ABI-encoded args without needing a Web3Context.
    # Callers that route through an external signer (e.g. HTTP signing service,
    # multisig flow) can build calldata once and dispatch it themselves.

    @staticmethod
    def encode_add_fuses_calldata(fuses: list[ChecksumAddress]) -> bytes:
        selector = function_signature_to_4byte_selector("addFuses(address[])")
        return selector + abi_encode(["address[]"], [list(fuses)])

    @staticmethod
    def encode_add_balance_fuse_calldata(
        market_id: MarketId, balance_fuse: ChecksumAddress
    ) -> bytes:
        selector = function_signature_to_4byte_selector(
            "addBalanceFuse(uint256,address)"
        )
        return selector + abi_encode(
            ["uint256", "address"], [int(market_id), balance_fuse]
        )

    @staticmethod
    def encode_grant_market_substrates_calldata(
        market_id: MarketId, substrates: list[bytes]
    ) -> bytes:
        selector = function_signature_to_4byte_selector(
            "grantMarketSubstrates(uint256,bytes32[])"
        )
        return selector + abi_encode(
            ["uint256", "bytes32[]"], [int(market_id), list(substrates)]
        )

    @staticmethod
    def encode_setup_markets_limits_calldata(
        limits: list[tuple[MarketId, Amount]],
    ) -> bytes:
        selector = function_signature_to_4byte_selector(
            "setupMarketsLimits((uint256,uint256)[])"
        )
        return selector + abi_encode(
            ["(uint256,uint256)[]"],
            [[(int(mkt), int(cap)) for mkt, cap in limits]],
        )

    @staticmethod
    def encode_configure_instant_withdrawal_fuses_calldata(
        configs: list[tuple[ChecksumAddress, list[bytes]]],
    ) -> bytes:
        selector = function_signature_to_4byte_selector(
            "configureInstantWithdrawalFuses((address,bytes32[])[])"
        )
        return selector + abi_encode(
            ["(address,bytes32[])[]"],
            [[(fuse, list(params)) for fuse, params in configs]],
        )

    # ── Call builders ───────────────────────────────────────────────────────

    def execute(self, actions: list[FuseAction]) -> Call[None]:
        data = FuseAction.encode_execute_payload(actions, "execute((address,bytes)[])")
        return Call(to=self._address, data=data, ctx=self._ctx)

    def deposit(self, assets: Amount, receiver: ChecksumAddress) -> Call[None]:
        return self._write("deposit(uint256,address)", assets, receiver)

    def mint(self, shares: Shares, receiver: ChecksumAddress) -> Call[None]:
        return self._write("mint(uint256,address)", shares, receiver)

    def withdraw(
        self, assets: Amount, receiver: ChecksumAddress, owner: ChecksumAddress
    ) -> Call[None]:
        return self._write("withdraw(uint256,address,address)", assets, receiver, owner)

    def redeem(
        self, shares: Shares, receiver: ChecksumAddress, owner: ChecksumAddress
    ) -> Call[None]:
        return self._write("redeem(uint256,address,address)", shares, receiver, owner)

    def redeem_from_request(
        self, shares: Shares, receiver: ChecksumAddress, owner: ChecksumAddress
    ) -> Call[None]:
        return self._write(
            "redeemFromRequest(uint256,address,address)", shares, receiver, owner
        )

    def add_fuses(self, fuses: list[ChecksumAddress]) -> Call[None]:
        return self._write("addFuses(address[])", fuses)

    def set_total_supply_cap(self, cap: Amount) -> Call[None]:
        return self._write("setTotalSupplyCap(uint256)", cap)

    def grant_market_substrates(
        self, market_id: MarketId, substrates: list[bytes]
    ) -> Call[None]:
        """Atomist-only configuration call. Each substrate is bytes32 (an address
        left-padded to 32 bytes, or a typed substrate prefix-encoded by the vault).
        """
        return self._write(
            "grantMarketSubstrates(uint256,bytes32[])", market_id, substrates
        )

    def add_balance_fuse(
        self, market_id: MarketId, balance_fuse: ChecksumAddress
    ) -> Call[None]:
        """FUSE_MANAGER-only: link a balance-tracking fuse to a market id."""
        return self._write("addBalanceFuse(uint256,address)", market_id, balance_fuse)

    def setup_markets_limits(self, limits: list[tuple[MarketId, Amount]]) -> Call[None]:
        """ATOMIST-only: set per-market cap in the underlying asset's smallest unit."""
        return self._write("setupMarketsLimits((uint256,uint256)[])", list(limits))

    def configure_instant_withdrawal_fuses(
        self, configs: list[tuple[ChecksumAddress, list[bytes]]]
    ) -> Call[None]:
        """CONFIG_INSTANT_WITHDRAWAL_FUSES-only: (fuse, bytes32[] params) tuples.
        Order defines priority — index 0 is queried first on instant withdraw."""
        normalized = [(fuse, list(params)) for fuse, params in configs]
        return self._write(
            "configureInstantWithdrawalFuses((address,bytes32[])[])", normalized
        )

    def transfer(self, to: ChecksumAddress, value: Amount) -> Call[None]:
        return self._write("transfer(address,uint256)", to, value)

    def approve(self, account: ChecksumAddress, amount: Amount) -> Call[None]:
        return self._write("approve(address,uint256)", account, amount)

    def transfer_from(
        self, _from: ChecksumAddress, to: ChecksumAddress, amount: Amount
    ) -> Call[None]:
        return self._write("transferFrom(address,address,uint256)", _from, to, amount)

    def balance_of(self, account: ChecksumAddress) -> Call[Amount]:
        return self._view(
            "balanceOf(address)", account, output_types=["uint256"], decoder=Amount
        )

    def get_total_supply_cap(self) -> Call[Amount]:
        return self._view(
            "getTotalSupplyCap()", output_types=["uint256"], decoder=Amount
        )

    def max_withdraw(self, account: ChecksumAddress) -> Call[Amount]:
        return self._view(
            "maxWithdraw(address)", account, output_types=["uint256"], decoder=Amount
        )

    def convert_to_shares(self, amount: Amount) -> Call[Shares]:
        return self._view(
            "convertToShares(uint256)",
            amount,
            output_types=["uint256"],
            decoder=Shares,
        )

    def convert_to_assets(self, shares: Shares) -> Call[Amount]:
        return self._view(
            "convertToAssets(uint256)",
            shares,
            output_types=["uint256"],
            decoder=Amount,
        )

    def total_assets_in_market(self, market: MarketId) -> Call[Amount]:
        return self._view(
            "totalAssetsInMarket(uint256)",
            market,
            output_types=["uint256"],
            decoder=Amount,
        )

    def decimals(self) -> Call[Decimals]:
        return self._view("decimals()", output_types=["uint256"], decoder=Decimals)

    def total_assets(self) -> Call[Amount]:
        return self._view("totalAssets()", output_types=["uint256"], decoder=Amount)

    def total_supply(self) -> Call[Amount]:
        return self._view("totalSupply()", output_types=["uint256"], decoder=Amount)

    def name(self) -> Call[str]:
        return self._view("name()", output_types=["string"])

    def underlying_asset_address(self) -> Call[ChecksumAddress]:
        return self._view(
            "asset()", output_types=["address"], decoder=Web3.to_checksum_address
        )

    def get_access_manager_address(self) -> Call[ChecksumAddress]:
        return self._view(
            "getAccessManagerAddress()",
            output_types=["address"],
            decoder=Web3.to_checksum_address,
        )

    def get_rewards_claim_manager_address(self) -> Call[ChecksumAddress]:
        return self._view(
            "getRewardsClaimManagerAddress()",
            output_types=["address"],
            decoder=Web3.to_checksum_address,
        )

    def get_price_oracle_middleware_address(self) -> Call[ChecksumAddress]:
        return self._view(
            "getPriceOracleMiddleware()",
            output_types=["address"],
            decoder=Web3.to_checksum_address,
        )

    def get_fuses(self) -> Call[list[ChecksumAddress]]:
        return self._view(
            "getFuses()", output_types=["address[]"], decoder=_address_list_decoder
        )

    def get_instant_withdrawal_fuses(self) -> Call[list[ChecksumAddress]]:
        return self._view(
            "getInstantWithdrawalFuses()",
            output_types=["address[]"],
            decoder=_address_list_decoder,
        )

    def get_instant_withdrawal_fuses_params(
        self, fuse: ChecksumAddress, index: int
    ) -> Call[list[bytes]]:
        return self._view(
            "getInstantWithdrawalFusesParams(address,uint256)",
            fuse,
            index,
            output_types=["bytes32[]"],
            decoder=list,
        )

    def get_dependency_balance_graph(self, market_id: MarketId) -> Call[list[MarketId]]:
        return self._view(
            "getDependencyBalanceGraph(uint256)",
            market_id,
            output_types=["uint256[]"],
            decoder=_market_id_list_decoder,
        )

    def get_market_substrates(self, market_id: MarketId) -> Call[list[bytes]]:
        return self._view(
            "getMarketSubstrates(uint256)",
            market_id,
            output_types=["bytes32[]"],
            decoder=list,
        )

    # ── Compound methods: event replay, no `Call` shape ─────────────────────

    def get_balance_fuses(self) -> list[BalanceFuse]:
        # Replay Added/Removed events chronologically to mirror on-chain storage.
        # Sorting by (blockNumber, logIndex) handles provider-side ordering quirks
        # and re-add-after-remove cases that a set-subtraction approach misses.
        events: list[tuple[LogReceipt, bool]] = [
            (e, True) for e in self._get_balance_fuse_added_events()
        ] + [(e, False) for e in self._get_balance_fuse_removed_events()]
        events.sort(key=lambda item: (item[0]["blockNumber"], item[0]["logIndex"]))

        state: dict[int, BalanceFuse] = {}
        for event, is_added in events:
            (market_id, fuse) = decode(["uint256", "address"], event["data"])
            checksum = Web3.to_checksum_address(fuse)
            if is_added:
                state[market_id] = BalanceFuse(market_id=market_id, fuse=checksum)
            else:
                current = state.get(market_id)
                if current and str(current.fuse).lower() == str(checksum).lower():
                    del state[market_id]

        return list(state.values())

    def withdraw_manager_address(self) -> ChecksumAddress | None:
        events = self._get_withdraw_manager_changed_events()
        sorted_events = sorted(
            events, key=lambda event: event["blockNumber"], reverse=True
        )
        if sorted_events:
            (decoded_address,) = decode(["address"], sorted_events[0]["data"])
            return Web3.to_checksum_address(decoded_address)
        return None

    def _get_withdraw_manager_changed_events(self) -> list[LogReceipt]:
        event_signature_hash = HexBytes(
            Web3.keccak(text="WithdrawManagerChanged(address)")
        ).to_0x_hex()
        return list(
            self._ctx.get_logs(
                contract_address=self._address, topics=[event_signature_hash]
            )
        )

    def _get_balance_fuse_added_events(self) -> list[LogReceipt]:
        event_signature_hash = HexBytes(
            Web3.keccak(text="BalanceFuseAdded(uint256,address)")
        ).to_0x_hex()
        return list(
            self._ctx.get_logs(
                contract_address=self._address, topics=[event_signature_hash]
            )
        )

    def _get_balance_fuse_removed_events(self) -> list[LogReceipt]:
        event_signature_hash = HexBytes(
            Web3.keccak(text="BalanceFuseRemoved(uint256,address)")
        ).to_0x_hex()
        return list(
            self._ctx.get_logs(
                contract_address=self._address, topics=[event_signature_hash]
            )
        )
