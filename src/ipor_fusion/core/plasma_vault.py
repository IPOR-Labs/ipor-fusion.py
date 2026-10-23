from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TypeVar, cast

from eth_abi import decode, encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import ContractLogicError
from web3.types import LogReceipt, Timestamp

from ipor_fusion.config.roles import Roles
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.core.fuse_action import FuseAction
from ipor_fusion.errors import EmptyCallResultError, UnsupportedVaultVersionError
from ipor_fusion.types import (
    ZERO_ADDRESS,
    AccessManagerAddress,
    Amount,
    Decimals,
    Fee,
    FuseAddress,
    MarketId,
    Period,
    PlasmaVaultAddress,
    RoleId,
    Shares,
)

T = TypeVar("T")

_BALANCE_FUSE_ADDED_TOPIC = HexBytes(
    Web3.keccak(text="BalanceFuseAdded(uint256,address)")
)
_BALANCE_FUSE_REMOVED_TOPIC = HexBytes(
    Web3.keccak(text="BalanceFuseRemoved(uint256,address)")
)

# ERC-7201 slots in PlasmaVaultStorageLib. BalanceFuses.fuseAddresses is the
# struct's first member, so a market's fuse sits at keccak(marketId . slot).
_BALANCE_FUSES_SLOT = 0x150144DD6AF711BAC4392499881EC6649090601BD196A5ECE5174C1400B1F700
# WithdrawManager.manager at the slot the contracts corrected later, and
# the slot older vaults use. The legacy slot overlaps the callback-handler
# storage, so its value counts only if that contract is a WithdrawManager bound
# to this vault.
_WITHDRAW_MANAGER_SLOT = (
    0x465D2FF0062318FE6F4C7E9AC78CFCD70BC86A1D992722875EF83A9770513100
)
_WITHDRAW_MANAGER_LEGACY_SLOT = (
    0xB37E8684757599DA669B8AEA811EE2B3693B2582D2C730FAB3F4965FA2EC3E11
)
_GET_PLASMA_VAULT_ADDRESS = function_signature_to_4byte_selector(
    "getPlasmaVaultAddress()"
)


@dataclass(slots=True)
class BalanceFuse:
    """Mapping between a market and its balance-tracking fuse contract."""

    market_id: MarketId
    fuse: FuseAddress


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


def _market_id_list_decoder(value: tuple[int, ...]) -> list[MarketId]:
    return [MarketId(v) for v in value]


_BALANCE_OF_SELECTOR = function_signature_to_4byte_selector("balanceOf()")

# PlasmaVaultStorageLib.PLASMA_VAULT_TOTAL_ASSETS_IN_ALL_MARKETS, and its value
# before the June 2024 storage rename (ipor-fusion #43).
_TOTAL_ASSETS_IN_ALL_MARKETS_SLOT = int(
    "0x24e02552e88772b8e8fd15f3e6699ba530635ffc6b52322da922b0b497a77300", 16
)
_LEGACY_TOTAL_ASSETS_SLOT = int(
    "0xf660efede6b8071aab61a75191de57a4a8a416b088f961d8c2a18168aa7c7700", 16
)


def _read_result_data(result: tuple[bytes]) -> bytes:
    """``ReadResult.data``, undecoded."""
    return bytes(result[0])


def _universal_read_decoder(
    output_types: list[str], decoder: Callable[..., T] | None
) -> Callable[[tuple[bytes]], T]:
    """Unwrap ``ReadResult.data`` and decode it the way `Call.decode` decodes a
    view's return data."""

    def _decode(result: tuple[bytes]) -> T:
        values = tuple(decode(output_types, _read_result_data(result)))
        single: object = values[0] if len(values) == 1 else values
        return decoder(single) if decoder is not None else cast(T, single)

    return _decode


def _address_in_word(word: bytes) -> ChecksumAddress:
    return Web3.to_checksum_address(bytes(word)[-20:])


def _address_list_decoder(value: tuple[str, ...]) -> list[ChecksumAddress]:
    return [Web3.to_checksum_address(item) for item in value]


def _fuse_list_decoder(value: tuple[str, ...]) -> list[FuseAddress]:
    return [FuseAddress(Web3.to_checksum_address(item)) for item in value]


def _access_manager_decoder(value: str) -> AccessManagerAddress:
    return AccessManagerAddress(Web3.to_checksum_address(value))


def _performance_fee_data_decoder(value: tuple[str, int]) -> PerformanceFeeData:
    fee_account, fee_in_percentage = value
    return PerformanceFeeData(
        fee_account=Web3.to_checksum_address(fee_account),
        fee_in_percentage=Fee(fee_in_percentage),
    )


def _management_fee_data_decoder(
    value: tuple[str, int, int],
) -> ManagementFeeData:
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

    def __init__(self, address: PlasmaVaultAddress):
        super().__init__(address)

    @property
    def address(self) -> PlasmaVaultAddress:
        return PlasmaVaultAddress(self._address)

    def execute(self, actions: list[FuseAction]) -> Call[None]:
        data = FuseAction.encode_execute_payload(actions, "execute((address,bytes)[])")
        return Call(to=self._address, data=data)

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

    def add_fuses(self, fuses: Sequence[FuseAddress]) -> Call[None]:
        return self._write("addFuses(address[])", fuses)

    def remove_fuses(self, fuses: Sequence[FuseAddress]) -> Call[None]:
        """FUSE_MANAGER-only inverse of `add_fuses`. Used by partial-failure
        rollback flows where a setter batch aborted mid-sequence and prior
        `addFuses` steps must be reverted before the vault is usable."""
        return self._write("removeFuses(address[])", fuses)

    def set_total_supply_cap(self, cap: Shares) -> Call[None]:
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
        self, market_id: MarketId, balance_fuse: FuseAddress
    ) -> Call[None]:
        """FUSE_MANAGER-only: link a balance-tracking fuse to a market id."""
        return self._write("addBalanceFuse(uint256,address)", market_id, balance_fuse)

    def remove_balance_fuse(
        self, market_id: MarketId, balance_fuse: FuseAddress
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

    def universal_read(self, target: ChecksumAddress, data: bytes) -> Call[bytes]:
        """``UniversalReader.read``: delegatecall ``data`` on ``target`` in the
        vault's own storage context and return the raw ABI-encoded result, for
        helpers that must see the vault's storage (a market's pending-action
        reader, a balance fuse's ``balanceOf()``)."""
        return self._view(
            "read(address,bytes)",
            target,
            data,
            output_types=["(bytes)"],
            decoder=_read_result_data,
        )

    def universal_read_decoded(
        self,
        target: ChecksumAddress,
        data: bytes,
        *,
        output_types: list[str],
        decoder: Callable[..., T] | None = None,
    ) -> Call[T]:
        """:meth:`universal_read` with the result decoded like a view's return
        value."""
        return self._view(
            "read(address,bytes)",
            target,
            data,
            output_types=["(bytes)"],
            decoder=_universal_read_decoder(output_types, decoder),
        )

    def balance_fuse_value(self, balance_fuse: FuseAddress) -> Call[Amount]:
        """``balanceOf()`` of ``balance_fuse`` evaluated in the vault's context:
        the figure ``updateMarketsBalances`` would store for its market now."""
        return self.universal_read_decoded(
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
        self, configs: Sequence[tuple[FuseAddress, Sequence[bytes]]]
    ) -> Call[None]:
        """CONFIG_INSTANT_WITHDRAWAL_FUSES-only: (fuse, bytes32[] params) tuples.
        Order defines priority — index 0 is queried first on instant withdraw."""
        normalized = [(fuse, list(params)) for fuse, params in configs]
        return self._write(
            "configureInstantWithdrawalFuses((address,bytes32[])[])", normalized
        )

    def transfer(self, to: ChecksumAddress, value: Shares) -> Call[None]:
        return self._write("transfer(address,uint256)", to, value)

    def approve(self, account: ChecksumAddress, amount: Shares) -> Call[None]:
        return self._write("approve(address,uint256)", account, amount)

    def transfer_from(
        self, _from: ChecksumAddress, to: ChecksumAddress, amount: Shares
    ) -> Call[None]:
        return self._write("transferFrom(address,address,uint256)", _from, to, amount)

    def balance_of(self, account: ChecksumAddress) -> Call[Shares]:
        return self._view(
            "balanceOf(address)", account, output_types=["uint256"], decoder=Shares
        )

    def get_total_supply_cap(self) -> Call[Shares]:
        """The maximum total supply, in shares (``2**256 - 1``: uncapped)."""
        return self._view(
            "getTotalSupplyCap()", output_types=["uint256"], decoder=Shares
        )

    def max_withdraw(self, account: ChecksumAddress) -> Call[Amount]:
        return self._view(
            "maxWithdraw(address)", account, output_types=["uint256"], decoder=Amount
        )

    def max_redeem(self, account: ChecksumAddress) -> Call[Shares]:
        return self._view(
            "maxRedeem(address)", account, output_types=["uint256"], decoder=Shares
        )

    def max_deposit(self, receiver: ChecksumAddress) -> Call[Amount]:
        """Assets ``receiver`` can still deposit under the total supply cap."""
        return self._view(
            "maxDeposit(address)", receiver, output_types=["uint256"], decoder=Amount
        )

    def max_mint(self, receiver: ChecksumAddress) -> Call[Shares]:
        """Shares ``receiver`` can still mint under the total supply cap."""
        return self._view(
            "maxMint(address)", receiver, output_types=["uint256"], decoder=Shares
        )

    def preview_deposit(self, assets: Amount) -> Call[Shares]:
        """Shares a deposit of ``assets`` mints, after the deposit fee."""
        return self._view(
            "previewDeposit(uint256)", assets, output_types=["uint256"], decoder=Shares
        )

    def preview_mint(self, shares: Shares) -> Call[Amount]:
        """Assets a mint of ``shares`` takes, the deposit fee included."""
        return self._view(
            "previewMint(uint256)", shares, output_types=["uint256"], decoder=Amount
        )

    def preview_withdraw(self, assets: Amount) -> Call[Shares]:
        """Shares an instant withdrawal of ``assets`` burns, the withdraw fee
        included."""
        return self._view(
            "previewWithdraw(uint256)", assets, output_types=["uint256"], decoder=Shares
        )

    def preview_redeem(self, shares: Shares) -> Call[Amount]:
        """Assets an instant redemption of ``shares`` pays, after the withdraw fee."""
        return self._view(
            "previewRedeem(uint256)", shares, output_types=["uint256"], decoder=Amount
        )

    def deposit_with_permit(
        self,
        assets: Amount,
        receiver: ChecksumAddress,
        deadline: Timestamp,
        v: int,
        r: bytes,
        s: bytes,
    ) -> Call[None]:
        """`deposit` with an EIP-2612 permit of the asset signed by the caller.
        The vault deposits even when the permit call fails (an allowance already
        in place, or a front-run permit)."""
        return self._write(
            "depositWithPermit(uint256,address,uint256,uint8,bytes32,bytes32)",
            assets,
            receiver,
            deadline,
            v,
            r,
            s,
        )

    def is_fuse_supported(self, fuse: FuseAddress) -> Call[bool]:
        """Whether ``fuse`` is registered (`add_fuses`), so `execute` may call it."""
        return self._view(
            "isFuseSupported(address)", fuse, output_types=["bool"], decoder=bool
        )

    def is_balance_fuse_supported(
        self, market: MarketId, fuse: FuseAddress
    ) -> Call[bool]:
        """Whether ``fuse`` is the balance fuse of ``market``."""
        return self._view(
            "isBalanceFuseSupported(uint256,address)",
            market,
            fuse,
            output_types=["bool"],
            decoder=bool,
        )

    def is_market_substrate_granted(
        self, market: MarketId, substrate: bytes
    ) -> Call[bool]:
        """Whether the 32-byte ``substrate`` is granted on ``market``."""
        if len(substrate) != 32:
            raise ValueError(f"substrate must be 32 bytes, got {len(substrate)}")
        return self._view(
            "isMarketSubstrateGranted(uint256,bytes32)",
            market,
            substrate,
            output_types=["bool"],
            decoder=bool,
        )

    def is_markets_limits_activated(self) -> Call[bool]:
        return self._view(
            "isMarketsLimitsActivated()", output_types=["bool"], decoder=bool
        )

    def get_market_limit(self, market: MarketId) -> Call[int]:
        """The most of the vault's total assets ``market`` may hold, as a WAD
        share (``10**18`` is 100%), enforced only while limits are activated."""
        return self._view(
            "getMarketLimit(uint256)", market, output_types=["uint256"], decoder=int
        )

    def activate_markets_limits(self) -> Call[None]:
        return self._write("activateMarketsLimits()")

    def deactivate_markets_limits(self) -> Call[None]:
        return self._write("deactivateMarketsLimits()")

    def set_price_oracle_middleware(self, middleware: ChecksumAddress) -> Call[None]:
        """The vault rejects a middleware whose quote currency differs from the
        current one."""
        return self._write("setPriceOracleMiddleware(address)", middleware)

    def configure_performance_fee(
        self, fee_account: ChecksumAddress, fee: Fee
    ) -> Call[None]:
        """``fee`` in percent with 2 decimals (10000 = 100%), as
        `get_performance_fee_data` reports it."""
        return self._write("configurePerformanceFee(address,uint256)", fee_account, fee)

    def configure_management_fee(
        self, fee_account: ChecksumAddress, fee: Fee
    ) -> Call[None]:
        """``fee`` in percent with 2 decimals (10000 = 100%), as
        `get_management_fee_data` reports it."""
        return self._write("configureManagementFee(address,uint256)", fee_account, fee)

    def set_rewards_claim_manager_address(
        self, rewards_claim_manager: ChecksumAddress
    ) -> Call[None]:
        return self._write(
            "setRewardsClaimManagerAddress(address)", rewards_claim_manager
        )

    def enable_transfer_shares(self) -> Call[None]:
        """Make share `transfer` and `transferFrom` public (PUBLIC_ROLE on both
        selectors); only an access manager admin can map them back."""
        return self._write("enableTransferShares()")

    def set_minimal_execution_delays_for_roles(
        self, delays: Sequence[tuple[RoleId | Roles, Period]]
    ) -> Call[None]:
        """The floor of each role's execution delay; the access manager refuses
        to grant the role with a shorter one."""
        return self._write(
            "setMinimalExecutionDelaysForRoles(uint64[],uint256[])",
            [role for role, _ in delays],
            [delay for _, delay in delays],
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

    def total_supply(self) -> Call[Shares]:
        return self._view("totalSupply()", output_types=["uint256"], decoder=Shares)

    def name(self) -> Call[str]:
        return self._view("name()", output_types=["string"])

    def underlying_asset_address(self) -> Call[ChecksumAddress]:
        return self._view(
            "asset()", output_types=["address"], decoder=Web3.to_checksum_address
        )

    def get_access_manager_address(self) -> Call[AccessManagerAddress]:
        return self._view(
            "getAccessManagerAddress()",
            output_types=["address"],
            decoder=_access_manager_decoder,
        )

    def get_rewards_claim_manager_address(self) -> Call[ChecksumAddress]:
        return self._view(
            "getRewardsClaimManagerAddress()",
            output_types=["address"],
            decoder=Web3.to_checksum_address,
        )

    def get_rewards_manager_address(self) -> Call[ChecksumAddress]:
        """The getter of vaults deployed before the manager was renamed
        RewardsClaimManager (June 2024)."""
        return self._view(
            "getRewardsManagerAddress()",
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

    def read_price_oracle_address(self, ctx: Web3Context) -> ChecksumAddress:
        """The vault's price oracle: ``getPriceOracleMiddleware()``, or
        ``getPriceOracle()`` on vaults deployed before the August 2024 audit.

        Raises `UnsupportedVaultVersionError` when the vault answers neither
        with data; a revert or RPC error propagates."""
        for call in (
            self.get_price_oracle_middleware_address(),
            self.get_price_oracle_address(),
        ):
            try:
                return call.call(ctx)
            except EmptyCallResultError:
                continue
        raise UnsupportedVaultVersionError(
            f"Plasma Vault {self._address} implements neither "
            "getPriceOracleMiddleware() nor getPriceOracle()"
        )

    def get_fuses(self) -> Call[list[FuseAddress]]:
        return self._view(
            "getFuses()", output_types=["address[]"], decoder=_fuse_list_decoder
        )

    def get_instant_withdrawal_fuses(self) -> Call[list[FuseAddress]]:
        return self._view(
            "getInstantWithdrawalFuses()",
            output_types=["address[]"],
            decoder=_fuse_list_decoder,
        )

    def get_instant_withdrawal_fuses_params(
        self, fuse: FuseAddress, index: int
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

    def get_active_markets_in_balance_fuses(self) -> Call[list[MarketId]]:
        """Markets with a balance fuse, in storage order. Vaults deployed
        before the view existed revert or return nothing."""
        return self._view(
            "getActiveMarketsInBalanceFuses()",
            output_types=["uint256[]"],
            decoder=_market_id_list_decoder,
        )

    # ── Composite reads: storage, event replay or dependent reads on ``ctx`` ──

    def read_balance_fuses(self, ctx: Web3Context) -> list[BalanceFuse]:
        """The balance fuse of every market that has one.

        Read from storage (`getActiveMarketsInBalanceFuses` plus each market's
        fuse slot, at ``ctx.default_block``); vaults that predate the view
        fall back to replaying BalanceFuseAdded/BalanceFuseRemoved logs."""
        try:
            market_ids = self.get_active_markets_in_balance_fuses().call(ctx)
        except (EmptyCallResultError, ContractLogicError):
            return self._replay_balance_fuses(ctx)
        # The list can repeat a market (seen for the zero-balance market); the
        # fuse mapping holds one fuse per market either way.
        market_ids = list(dict.fromkeys(market_ids))
        with ThreadPoolExecutor(max(1, min(len(market_ids), 16))) as pool:
            fuses = list(
                pool.map(lambda m: self._balance_fuse_in_storage(ctx, m), market_ids)
            )
        return [
            BalanceFuse(market_id=market_id, fuse=fuse)
            for market_id, fuse in zip(market_ids, fuses, strict=True)
        ]

    def read_stored_markets_total(self, ctx: Web3Context) -> Amount:
        """The global market total the vault stores (the
        ``totalAssetsInAllMarkets`` term of ``totalAssets``), read from its
        ERC-7201 slot; vaults built before June 2024 keep it at an earlier slot,
        read when the current one is empty."""
        for slot in (_TOTAL_ASSETS_IN_ALL_MARKETS_SLOT, _LEGACY_TOTAL_ASSETS_SLOT):
            if value := int.from_bytes(ctx.get_storage_at(self._address, slot)):
                return Amount(value)
        return Amount(0)

    def _address_at(self, ctx: Web3Context, slot: int) -> ChecksumAddress:
        return _address_in_word(ctx.get_storage_at(self._address, slot))

    def _is_own_withdraw_manager(
        self, ctx: Web3Context, candidate: ChecksumAddress
    ) -> bool:
        call = Call(
            to=candidate,
            data=_GET_PLASMA_VAULT_ADDRESS,
            output_types=["address"],
            decoder=Web3.to_checksum_address,
        )
        try:
            return call.call(ctx) == self._address
        except (EmptyCallResultError, ContractLogicError):
            return False

    def _balance_fuse_in_storage(
        self, ctx: Web3Context, market_id: MarketId
    ) -> FuseAddress:
        slot = Web3.keccak(
            encode(["uint256", "uint256"], [market_id, _BALANCE_FUSES_SLOT])
        )
        return FuseAddress(self._address_at(ctx, int.from_bytes(slot)))

    def _replay_balance_fuses(self, ctx: Web3Context) -> list[BalanceFuse]:
        # Replay Added/Removed events chronologically to mirror on-chain storage.
        # Sorting by (blockNumber, logIndex) handles provider-side ordering quirks
        # and re-add-after-remove cases that a set-subtraction approach misses.
        events = sorted(
            self._get_balance_fuse_events(ctx),
            key=lambda event: (event["blockNumber"], event["logIndex"]),
        )

        state: dict[MarketId, BalanceFuse] = {}
        for event in events:
            (market_id, fuse) = decode(["uint256", "address"], event["data"])
            checksum = FuseAddress(Web3.to_checksum_address(fuse))
            if HexBytes(event["topics"][0]) == _BALANCE_FUSE_ADDED_TOPIC:
                state[market_id] = BalanceFuse(market_id=market_id, fuse=checksum)
            else:
                current = state.get(market_id)
                if current and str(current.fuse).lower() == str(checksum).lower():
                    del state[market_id]

        return list(state.values())

    def read_withdraw_manager_address(self, ctx: Web3Context) -> ChecksumAddress | None:
        """The vault's WithdrawManager, or None when unset.

        Read from storage, as the contracts resolve it: the WithdrawManager
        slot, else the legacy slot of older vaults (accepted only when the
        contract there names this vault as its Plasma Vault). Storage is what
        the vault uses: a manager swapped in by a maintenance fuse emits no
        ``WithdrawManagerChanged``, so the events can name a retired one. Only
        when neither slot answers does the latest event decide. Vaults
        deployed without a withdraw manager emit the event with ``address(0)``
        (e.g. legacy Base vaults), so the zero address means "none" — it is
        not a contract that can be queried.
        """
        stored = self._address_at(ctx, _WITHDRAW_MANAGER_SLOT)
        if stored != ZERO_ADDRESS:
            return stored
        legacy = self._address_at(ctx, _WITHDRAW_MANAGER_LEGACY_SLOT)
        if legacy != ZERO_ADDRESS and self._is_own_withdraw_manager(ctx, legacy):
            return legacy
        events = self._get_withdraw_manager_changed_events(ctx)
        sorted_events = sorted(
            events, key=lambda event: event["blockNumber"], reverse=True
        )
        if not sorted_events:
            return None
        (decoded_address,) = decode(["address"], sorted_events[0]["data"])
        checksum = Web3.to_checksum_address(decoded_address)
        return None if checksum == ZERO_ADDRESS else checksum

    def _get_withdraw_manager_changed_events(
        self, ctx: Web3Context
    ) -> list[LogReceipt]:
        event_signature_hash = HexBytes(
            Web3.keccak(text="WithdrawManagerChanged(address)")
        ).to_0x_hex()
        return list(
            ctx.get_logs(contract_address=self._address, topics=[event_signature_hash])
        )

    def _get_balance_fuse_events(self, ctx: Web3Context) -> list[LogReceipt]:
        """BalanceFuseAdded and BalanceFuseRemoved logs, from one eth_getLogs."""
        return list(
            ctx.get_logs(
                contract_address=self._address,
                topics=[
                    [
                        _BALANCE_FUSE_ADDED_TOPIC.to_0x_hex(),
                        _BALANCE_FUSE_REMOVED_TOPIC.to_0x_hex(),
                    ]
                ],
            )
        )
