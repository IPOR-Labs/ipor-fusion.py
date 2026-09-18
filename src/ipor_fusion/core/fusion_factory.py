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

`clone()` is a write — call `.send(ctx)` for a real tx, then decode the
created addresses from its receipt with `factory.decode_clone_receipt(receipt)`.
An `.call(ctx)` dry run can preview the next CREATE addresses for an atomic
simulation, but a separate transaction can consume them before broadcast.
For external-signer flows, grab the bytes directly:
`factory.clone(...).calldata`.
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_abi import decode as abi_decode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.types import TxReceipt

from ipor_fusion import addresses
from ipor_fusion.core.context import Web3Context
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

_FUSION_INSTANCE_CREATED = (
    "FusionInstanceCreated(uint256,uint256,string,string,uint8,address,string,uint8,"
    "address,address,address,address)"
)
_FUSION_INSTANCE_CREATED_TYPES = [
    "uint256",
    "uint256",
    "string",
    "string",
    "uint8",
    "address",
    "string",
    "uint8",
    "address",
    "address",
    "address",
    "address",
]
_COMPONENT_EVENTS: dict[str, tuple[str, list[str], int]] = {
    "access_manager": (
        "AccessManagerCreated(uint256,address,uint256)",
        ["uint256", "address", "uint256"],
        1,
    ),
    "rewards_manager": (
        "RewardsManagerCreated(uint256,address,address,address)",
        ["uint256", "address", "address", "address"],
        1,
    ),
    "withdraw_manager": (
        "WithdrawManagerCreated(uint256,address,address)",
        ["uint256", "address", "address"],
        1,
    ),
    "context_manager": (
        "ContextManagerCreated(uint256,address,address[])",
        ["uint256", "address", "address[]"],
        1,
    ),
    "price_manager": (
        "PriceManagerCreated(uint256,address,address)",
        ["uint256", "address", "address"],
        1,
    ),
}


def _event_values(
    receipt: TxReceipt,
    event_signature: str,
    abi_types: list[str],
    *,
    contract_address: ChecksumAddress | None = None,
    instance_index: int | None = None,
) -> tuple:
    topic = bytes(Web3.keccak(text=event_signature))
    matches: list[tuple] = []
    for log in receipt["logs"]:
        if not log["topics"] or bytes(log["topics"][0]) != topic:
            continue
        if contract_address is not None and (
            Web3.to_checksum_address(log["address"]) != contract_address
        ):
            continue
        values = tuple(abi_decode(abi_types, bytes(log["data"])))
        if instance_index is not None and values[0] != instance_index:
            continue
        matches.append(values)
    if len(matches) != 1:
        raise ValueError(
            f"clone receipt must contain exactly one {event_signature} event; "
            f"found {len(matches)}"
        )
    return matches[0]


class FusionFactory(ContractWrapper):
    """Wraps IporFusionFactoryProxy. Use `clone()` for a permissionless
    deploy, `clone_supervised()` for the maintenance-manager-gated path.

    `FusionFactory(ctx)` resolves the proxy for `ctx.chain_id` from the
    shipped ipor-abi snapshot (`ipor_fusion.addresses.factory_proxy`). Passing
    the chain's `IporFusionFactoryImpl` raises before anything is sent: the
    implementation reverts `DaoFeePackagesArrayEmpty()` on `clone()`.
    """

    def __init__(self, ctx: Web3Context, address: ChecksumAddress | None = None):
        if address is None:
            address = addresses.factory_proxy(int(ctx.chain_id))
        elif addresses.is_factory_impl(int(ctx.chain_id), address):
            raise ValueError(
                f"{address} is IporFusionFactoryImpl on chain {ctx.chain_id}; "
                "clone() on the implementation reverts DaoFeePackagesArrayEmpty(). "
                f"Use IporFusionFactoryProxy "
                f"{addresses.factory_proxy(int(ctx.chain_id))} "
                "(FusionFactory(ctx) resolves it for you)"
            )
        super().__init__(ctx, address)

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

        Useful for simulations and off-context flows where the caller has raw
        return bytes from `eth_call`. Do not use a preview's addresses after a
        separately broadcast transaction; another clone can consume the next
        CREATE addresses first. The address fields are normalized to EIP-55.
        """
        (values,) = abi_decode([_FUSION_INSTANCE_TUPLE_TYPE], data)
        return _fusion_instance_decoder(values)

    def decode_clone_receipt(self, receipt: TxReceipt) -> FusionInstance:
        """Return the Fusion instance actually created by ``receipt``.

        The factory proxy's ``FusionInstanceCreated`` log and the component
        factory logs are correlated by instance index. Missing or duplicate
        events raise ``ValueError`` instead of returning an address that was
        only predicted before the transaction landed.
        """
        values = _event_values(
            receipt,
            _FUSION_INSTANCE_CREATED,
            _FUSION_INSTANCE_CREATED_TYPES,
            contract_address=self._address,
        )
        instance_index = int(values[0])
        components = {
            field: Web3.to_checksum_address(
                _event_values(
                    receipt,
                    signature,
                    abi_types,
                    instance_index=instance_index,
                )[address_position]
            )
            for field, (
                signature,
                abi_types,
                address_position,
            ) in _COMPONENT_EVENTS.items()
        }
        return FusionInstance(
            index=instance_index,
            version=int(values[1]),
            asset_name=values[2],
            asset_symbol=values[3],
            asset_decimals=int(values[4]),
            underlying_token=Web3.to_checksum_address(values[5]),
            underlying_token_symbol=values[6],
            underlying_token_decimals=int(values[7]),
            initial_owner=Web3.to_checksum_address(values[8]),
            plasma_vault=Web3.to_checksum_address(values[9]),
            plasma_vault_base=Web3.to_checksum_address(values[10]),
            fee_manager=Web3.to_checksum_address(values[11]),
            **components,
        )

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
          * `.call(ctx)` previews the next CREATE addresses without changing
            state. Use this only inside an atomic simulation.
          * `.send(ctx)` submits the real tx → `TxReceipt`. Pass that receipt
            to `factory.decode_clone_receipt()` for the created addresses.
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
