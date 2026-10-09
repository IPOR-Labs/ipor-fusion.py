from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from eth_abi import decode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from hexbytes import HexBytes
from web3 import Web3
from web3.types import LogReceipt, Timestamp

from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.fuses.base import ZERO_ADDRESS, FuseAction
from ipor_fusion.types import Amount, Decimals, Fee, MarketId, Shares

T = TypeVar("T")

_BALANCE_FUSE_ADDED_TOPIC = HexBytes(
    Web3.keccak(text="BalanceFuseAdded(uint256,address)")
)
_BALANCE_FUSE_REMOVED_TOPIC = HexBytes(
    Web3.keccak(text="BalanceFuseRemoved(uint256,address)")
)


@dataclass(slots=True)
class BalanceFuse:
    """Mapping between a market and its balance-tracking fuse contract."""

    market_id: MarketId
    fuse: ChecksumAddress


@dataclass(slots=True)
class PerformanceFeeData:
    """Vault-level performance fee config; fee_account is a FeeAccount whose
    FEE_MANAGER() leads to the vault's FeeManager. Percentage with 2 decimals
    (10000 = 100%)."""

    fee_account: ChecksumAddress
    fee_in_percentage: Fee


@dataclass(slots=True)
class ManagementFeeData:
    """Vault-level management fee config; same conventions as
    PerformanceFeeData."""

    fee_account: ChecksumAddress
    fee_in_percentage: Fee
    last_update_timestamp: Timestamp


def _market_id_list_decoder(value: list) -> list[MarketId]:
    return [MarketId(v) for v in value]


_BALANCE_OF_SELECTOR = function_signature_to_4byte_selector("balanceOf()")


def _universal_read_decoder(
    output_types: list[str] | None, decoder: Callable[..., Any] | None
) -> Callable[[tuple], Any]:
    """Unwrap ``ReadResult.data`` and, given ``output_types``, decode it the way
    `Call.decode` decodes a view's return data."""

    def _decode(result: tuple) -> Any:
        payload = bytes(result[0])
        if output_types is None:
            return payload
        values = tuple(decode(output_types, payload))
        single: Any = values[0] if len(values) == 1 else values
        return decoder(single) if decoder is not None else single

    return _decode


def _address_list_decoder(value: list) -> list[ChecksumAddress]:
    return [Web3.to_checksum_address(item) for item in value]


def _performance_fee_data_decoder(value: tuple) -> PerformanceFeeData:
    fee_account, fee_in_percentage = value
    return PerformanceFeeData(
        fee_account=Web3.to_checksum_address(fee_account),
        fee_in_percentage=Fee(fee_in_percentage),
    )


def _management_fee_data_decoder(value: tuple) -> ManagementFeeData:
    fee_account, fee_in_percentage, last_update_timestamp = value
    return ManagementFeeData(
        fee_account=Web3.to_checksum_address(fee_account),
        fee_in_percentage=Fee(fee_in_percentage),
        last_update_timestamp=Timestamp(last_update_timestamp),
    )


class PlasmaVault(ContractWrapper):
    """ERC-4626 vault that batches and executes FuseAction sequences on-chain.

    Each method returns a `Call[T]`. For external-signer flows (HTTP signing
    service, multisig), grab the bytes directly: `vault.add_fuses(...).calldata`.
    """

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

    def remove_fuses(self, fuses: list[ChecksumAddress]) -> Call[None]:
        """FUSE_MANAGER-only inverse of `add_fuses`. Used by partial-failure
        rollback flows where a setter batch aborted mid-sequence and prior
        `addFuses` steps must be reverted before the vault is usable."""
        return self._write("removeFuses(address[])", fuses)

    def set_total_supply_cap(self, cap: Amount) -> Call[None]:
        return self._write("setTotalSupplyCap(uint256)", cap)

    def convert_to_public_vault(self) -> Call[None]:
        """ATOMIST-only one-way switch: flips deposit/mint gating from
        WHITELIST_ROLE to PUBLIC_ROLE. Cannot be reverted."""
        return self._write("convertToPublicVault()")

    def grant_market_substrates(
        self, market_id: MarketId, substrates: list[bytes]
    ) -> Call[None]:
        """FUSE_MANAGER-only configuration call. Each substrate is bytes32 (an address
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

    def remove_balance_fuse(
        self, market_id: MarketId, balance_fuse: ChecksumAddress
    ) -> Call[None]:
        """FUSE_MANAGER-only inverse of `add_balance_fuse`. Pairs with
        `remove_fuses` for partial-failure rollback."""
        return self._write(
            "removeBalanceFuse(uint256,address)", market_id, balance_fuse
        )

    def set_pre_hook_implementations(
        self,
        selectors: list[bytes],
        implementations: list[ChecksumAddress],
        substrates: list[list[bytes]],
    ) -> Call[None]:
        """PRE_HOOKS_MANAGER-only configuration call: route each vault function
        selector to a pre-hook implementation (the zero address removes the
        hook) with that hook's bytes32 substrates. One implementation and one
        substrate list per selector, in the same order; a HyperCore vault, for
        example, puts the pending-action hook on ``execute`` and
        ``updateMarketsBalances`` and the capital-flow hook on the deposit,
        mint, withdraw and redeem entry points."""
        if not (len(selectors) == len(implementations) == len(substrates)):
            raise ValueError(
                "selectors, implementations and substrates must have equal length"
            )
        for selector in selectors:
            if len(selector) != 4 or selector == b"\x00" * 4:
                raise ValueError(f"invalid pre-hook selector {selector.hex()!r}")
        for group in substrates:
            for word in group:
                if len(word) != 32:
                    raise ValueError(f"invalid pre-hook substrate {word.hex()!r}")
        return self._write(
            "setPreHookImplementations(bytes4[],address[],bytes32[][])",
            list(selectors),
            list(implementations),
            [list(group) for group in substrates],
        )

    def read(self, target: ChecksumAddress, data: bytes) -> Call[bytes]:
        """``UniversalReader.read``: delegatecall ``data`` on ``target`` in the
        vault's own storage context and return the raw ABI-encoded result, for
        helpers that must see the vault's storage (a market's pending-action
        reader, a balance fuse's ``balanceOf()``)."""
        return self._view(
            "read(address,bytes)",
            target,
            data,
            output_types=["(bytes)"],
            decoder=_universal_read_decoder(None, None),
        )

    def read_as(
        self,
        target: ChecksumAddress,
        data: bytes,
        *,
        output_types: list[str],
        decoder: Callable[..., T] | None = None,
    ) -> Call[T]:
        """:meth:`read` with the result decoded like a view's return value."""
        return self._view(
            "read(address,bytes)",
            target,
            data,
            output_types=["(bytes)"],
            decoder=_universal_read_decoder(output_types, decoder),
        )

    def balance_fuse_value(self, balance_fuse: ChecksumAddress) -> Call[Amount]:
        """``balanceOf()`` of ``balance_fuse`` evaluated in the vault's context:
        the figure ``updateMarketsBalances`` would store for its market now."""
        return self.read_as(
            balance_fuse, _BALANCE_OF_SELECTOR, output_types=["uint256"], decoder=Amount
        )

    def get_pre_hook_selectors(self) -> Call[list[bytes]]:
        """The vault function selectors that currently have a pre-hook."""
        return self._view(
            "getPreHookSelectors()",
            output_types=["bytes4[]"],
            decoder=lambda values: [bytes(v) for v in values],
        )

    def get_pre_hook_implementation(self, selector: bytes) -> Call[ChecksumAddress]:
        """The pre-hook behind ``selector`` (the zero address when none)."""
        if len(selector) != 4:
            raise ValueError(f"invalid pre-hook selector {selector.hex()!r}")
        return self._view(
            "getPreHookImplementation(bytes4)",
            selector,
            output_types=["address"],
            decoder=Web3.to_checksum_address,
        )

    def update_dependency_balance_graphs(
        self, market_ids: list[MarketId], dependencies: list[list[MarketId]]
    ) -> Call[None]:
        """FUSE_MANAGER-only: `dependencies[i]` replaces the full dependency list
        of `market_ids[i]`. After `execute()` touches a market, the vault
        re-measures it plus its graph neighbours."""
        return self._write(
            "updateDependencyBalanceGraphs(uint256[],uint256[][])",
            list(market_ids),
            [list(deps) for deps in dependencies],
        )

    def update_callback_handler(
        self,
        handler: ChecksumAddress,
        sender: ChecksumAddress,
        selector: bytes | bytearray,
    ) -> Call[None]:
        """FUSE_MANAGER-only: route a sender's callback through a handler."""
        if not isinstance(selector, (bytes, bytearray)):
            raise TypeError("selector must be bytes-like")
        if len(selector) != 4:
            raise ValueError(f"selector must be exactly 4 bytes, got {len(selector)}")
        return self._write(
            "updateCallbackHandler(address,address,bytes4)",
            handler,
            sender,
            bytes(selector),
        )

    def setup_markets_limits(self, limits: list[tuple[MarketId, Amount]]) -> Call[None]:
        """ATOMIST-only: set per-market cap in the underlying asset's smallest unit."""
        return self._write("setupMarketsLimits((uint256,uint256)[])", list(limits))

    def update_markets_balances(self, market_ids: list[MarketId]) -> Call[None]:
        """UPDATE_MARKETS_BALANCES-only: re-read and cache each market's balance
        via its balance fuse. `total_assets_in_market` / `total_assets` return a
        *stored* value refreshed by the post-execute hook; call this to resync
        after time passes (e.g. accrued lending interest) without an execute."""
        return self._write("updateMarketsBalances(uint256[])", list(market_ids))

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

    def get_performance_fee_data(self) -> Call[PerformanceFeeData]:
        return self._view(
            "getPerformanceFeeData()",
            output_types=["(address,uint16)"],
            decoder=_performance_fee_data_decoder,
        )

    def get_management_fee_data(self) -> Call[ManagementFeeData]:
        return self._view(
            "getManagementFeeData()",
            output_types=["(address,uint16,uint32)"],
            decoder=_management_fee_data_decoder,
        )

    def get_unrealized_management_fee(self) -> Call[Amount]:
        """Accrued-but-uncollected management fee, in underlying asset units."""
        return self._view(
            "getUnrealizedManagementFee()", output_types=["uint256"], decoder=Amount
        )

    def get_price_oracle_middleware_address(self) -> Call[ChecksumAddress]:
        return self._view(
            "getPriceOracleMiddleware()",
            output_types=["address"],
            decoder=Web3.to_checksum_address,
        )

    def get_price_oracle_address(self) -> Call[ChecksumAddress]:
        """``getPriceOracle()``: the name of ``getPriceOracleMiddleware()`` in
        vaults deployed before the August 2024 audit, which lack the new name."""
        return self._view(
            "getPriceOracle()",
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
        events = sorted(
            self._get_balance_fuse_events(),
            key=lambda event: (event["blockNumber"], event["logIndex"]),
        )

        state: dict[int, BalanceFuse] = {}
        for event in events:
            (market_id, fuse) = decode(["uint256", "address"], event["data"])
            checksum = Web3.to_checksum_address(fuse)
            if HexBytes(event["topics"][0]) == _BALANCE_FUSE_ADDED_TOPIC:
                state[market_id] = BalanceFuse(market_id=market_id, fuse=checksum)
            else:
                current = state.get(market_id)
                if current and str(current.fuse).lower() == str(checksum).lower():
                    del state[market_id]

        return list(state.values())

    def withdraw_manager_address(self) -> ChecksumAddress | None:
        """Latest ``WithdrawManagerChanged`` address, or None when unset.

        Vaults deployed without a withdraw manager emit the event with
        ``address(0)`` (e.g. legacy Base vaults), so the zero address means
        "none" — it is not a contract that can be queried.
        """
        events = self._get_withdraw_manager_changed_events()
        sorted_events = sorted(
            events, key=lambda event: event["blockNumber"], reverse=True
        )
        if not sorted_events:
            return None
        (decoded_address,) = decode(["address"], sorted_events[0]["data"])
        checksum = Web3.to_checksum_address(decoded_address)
        return None if checksum == ZERO_ADDRESS else checksum

    def _get_withdraw_manager_changed_events(self) -> list[LogReceipt]:
        event_signature_hash = HexBytes(
            Web3.keccak(text="WithdrawManagerChanged(address)")
        ).to_0x_hex()
        return list(
            self._ctx.get_logs(
                contract_address=self._address, topics=[event_signature_hash]
            )
        )

    def _get_balance_fuse_events(self) -> list[LogReceipt]:
        """BalanceFuseAdded and BalanceFuseRemoved logs, from one eth_getLogs."""
        return list(
            self._ctx.get_logs(
                contract_address=self._address,
                topics=[
                    [
                        _BALANCE_FUSE_ADDED_TOPIC.to_0x_hex(),
                        _BALANCE_FUSE_REMOVED_TOPIC.to_0x_hex(),
                    ]
                ],
            )
        )
