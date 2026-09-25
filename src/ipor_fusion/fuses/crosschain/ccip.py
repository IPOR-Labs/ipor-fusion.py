"""Chainlink CCIP crosschain fuses (``contracts/fuses/crosschain/ccip/**``).

ABIs::

    CcipCrosschainSupplyFuse.enter((address executor, address asset,
        uint256 dstChainId, uint256 amount, uint256 maxFee, address feeToken,
        uint256 gasLimit))
    CcipCrosschainSupplyFuse.exit((address executor, uint256 dstChainId,
        uint256 amount, uint256 minReturn, uint256 maxFee, address feeToken,
        uint256 gasLimit))
    CcipCrosschainCommandFuse.enter((uint8 commandType, address executor,
        uint256 chainId, CcipRouteConfig route, address[] plasmaVaults,
        bool vaultsAllowed, bool vaultsReplace, Command command,
        uint256 maxFee, address feeToken, uint256 gasLimit))
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum

from eth_typing import ChecksumAddress

from ipor_fusion.crosschain.ccip.contracts import CcipRouteConfig
from ipor_fusion.crosschain.messages import COMMAND_TUPLE_TYPE, EMPTY_COMMAND, Command
from ipor_fusion.fuses.base import ZERO_ADDRESS, Fuse, FuseAction
from ipor_fusion.fuses.crosschain.base import (
    CrosschainCommandFuse,
    CrosschainSupplyFuse,
    SendParams,
)
from ipor_fusion.types import Amount, ChainId


@dataclass(frozen=True, slots=True)
class CcipSendParams(SendParams):
    """Transport parameters of one CCIP send: ``maxFee`` caps the router quote
    and must not exceed the route ceiling, ``feeToken`` must equal the route's
    (zero address for native), ``gasLimit`` is the destination ``ccipReceive``
    gas. Every CCIP fuse operation that sends a message takes them."""

    max_fee: int
    gas_limit: int
    fee_token: ChecksumAddress = ZERO_ADDRESS  # type: ignore[assignment]

    @classmethod
    def from_route(cls, route: CcipRouteConfig, *, token: bool) -> CcipSendParams:
        """Params that stay within the executor's registered route policy:
        the route's fee ceiling and fee token, and its token or message gas
        limit depending on whether the send carries tokens."""
        return cls(
            max_fee=route.max_fee,
            gas_limit=route.token_gas_limit if token else route.message_gas_limit,
            fee_token=route.fee_token,
        )


def _ccip_send(send: object, fuse: Fuse) -> CcipSendParams:
    if not isinstance(send, CcipSendParams):
        raise TypeError(
            f"{type(fuse).__name__} takes CcipSendParams, got {type(send).__name__}"
        )
    return send


class CcipCrosschainSupplyFuse(CrosschainSupplyFuse):
    """``CcipCrosschainSupplyFuse``: the Chainlink CCIP supply fuse. ``send`` is
    a ``CcipSendParams`` (``CcipSendParams.from_route(route, token=True)`` for
    ``enter``, ``token=False`` for ``exit``)."""

    _ENTER = "enter((address,address,uint256,uint256,uint256,address,uint256))"
    _EXIT = "exit((address,uint256,uint256,uint256,uint256,address,uint256))"

    def enter(
        self,
        *,
        executor: ChecksumAddress,
        asset: ChecksumAddress,
        dst_chain_id: ChainId,
        amount: Amount,
        send: SendParams,
    ) -> FuseAction:
        params = _ccip_send(send, self)
        self._validate_enter(executor, asset, dst_chain_id, amount)
        _validate_ccip_send(self, params)
        return self._action_raw(
            self._ENTER,
            [
                [
                    executor,
                    asset,
                    dst_chain_id,
                    amount,
                    params.max_fee,
                    params.fee_token,
                    params.gas_limit,
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
        params = _ccip_send(send, self)
        self._validate_exit(executor, dst_chain_id, amount, min_return)
        _validate_ccip_send(self, params)
        return self._action_raw(
            self._EXIT,
            [
                [
                    executor,
                    dst_chain_id,
                    amount,
                    min_return,
                    params.max_fee,
                    params.fee_token,
                    params.gas_limit,
                ]
            ],
        )


def _validate_ccip_send(fuse: Fuse, params: CcipSendParams) -> None:
    fuse._validate_non_negative(params.max_fee, "max_fee")
    fuse._validate_non_negative(params.gas_limit, "gas_limit")


class CcipCommandType(IntEnum):
    """``CcipCrosschainCommandFuse.CcipCommandType``."""

    UNDEFINED = 0
    REGISTER_ROUTE = 1
    CREATE_DISPATCHER = 2
    UPDATE_VAULTS = 3
    SEND_COMMAND = 4
    REFRESH_BALANCE = 5
    CANCEL_RETURN = 6
    RETRY_COMMAND = 7
    CANCEL_COMMAND = 8


_EMPTY_ROUTE = CcipRouteConfig(
    chain_selector=0,
    peer=ZERO_ADDRESS,  # type: ignore[arg-type]
    fee_token=ZERO_ADDRESS,  # type: ignore[arg-type]
    message_gas_limit=0,
    token_gas_limit=0,
    max_fee=0,
    enabled=False,
)


class CcipCrosschainCommandFuse(CrosschainCommandFuse):
    """``CcipCrosschainCommandFuse``: every manager-only CCIP executor and
    factory operation, selected by ``command_type`` in ``enter``. Every
    operation is chain-scoped; the ones that send a CCIP message also take
    ``max_fee``, ``fee_token`` and ``gas_limit`` like the supply fuse, and the
    transport-agnostic methods take them as a ``CcipSendParams``."""

    _ENTER = (
        "enter((uint8,address,uint256,(uint64,address,address,uint96,uint96,uint256,bool),"
        f"address[],bool,bool,{COMMAND_TUPLE_TYPE},uint256,address,uint256))"
    )
    _WITH_COMMAND = frozenset(
        {
            CcipCommandType.SEND_COMMAND,
            CcipCommandType.RETRY_COMMAND,
            CcipCommandType.CANCEL_COMMAND,
        }
    )

    def send_command(
        self,
        *,
        executor: ChecksumAddress,
        chain_id: ChainId,
        command: Command,
        send: SendParams | None = None,
    ) -> FuseAction:
        params = _ccip_send(send, self)
        return self.enter(
            CcipCommandType.SEND_COMMAND,
            executor=executor,
            chain_id=chain_id,
            command=command,
            max_fee=params.max_fee,
            fee_token=params.fee_token,
            gas_limit=params.gas_limit,
        )

    def create_dispatcher(
        self,
        *,
        executor: ChecksumAddress,
        chain_id: ChainId,
        plasma_vaults: Sequence[ChecksumAddress],
        send: SendParams | None = None,
    ) -> FuseAction:
        """The factory pays the ticket from its own budget, capped by
        ``send.max_fee``; the route must have been registered first
        (``REGISTER_ROUTE``)."""
        params = _ccip_send(send, self)
        return self.enter(
            CcipCommandType.CREATE_DISPATCHER,
            executor=executor,
            chain_id=chain_id,
            plasma_vaults=plasma_vaults,
            max_fee=params.max_fee,
        )

    def update_vaults(
        self,
        *,
        executor: ChecksumAddress,
        chain_id: ChainId,
        plasma_vaults: Sequence[ChecksumAddress],
        send: SendParams | None = None,
    ) -> FuseAction:
        """Replace semantics (``vaultsAllowed`` and ``vaultsReplace`` set), the
        same remote state the Stargate ``UPDATE_PLASMA_VAULTS`` produces."""
        params = _ccip_send(send, self)
        return self.enter(
            CcipCommandType.UPDATE_VAULTS,
            executor=executor,
            chain_id=chain_id,
            plasma_vaults=plasma_vaults,
            vaults_allowed=True,
            vaults_replace=True,
            max_fee=params.max_fee,
            fee_token=params.fee_token,
            gas_limit=params.gas_limit,
        )

    def enter(
        self,
        command_type: CcipCommandType,
        *,
        executor: ChecksumAddress,
        chain_id: ChainId,
        route: CcipRouteConfig | None = None,
        plasma_vaults: Sequence[ChecksumAddress] = (),
        vaults_allowed: bool = False,
        vaults_replace: bool = False,
        command: Command = EMPTY_COMMAND,
        max_fee: int = 0,
        gas_limit: int = 0,
        fee_token: ChecksumAddress = ZERO_ADDRESS,  # type: ignore[assignment]
    ) -> FuseAction:
        """Forward one operation to the executor (or its factory):

        - REGISTER_ROUTE: ``route`` (must match the factory's timelocked route).
        - CREATE_DISPATCHER: ``plasma_vaults`` (granted REMOTE_VAULTs) and
          ``max_fee`` for the factory's ticket.
        - UPDATE_VAULTS: ``plasma_vaults``, ``vaults_allowed``, ``vaults_replace``.
        - SEND_COMMAND: ``command`` with a business action.
        - REFRESH_BALANCE: no extra input.
        - CANCEL_RETURN: no extra input, no message sent.
        - RETRY_COMMAND / CANCEL_COMMAND: ``command`` carrying the ``command_id``.
        """
        command_type = CcipCommandType(command_type)
        self._validate_address(executor, "executor")
        if command_type == CcipCommandType.UNDEFINED:
            raise ValueError("command_type must not be UNDEFINED")
        if chain_id == 0:
            raise ValueError(f"{command_type.name} requires chain_id")
        if command_type == CcipCommandType.REGISTER_ROUTE and route is None:
            raise ValueError("REGISTER_ROUTE requires route")
        if command_type in self._WITH_COMMAND and command == EMPTY_COMMAND:
            raise ValueError(f"{command_type.name} requires command")
        for index, vault in enumerate(plasma_vaults):
            self._validate_address(vault, f"plasma_vaults[{index}]")
        return self._action_raw(
            self._ENTER,
            [
                [
                    int(command_type),
                    executor,
                    chain_id,
                    (route or _EMPTY_ROUTE).as_tuple(),
                    list(plasma_vaults),
                    vaults_allowed,
                    vaults_replace,
                    command.as_tuple(),
                    max_fee,
                    fee_token,
                    gas_limit,
                ]
            ],
        )
