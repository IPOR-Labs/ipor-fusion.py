"""Stargate V2 / LayerZero V2 crosschain fuses
(``contracts/fuses/crosschain/stargate/**``).

ABIs::

    StargateCrosschainSupplyFuse.enter((address executor, address asset,
        uint256 dstChainId, uint256 amount, uint256 minAmountLD, bytes options))
    StargateCrosschainSupplyFuse.exit((address executor, uint256 dstChainId,
        uint256 amount, uint256 minReturn, uint128 nativeDrop, bytes options))
    StargateCrosschainCommandFuse.enter((uint8 commandType, address executor,
        uint256 chainId, uint32 eid, bytes enforcedOptions,
        address[] plasmaVaults, uint256 requestId, Command command))
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum

from eth_typing import ChecksumAddress

from ipor_fusion.crosschain.messages import (
    COMMAND_TUPLE_TYPE,
    EMPTY_COMMAND,
    BusinessAction,
    Command,
)
from ipor_fusion.crosschain.stargate.layerzero import OptionsBuilder
from ipor_fusion.fuses.base import Fuse, FuseAction
from ipor_fusion.fuses.crosschain.base import (
    CrosschainCommandFuse,
    CrosschainSupplyFuse,
    SendParams,
)
from ipor_fusion.types import Amount, ChainId

_NO_CHAIN = ChainId(0)
_ZERO_AMOUNT = Amount(0)


@dataclass(frozen=True, slots=True)
class StargateSendParams(SendParams):
    """Transport parameters of one Stargate/LayerZero send: the tail of
    ``StargateCrosschainSupplyFuseEnterData`` / ``ExitData``. ``min_amount_ld``
    (the Stargate slippage floor) is read on ``enter``, ``native_drop`` on
    ``exit``. The command lane takes no params at all: it uses the executor's
    enforced options."""

    options: bytes | OptionsBuilder
    min_amount_ld: Amount = _ZERO_AMOUNT
    native_drop: int = 0


def _stargate_send(send: object, fuse: Fuse) -> StargateSendParams:
    if not isinstance(send, StargateSendParams):
        raise TypeError(
            f"{type(fuse).__name__} takes StargateSendParams, got {type(send).__name__}"
        )
    return send


def _no_send(send: object, fuse: Fuse) -> None:
    if send is not None:
        raise TypeError(
            f"{type(fuse).__name__}: the Stargate command lane takes no send params "
            "(it uses the executor's enforced options), got "
            f"{type(send).__name__}"
        )


class StargateCrosschainSupplyFuse(CrosschainSupplyFuse):
    """``StargateCrosschainSupplyFuse``: the Stargate V2 / LayerZero V2 supply
    fuse. ``send`` is a ``StargateSendParams``; typical ``enter`` options are
    ``OptionsBuilder.new_options().add_executor_lz_receive_option(250_000)
    .add_executor_lz_compose_option(0, 1_500_000)``."""

    _ENTER = "enter((address,address,uint256,uint256,uint256,bytes))"
    _EXIT = "exit((address,uint256,uint256,uint256,uint128,bytes))"

    def enter(
        self,
        *,
        executor: ChecksumAddress,
        asset: ChecksumAddress,
        dst_chain_id: ChainId,
        amount: Amount,
        send: SendParams,
    ) -> FuseAction:
        params = _stargate_send(send, self)
        self._validate_enter(executor, asset, dst_chain_id, amount)
        self._validate_non_negative(params.min_amount_ld, "min_amount_ld")
        return self._action_raw(
            self._ENTER,
            [
                [
                    executor,
                    asset,
                    dst_chain_id,
                    amount,
                    params.min_amount_ld,
                    bytes(params.options),
                ]
            ],
        )

    def exit(
        self,
        *,
        executor: ChecksumAddress,
        dst_chain_id: ChainId,
        amount: Amount,
        min_return: Amount,
        send: SendParams,
    ) -> FuseAction:
        """``native_drop`` wei of destination gas is delivered to the dispatcher
        before the recall runs, within the factory's per-destination fee cap."""
        params = _stargate_send(send, self)
        self._validate_exit(executor, dst_chain_id, amount, min_return)
        self._validate_non_negative(params.native_drop, "native_drop")
        return self._action_raw(
            self._EXIT,
            [
                [
                    executor,
                    dst_chain_id,
                    amount,
                    min_return,
                    params.native_drop,
                    bytes(params.options),
                ]
            ],
        )


class StargateCrosschainCommandType(IntEnum):
    """``StargateCrosschainCommandFuse.StargateCrosschainCommandType``."""

    UNDEFINED = 0
    REGISTER_CHAIN = 1
    CREATE_DISPATCHER = 2
    UPDATE_PLASMA_VAULTS = 3
    SEND_COMMAND = 4
    CANCEL_DISPATCHER_REQUEST = 5
    RETRY_COMMAND = 6
    CANCEL_COMMAND = 7


class StargateCrosschainCommandFuse(CrosschainCommandFuse):
    """``StargateCrosschainCommandFuse``: every manager-only executor operation,
    selected by ``command_type`` (a tagged union, as on-chain)."""

    _ENTER = (
        "enter((uint8,address,uint256,uint32,bytes,address[],uint256,"
        f"{COMMAND_TUPLE_TYPE}))"
    )
    _CHAIN_SCOPED = frozenset(
        {
            StargateCrosschainCommandType.REGISTER_CHAIN,
            StargateCrosschainCommandType.CREATE_DISPATCHER,
            StargateCrosschainCommandType.UPDATE_PLASMA_VAULTS,
            StargateCrosschainCommandType.SEND_COMMAND,
            StargateCrosschainCommandType.RETRY_COMMAND,
            StargateCrosschainCommandType.CANCEL_COMMAND,
        }
    )

    def enter(
        self,
        command_type: StargateCrosschainCommandType,
        *,
        executor: ChecksumAddress,
        chain_id: ChainId = _NO_CHAIN,
        eid: int = 0,
        enforced_options: bytes = b"",
        plasma_vaults: Sequence[ChecksumAddress] = (),
        request_id: int = 0,
        command: Command = EMPTY_COMMAND,
    ) -> FuseAction:
        """Forward one operation to the executor. Only the fields the operation
        reads matter:

        - REGISTER_CHAIN: ``chain_id``, ``eid`` and ``enforced_options`` (see
          ``encode_enforced_options``; the command lane needs ``lzReceive`` gas).
        - CREATE_DISPATCHER / UPDATE_PLASMA_VAULTS: ``chain_id`` and
          ``plasma_vaults``, each granted as a REMOTE_VAULT for that chain.
        - SEND_COMMAND: ``chain_id`` and ``command``; its target vault must be a
          granted REMOTE_VAULT for that chain.
        - CANCEL_DISPATCHER_REQUEST: ``request_id``.
        - RETRY_COMMAND / CANCEL_COMMAND: ``chain_id``.
        """
        command_type = StargateCrosschainCommandType(command_type)
        self._validate_address(executor, "executor")
        self._validate_command(command_type, chain_id, eid, request_id, command)
        for index, vault in enumerate(plasma_vaults):
            self._validate_address(vault, f"plasma_vaults[{index}]")
        return self._action_raw(
            self._ENTER,
            [
                [
                    int(command_type),
                    executor,
                    chain_id,
                    eid,
                    enforced_options,
                    list(plasma_vaults),
                    request_id,
                    command.as_tuple(),
                ]
            ],
        )

    def send_command(
        self,
        *,
        executor: ChecksumAddress,
        chain_id: ChainId,
        command: Command,
        send: SendParams | None = None,
    ) -> FuseAction:
        _no_send(send, self)
        return self.enter(
            StargateCrosschainCommandType.SEND_COMMAND,
            executor=executor,
            chain_id=chain_id,
            command=command,
        )

    def create_dispatcher(
        self,
        *,
        executor: ChecksumAddress,
        chain_id: ChainId,
        plasma_vaults: Sequence[ChecksumAddress],
        send: SendParams | None = None,
    ) -> FuseAction:
        """The chain must have been registered first (``REGISTER_CHAIN``)."""
        _no_send(send, self)
        return self.enter(
            StargateCrosschainCommandType.CREATE_DISPATCHER,
            executor=executor,
            chain_id=chain_id,
            plasma_vaults=plasma_vaults,
        )

    def update_vaults(
        self,
        *,
        executor: ChecksumAddress,
        chain_id: ChainId,
        plasma_vaults: Sequence[ChecksumAddress],
        send: SendParams | None = None,
    ) -> FuseAction:
        _no_send(send, self)
        return self.enter(
            StargateCrosschainCommandType.UPDATE_PLASMA_VAULTS,
            executor=executor,
            chain_id=chain_id,
            plasma_vaults=plasma_vaults,
        )

    def _validate_command(
        self,
        command_type: StargateCrosschainCommandType,
        chain_id: int,
        eid: int,
        request_id: int,
        command: Command,
    ) -> None:
        if command_type == StargateCrosschainCommandType.UNDEFINED:
            raise ValueError("command_type must not be UNDEFINED")
        if command_type in self._CHAIN_SCOPED and chain_id == 0:
            raise ValueError(f"{command_type.name} requires chain_id")
        if command_type == StargateCrosschainCommandType.REGISTER_CHAIN and eid == 0:
            raise ValueError("REGISTER_CHAIN requires eid")
        if (
            command_type == StargateCrosschainCommandType.CANCEL_DISPATCHER_REQUEST
            and request_id == 0
        ):
            raise ValueError("CANCEL_DISPATCHER_REQUEST requires request_id")
        if (
            command_type == StargateCrosschainCommandType.SEND_COMMAND
            and command.action == BusinessAction.NONE
        ):
            raise ValueError("SEND_COMMAND requires a command with a business action")
