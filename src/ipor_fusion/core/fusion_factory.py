"""IporFusionFactoryProxy wrapper — deploys a full Fusion vault stack
(plasmaVault + accessManager + 6 managers) in a single tx.

Deployed signature on BASE (verified 2026-05-12 against impl bytecode):
  clone(string assetName_, string assetSymbol_, address underlyingToken_,
        uint256 redemptionDelayInSeconds_, address owner_,
        uint256 daoFeePackageIndex_) → FusionInstance

Note: the ipor-abi registry publishes a 5-arg shape (no daoFeePackageIndex);
that's stale — the deployed factory impl at
0x610152a79be7f2aa3aa70520c9331c18fe8d33b7 only exposes the 6-arg variant
(selector 0x8697b10a). Always trust the impl bytecode over the registry
when they disagree.

`clone()` is a write — call `.send(ctx)` for a real tx, or `.call(ctx)`
for an eth_call dry-run preview that returns the deterministic CREATE2
addresses without burning gas. For external-signer flows, grab the bytes
directly: `factory.clone(...).calldata`.
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_abi import decode as abi_decode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.types import Period


@dataclass(slots=True, frozen=True)
class CloneArgs:
    """Decoded `FusionFactory.clone(...)` calldata. Mirrors the on-chain
    parameter list 1:1 — useful for off-context flows that inspect a pending
    tx's data (e.g. operator notifications, multisig review UIs)."""

    asset_name: str
    asset_symbol: str
    underlying_token: ChecksumAddress
    redemption_delay_seconds: int
    owner: ChecksumAddress
    dao_fee_package_index: int


@dataclass(slots=True, frozen=True)
class FusionInstance:
    """17-field tuple returned by FusionFactory.clone()."""

    index: int
    version: int
    asset_name: str
    asset_symbol: str
    asset_decimals: int
    underlying_token: ChecksumAddress
    underlying_token_symbol: str
    underlying_token_decimals: int
    initial_owner: ChecksumAddress
    plasma_vault: ChecksumAddress
    plasma_vault_base: ChecksumAddress
    access_manager: ChecksumAddress
    fee_manager: ChecksumAddress
    rewards_manager: ChecksumAddress
    withdraw_manager: ChecksumAddress
    context_manager: ChecksumAddress
    price_manager: ChecksumAddress


_FUSION_INSTANCE_OUTPUT_TYPES: list[str] = [
    "uint256",  # index
    "uint256",  # version
    "string",  # assetName
    "string",  # assetSymbol
    "uint8",  # assetDecimals
    "address",  # underlyingToken
    "string",  # underlyingTokenSymbol
    "uint8",  # underlyingTokenDecimals
    "address",  # initialOwner
    "address",  # plasmaVault
    "address",  # plasmaVaultBase
    "address",  # accessManager
    "address",  # feeManager
    "address",  # rewardsManager
    "address",  # withdrawManager
    "address",  # contextManager
    "address",  # priceManager
]


def _fusion_instance_decoder(values: tuple) -> FusionInstance:
    # eth_abi.decode returns address fields as lowercase hex strings; the
    # FusionInstance dataclass declares them as ChecksumAddress, so normalize
    # to EIP-55 before constructing — otherwise equality against checksummed
    # inputs (e.g. `Web3.to_checksum_address(...)`) silently fails.
    (
        index,
        version,
        asset_name,
        asset_symbol,
        asset_decimals,
        underlying_token,
        underlying_token_symbol,
        underlying_token_decimals,
        initial_owner,
        plasma_vault,
        plasma_vault_base,
        access_manager,
        fee_manager,
        rewards_manager,
        withdraw_manager,
        context_manager,
        price_manager,
    ) = values
    addr = Web3.to_checksum_address
    return FusionInstance(
        index=index,
        version=version,
        asset_name=asset_name,
        asset_symbol=asset_symbol,
        asset_decimals=asset_decimals,
        underlying_token=addr(underlying_token),
        underlying_token_symbol=underlying_token_symbol,
        underlying_token_decimals=underlying_token_decimals,
        initial_owner=addr(initial_owner),
        plasma_vault=addr(plasma_vault),
        plasma_vault_base=addr(plasma_vault_base),
        access_manager=addr(access_manager),
        fee_manager=addr(fee_manager),
        rewards_manager=addr(rewards_manager),
        withdraw_manager=addr(withdraw_manager),
        context_manager=addr(context_manager),
        price_manager=addr(price_manager),
    )


# Encoded as a tuple because `Call._view` wraps inputs in a single root
# tuple — but `clone()` returns a *struct* (one ABI tuple), so we use the
# Solidity tuple syntax `(...)` to keep eth_abi decoding aligned.
_FUSION_INSTANCE_TUPLE_TYPE = "(" + ",".join(_FUSION_INSTANCE_OUTPUT_TYPES) + ")"


_CLONE_ARG_TYPES = ["string", "string", "address", "uint256", "address", "uint256"]


class FusionFactory(ContractWrapper):
    """Wraps IporFusionFactoryProxy. Use `clone()` for a permissionless
    deploy, `clone_supervised()` for the maintenance-manager-gated path."""

    #: Solidity signature of the deployed `clone(...)` entry-point. Exported
    #: so off-context flows (decoders, audit logs, signer UIs) can reference
    #: a single source of truth instead of hard-coding the string.
    CLONE_FUNC_SIG: str = "clone(string,string,address,uint256,address,uint256)"

    #: 4-byte selector of `CLONE_FUNC_SIG` (`0x8697b10a` on the deployed
    #: BASE proxy). Use to identify pending clone txs from raw calldata.
    CLONE_SELECTOR: bytes = function_signature_to_4byte_selector(
        "clone(string,string,address,uint256,address,uint256)"
    )

    @staticmethod
    def decode_clone_result(data: bytes) -> FusionInstance:
        """Decode raw ABI-encoded `clone()` return bytes → `FusionInstance`.

        Useful for off-context flows where caller has the raw bytes from
        their own `eth_call` (e.g. agent runtimes that broadcast via an
        external signing service and replay the preview at a historical
        block to recover CREATE2-deterministic addresses). The address
        fields are normalized to EIP-55 checksum.
        """
        (values,) = abi_decode([_FUSION_INSTANCE_TUPLE_TYPE], data)
        return _fusion_instance_decoder(values)

    @classmethod
    def decode_clone_calldata(cls, calldata: bytes) -> CloneArgs:
        """Decode raw `clone(...)` calldata (selector + ABI-encoded args) → `CloneArgs`.

        Symmetric to `decode_clone_result` but for the *input* side: pulls
        asset name / symbol / underlying / owner / etc. out of a pending tx's
        `data` field. Operator notification flows use this to render a
        human-readable summary of a sign request before the broadcast.

        Raises `ValueError` if the selector does not match `CLONE_SELECTOR`.
        Address fields are normalized to EIP-55 checksum.
        """
        if len(calldata) < 4 or calldata[:4] != cls.CLONE_SELECTOR:
            actual = calldata[:4].hex() if len(calldata) >= 4 else "<too short>"
            raise ValueError(
                f"calldata selector 0x{actual} does not match "
                f"FusionFactory.clone selector 0x{cls.CLONE_SELECTOR.hex()}"
            )
        values = abi_decode(_CLONE_ARG_TYPES, calldata[4:])
        (
            asset_name,
            asset_symbol,
            underlying_token,
            redemption_delay_seconds,
            owner,
            dao_fee_package_index,
        ) = values
        return CloneArgs(
            asset_name=asset_name,
            asset_symbol=asset_symbol,
            underlying_token=Web3.to_checksum_address(underlying_token),
            redemption_delay_seconds=int(redemption_delay_seconds),
            owner=Web3.to_checksum_address(owner),
            dao_fee_package_index=int(dao_fee_package_index),
        )

    def clone(
        self,
        asset_name: str,
        asset_symbol: str,
        underlying_token: ChecksumAddress,
        redemption_delay_seconds: Period,
        owner: ChecksumAddress,
        dao_fee_package_index: int = 0,
    ) -> Call[FusionInstance]:
        """Deploy a full Fusion vault stack.

        Returns a `Call[FusionInstance]`:
          * `.call(ctx)` runs `eth_call` → typed FusionInstance preview
            with deterministic CREATE2 addresses (no gas, no state change).
          * `.send(ctx)` submits the real tx → `TxReceipt`. Addresses match
            the prior `.call()` preview because CREATE2 is deterministic
            for the current factory index.
        """
        return self._view(
            self.CLONE_FUNC_SIG,
            asset_name,
            asset_symbol,
            underlying_token,
            redemption_delay_seconds,
            owner,
            dao_fee_package_index,
            output_types=[_FUSION_INSTANCE_TUPLE_TYPE],
            decoder=_fusion_instance_decoder,
        )

    def clone_supervised(
        self,
        asset_name: str,
        asset_symbol: str,
        underlying_token: ChecksumAddress,
        redemption_delay_seconds: Period,
        owner: ChecksumAddress,
        dao_fee_package_index: int = 0,
    ) -> Call[FusionInstance]:
        """Same shape as `clone()` but gated by `MAINTENANCE_MANAGER_ROLE`."""
        return self._view(
            "cloneSupervised(string,string,address,uint256,address,uint256)",
            asset_name,
            asset_symbol,
            underlying_token,
            redemption_delay_seconds,
            owner,
            dao_fee_package_index,
            output_types=[_FUSION_INSTANCE_TUPLE_TYPE],
            decoder=_fusion_instance_decoder,
        )
