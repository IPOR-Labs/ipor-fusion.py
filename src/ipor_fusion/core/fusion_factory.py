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

from eth_typing import ChecksumAddress

from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.types import Period


@dataclass(slots=True, frozen=True)
class FusionInstance:  # pylint: disable=too-many-instance-attributes
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
    return FusionInstance(*values)


# Encoded as a tuple because `Call._view` wraps inputs in a single root
# tuple — but `clone()` returns a *struct* (one ABI tuple), so we use the
# Solidity tuple syntax `(...)` to keep eth_abi decoding aligned.
_FUSION_INSTANCE_TUPLE_TYPE = "(" + ",".join(_FUSION_INSTANCE_OUTPUT_TYPES) + ")"


class FusionFactory(ContractWrapper):
    """Wraps IporFusionFactoryProxy. Use `clone()` for a permissionless
    deploy, `clone_supervised()` for the maintenance-manager-gated path."""

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
            "clone(string,string,address,uint256,address,uint256)",
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
