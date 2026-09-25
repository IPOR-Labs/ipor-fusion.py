"""Transport-agnostic crosschain wire vocabulary.

Mirrors ``contracts/crosschain/lib/CrosschainConstants.sol`` and
``CrosschainMessages.sol`` (the Stargate/LayerZero envelope) plus
``contracts/crosschain/ccip/lib/CcipMessages.sol`` (the CCIP envelope). Both
transports carry the same ``Command`` struct and the same per-action
``actionData`` tuples; only the envelope discriminators differ. Names follow
the Solidity sources.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from eth_abi import decode, encode
from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.fuses.base import _validate_not_zero_address
from ipor_fusion.types import Amount, Shares

#: ``CrosschainConstants.CODEC_VERSION`` -- the Stargate/LayerZero envelope version.
CODEC_VERSION = 3
#: ``CcipMessages.VERSION`` -- the CCIP envelope version.
CCIP_VERSION = 1


class BusinessAction(IntEnum):
    """``CrosschainConstants.BusinessAction``: the action inside a ``COMMAND``."""

    NONE = 0
    DEPOSIT = 1
    REDEEM = 2
    REQUEST_SHARES = 3
    REDEEM_FROM_REQUEST = 4


class MsgType(IntEnum):
    """``CrosschainConstants.MsgType``: the LayerZero envelope discriminator."""

    NONE = 0
    CREATE_DISPATCHER = 1
    DEPLOYMENT_ACK = 2
    DEPLOYMENT_NACK = 3
    UPDATE_VAULTS = 4
    OUTBOUND_TRANSFER = 5
    TRANSFER_SETTLED = 6
    RETURN_ASSETS_REQUEST = 7
    RETURN_STARTED = 8
    COMMAND = 9
    RETRY_COMMAND = 10
    CANCEL_COMMAND = 11
    ACK = 12
    NACK = 13
    RETURN_REJECTED = 14


class CcipMsgType(IntEnum):
    """``CcipMessages.CcipMsgType``: the CCIP envelope discriminator."""

    NONE = 0
    OUTBOUND_TRANSFER = 1
    TRANSFER_SETTLED = 2
    RETURN_REQUEST = 3
    RETURN_TRANSFER = 4
    COMMAND = 5
    COMMAND_ACK = 6
    COMMAND_NACK = 7
    UPDATE_VAULTS = 8
    CREATE_DISPATCHER = 9
    DEPLOYMENT_ACK = 10
    DEPLOYMENT_NACK = 11
    UPDATE_VAULTS_ACK = 12
    BALANCE_REQUEST = 13
    BALANCE_REPORT = 14
    RETURN_REJECTED = 15
    RETRY_COMMAND = 16
    CANCEL_COMMAND = 17


class CommandStatus(IntEnum):
    """``CrosschainConstants.CommandStatus``: dispatcher-side command lifecycle."""

    NONE = 0
    PENDING = 1
    SUCCEEDED = 2
    FAILED = 3
    CANCELLED = 4


class DeploymentStatus(IntEnum):
    """``CrosschainConstants.DeploymentStatus``: a dispatcher deployment request."""

    NONE = 0
    PENDING = 1
    DEPLOYED = 2
    FAILED = 3
    CANCELLED = 4
    REJECTED_OCCUPIED = 5


_ACTION_TYPES: dict[BusinessAction, list[str]] = {
    BusinessAction.DEPOSIT: ["address", "uint256", "uint256"],
    BusinessAction.REDEEM: ["address", "uint256", "uint256"],
    BusinessAction.REQUEST_SHARES: ["address", "address", "uint256"],
    BusinessAction.REDEEM_FROM_REQUEST: [
        "address",
        "address",
        "uint256",
        "uint256",
        "address",
    ],
}
# Index of the target PlasmaVault inside each action tuple -- the command fuse
# validates that vault against the REMOTE_VAULT substrates of the destination.
_ACTION_VAULT_INDEX: dict[BusinessAction, int] = {
    BusinessAction.DEPOSIT: 0,
    BusinessAction.REDEEM: 0,
    BusinessAction.REQUEST_SHARES: 1,
    BusinessAction.REDEEM_FROM_REQUEST: 1,
}

_ZERO_AMOUNT = Amount(0)
_ZERO_SHARES = Shares(0)

#: Solidity tuple type of ``Command`` as it appears inside fuse enter data.
COMMAND_TUPLE_TYPE = "(bytes32,uint64,uint64,uint8,bytes)"


@dataclass(frozen=True, slots=True)
class Command:
    """``CrosschainMessages.Command``: one business command for a dispatcher.

    Build one with :meth:`deposit`, :meth:`redeem`, :meth:`request_shares` or
    :meth:`redeem_from_request` (the ``encode*Action`` helpers of the Solidity
    library). Amounts are in the executor's *shared* decimals (6 for USDC,
    which equals the local unit when local and shared decimals match). The
    executor allocates ``command_id``, ``sequence`` and ``command_config_epoch``
    when it sends, so a fresh command leaves them zero; the CCIP retry and
    cancel operations address an existing command by its ``command_id``.
    """

    action: BusinessAction
    action_data: bytes
    command_id: bytes = bytes(32)
    sequence: int = 0
    command_config_epoch: int = 0

    def __post_init__(self) -> None:
        if len(self.command_id) != 32:
            raise ValueError(f"command_id must be 32 bytes, got {len(self.command_id)}")

    def as_tuple(self) -> tuple[bytes, int, int, int, bytes]:
        """``(commandId, sequence, commandConfigEpoch, action, actionData)``."""
        return (
            self.command_id,
            self.sequence,
            self.command_config_epoch,
            int(self.action),
            self.action_data,
        )

    @classmethod
    def from_tuple(cls, values: tuple) -> Command:
        command_id, sequence, config_epoch, action, action_data = values
        return cls(
            action=BusinessAction(action),
            action_data=bytes(action_data),
            command_id=bytes(command_id),
            sequence=int(sequence),
            command_config_epoch=int(config_epoch),
        )

    @property
    def target_vault(self) -> ChecksumAddress | None:
        """The remote PlasmaVault this command acts on, or ``None`` for an
        action without a vault target."""
        index = _ACTION_VAULT_INDEX.get(self.action)
        if index is None:
            return None
        return Web3.to_checksum_address(self.decode_action_data()[index])

    def decode_action_data(self) -> tuple:
        """The per-action tuple behind ``action_data``
        (``CrosschainMessages.decode*Action``)."""
        types = _ACTION_TYPES.get(self.action)
        if types is None:
            raise ValueError(f"action {self.action.name} carries no action data")
        return tuple(decode(types, self.action_data))

    @classmethod
    def deposit(
        cls,
        vault: ChecksumAddress,
        amount_sd: Amount,
        min_shares: Shares = _ZERO_SHARES,
    ) -> Command:
        """``encodeDepositAction(vault, amountSD, minShares)``: the dispatcher
        deposits ``amount_sd`` of its tracked idle into ``vault`` (clamped to
        the idle it holds) and requires at least ``min_shares`` back."""
        _validate_not_zero_address(vault, "vault")
        return cls(
            BusinessAction.DEPOSIT,
            _encode_action(BusinessAction.DEPOSIT, [vault, amount_sd, min_shares]),
        )

    @classmethod
    def redeem(
        cls, vault: ChecksumAddress, shares: Shares, min_assets: Amount = _ZERO_AMOUNT
    ) -> Command:
        """``encodeRedeemAction(vault, shares, minAssets)``: the dispatcher
        redeems ``shares`` of ``vault`` into its tracked idle."""
        _validate_not_zero_address(vault, "vault")
        return cls(
            BusinessAction.REDEEM,
            _encode_action(BusinessAction.REDEEM, [vault, shares, min_assets]),
        )

    @classmethod
    def request_shares(
        cls,
        withdraw_manager: ChecksumAddress,
        plasma_vault: ChecksumAddress,
        shares: Shares,
    ) -> Command:
        """``encodeRequestSharesAction(withdrawManager, plasmaVault, shares)``:
        enter the remote PlasmaVault's scheduled-withdrawal queue."""
        _validate_not_zero_address(withdraw_manager, "withdraw_manager")
        _validate_not_zero_address(plasma_vault, "plasma_vault")
        return cls(
            BusinessAction.REQUEST_SHARES,
            _encode_action(
                BusinessAction.REQUEST_SHARES, [withdraw_manager, plasma_vault, shares]
            ),
        )

    @classmethod
    def redeem_from_request(
        cls,
        withdraw_manager: ChecksumAddress,
        plasma_vault: ChecksumAddress,
        shares: Shares,
        min_assets: Amount,
        receiver: ChecksumAddress,
    ) -> Command:
        """``encodeRedeemFromRequestAction(...)``: claim a released scheduled
        withdrawal."""
        _validate_not_zero_address(withdraw_manager, "withdraw_manager")
        _validate_not_zero_address(plasma_vault, "plasma_vault")
        _validate_not_zero_address(receiver, "receiver")
        return cls(
            BusinessAction.REDEEM_FROM_REQUEST,
            _encode_action(
                BusinessAction.REDEEM_FROM_REQUEST,
                [withdraw_manager, plasma_vault, shares, min_assets, receiver],
            ),
        )


#: The all-zero command the command fuses expect on operations that carry none.
EMPTY_COMMAND = Command(BusinessAction.NONE, b"")


def _encode_action(action: BusinessAction, values: list) -> bytes:
    return encode(_ACTION_TYPES[action], values)


def decode_envelope(raw: bytes) -> tuple[MsgType, bytes]:
    """``CrosschainMessages.decodeEnvelope``: unwrap
    ``abi.encode(uint8 codecVersion, uint8 msgType, bytes data)``."""
    version, msg_type, data = decode(["uint8", "uint8", "bytes"], raw)
    if version != CODEC_VERSION:
        raise ValueError(f"unsupported codec version {version}")
    return MsgType(msg_type), bytes(data)


def decode_ccip_envelope(raw: bytes) -> tuple[CcipMsgType, bytes]:
    """``CcipMessages.decode``: unwrap
    ``abi.encode(uint8 version, uint8 messageType, bytes data)``."""
    version, msg_type, data = decode(["uint8", "uint8", "bytes"], raw)
    if version != CCIP_VERSION:
        raise ValueError(f"unsupported CCIP codec version {version}")
    return CcipMsgType(msg_type), bytes(data)


def decode_command(data: bytes) -> Command:
    """``CrosschainMessages.decodeCommand``: the ``Command`` behind a
    ``COMMAND`` envelope's inner data."""
    (values,) = decode([COMMAND_TUPLE_TYPE], data)
    return Command.from_tuple(values)


class CrosschainTransportKind(IntEnum):
    """``CrosschainTransportTypes.CrosschainTransportKind``. Only the CCIP
    executor reports it on-chain (``transportKind()``); a Stargate executor is
    recognized by its ``STARGATE_POOL()`` immutable instead."""

    UNDEFINED = 0
    STARGATE_LAYERZERO = 1
    CHAINLINK_CCIP = 2
