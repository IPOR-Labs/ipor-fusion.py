"""Async-action fuse (market 40).

Moves off-vault capital to an arbitrary account via the per-vault AsyncExecutor
(auto-created on the first enter). ``enter`` transfers ``amount_out`` of
``token_out`` from the PlasmaVault to the executor and has it run each
``call_datas[i]`` against ``targets[i]`` from the executor context (e.g. an
ERC20 transfer forwarding the funds to a trading account). ``exit`` values the
listed assets at the oracle, checks slippage, and sweeps them back to the vault.

The executor holds at most one funded position at a time: a second ``enter``
while it is still funded reverts, so funding several destinations must happen
within a single ``enter`` -- see ``transfer_out_many``.

ABI (AsyncActionFuse.sol)::

    enter((address tokenOut, uint256 amountOut, address[] targets,
           bytes[] callDatas, uint256[] ethAmounts, address[] tokensDustToCheck))
    exit((address[] assets, bytes[] fetchCallDatas))
"""

from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.core.contract import _encode_calldata
from ipor_fusion.fuses.base import (
    Fuse,
    FuseAction,
    _encode_uint248_substrate,
    _substrate_address_bytes,
)
from ipor_fusion.types import Amount


class AsyncActionFuse(Fuse):
    """Fuse for moving off-vault capital via the per-vault AsyncExecutor (market 40)."""

    _ENTER_TUPLE = "(address,uint256,address[],bytes[],uint256[],address[])"
    _EXIT_TUPLE = "(address[],bytes[])"
    _BALANCE_OF_SELECTOR = function_signature_to_4byte_selector("balanceOf(address)")

    @staticmethod
    def _erc20_transfer(destination: ChecksumAddress, amount: Amount) -> bytes:
        return _encode_calldata("transfer(address,uint256)", destination, amount)

    def enter(
        self,
        *,
        token_out: ChecksumAddress,
        amount_out: Amount,
        targets: list[ChecksumAddress],
        call_datas: list[bytes],
        eth_amounts: list[Amount] | None = None,
        tokens_dust_to_check: list[ChecksumAddress] | None = None,
    ) -> FuseAction:
        """Fund the executor with ``amount_out`` of ``token_out`` and run each
        ``call_datas[i]`` against ``targets[i]`` from the executor context.

        ``eth_amounts`` defaults to zeros (one per target) and must match
        ``targets`` in length; ``amount_out`` may be zero for an actions-only
        enter. ``tokens_dust_to_check`` is currently inert on-chain (reserved for
        future use), so do not rely on it for dust protection.
        """
        self._validate_address(token_out, "token_out")
        self._validate_non_negative(amount_out, "amount_out")
        if len(targets) != len(call_datas):
            raise ValueError(
                "targets and call_datas must have the same length, "
                f"got {len(targets)} and {len(call_datas)}"
            )
        for index, target in enumerate(targets):
            self._validate_address(target, f"targets[{index}]")
        eth = eth_amounts if eth_amounts is not None else [0] * len(targets)
        if len(eth) != len(targets):
            raise ValueError(
                "eth_amounts and targets must have the same length, "
                f"got {len(eth)} and {len(targets)}"
            )
        for index, eth_amount in enumerate(eth):
            self._validate_non_negative(eth_amount, f"eth_amounts[{index}]")
        dust = tokens_dust_to_check or []
        return self._action_raw(
            f"enter({self._ENTER_TUPLE})",
            [
                [
                    token_out,
                    amount_out,
                    list(targets),
                    list(call_datas),
                    list(eth),
                    list(dust),
                ]
            ],
        )

    def transfer_out(
        self,
        *,
        token_out: ChecksumAddress,
        amount_out: Amount,
        destination: ChecksumAddress,
    ) -> FuseAction:
        """The one-transfer enter: fund the executor with ``amount_out`` of
        ``token_out`` and forward all of it to ``destination``."""
        self._validate_address(destination, "destination")
        self._validate_amount(amount_out, "amount_out")
        return self.enter(
            token_out=token_out,
            amount_out=amount_out,
            targets=[token_out],
            call_datas=[self._erc20_transfer(destination, amount_out)],
        )

    def transfer_out_many(
        self,
        *,
        token_out: ChecksumAddress,
        transfers: list[tuple[ChecksumAddress, Amount]],
    ) -> FuseAction:
        """The split enter: fund the executor with the SUM of ``transfers``
        ([(destination, amount), ...]) of ``token_out`` and forward each share to
        its destination in one enter -- the executor rejects a second enter while
        funded, so multiple destinations must be funded together."""
        self._validate_non_empty_list(transfers, "transfers")
        targets: list[ChecksumAddress] = []
        call_datas: list[bytes] = []
        total = 0
        for index, (destination, amount) in enumerate(transfers):
            self._validate_address(destination, f"transfers[{index}].destination")
            self._validate_amount(amount, f"transfers[{index}].amount")
            targets.append(token_out)
            call_datas.append(self._erc20_transfer(destination, amount))
            total += amount
        return self.enter(
            token_out=token_out,
            amount_out=Amount(total),
            targets=targets,
            call_datas=call_datas,
        )

    def exit(
        self,
        *,
        assets: list[ChecksumAddress],
        fetch_call_datas: list[bytes] | None = None,
    ) -> FuseAction:
        """Sweep the executor's ``assets`` back to the vault. Each
        ``fetch_call_datas[i]`` carries a 4-byte selector checked against the
        ALLOWED_TARGETS substrate for ``assets[i]`` (never executed);
        ``balanceOf(address)`` is the conventional carrier and the default."""
        fetch = (
            fetch_call_datas
            if fetch_call_datas is not None
            else [self._BALANCE_OF_SELECTOR for _ in assets]
        )
        if len(fetch) != len(assets):
            raise ValueError(
                "fetch_call_datas and assets must have the same length, "
                f"got {len(fetch)} and {len(assets)}"
            )
        return self._action_raw(
            f"exit({self._EXIT_TUPLE})",
            [[list(assets), list(fetch)]],
        )


class AsyncActionSubstrates:
    """Typed bytes32 substrate encoders for the async-action market (40).

    Mirrors `AsyncActionFuseLib.sol`: each substrate is
    ``bytes32(uint256(type) << 248 | payload)`` -- a one-byte type tag in the
    high byte, then a 31-byte payload whose layout depends on the type.
    """

    _ALLOWED_AMOUNT_TO_OUTSIDE = 0
    _ALLOWED_TARGETS = 1
    _ALLOWED_EXIT_SLIPPAGE = 2

    @classmethod
    def allowed_amount_to_outside(cls, asset: ChecksumAddress, amount: Amount) -> bytes:
        """Cap how much of ``asset`` may leave the vault to the executor.

        ``amount`` is in the asset's own decimals and must fit uint88. Layout:
        type byte, then the 20-byte asset, then the amount as a uint88.
        """
        if not 0 <= amount < (1 << 88):
            raise ValueError(f"amount out of uint88 range: {amount}")
        return (
            bytes([cls._ALLOWED_AMOUNT_TO_OUTSIDE])
            + _substrate_address_bytes(asset)
            + amount.to_bytes(11, "big")
        )

    @classmethod
    def target(cls, target: ChecksumAddress, selector: bytes) -> bytes:
        """Allow the executor to call ``target`` with ``selector`` (a 4-byte
        function selector). On exit the same ``(asset, selector)`` pair is the
        carrier checked for a fetched asset -- see ``AsyncActionFuse.exit``.
        Layout: type byte, 7 zero bytes, the 20-byte target, the 4-byte selector.
        """
        if len(selector) != 4:
            raise ValueError(f"selector must be 4 bytes, got {len(selector)}")
        return (
            bytes([cls._ALLOWED_TARGETS])
            + b"\x00" * 7
            + _substrate_address_bytes(target)
            + selector
        )

    @classmethod
    def exit_slippage(cls, slippage_wad: int) -> bytes:
        """Maximum value drop tolerated on exit, as a WAD fraction (1e18 = 100%).
        On-chain a value above 1e18 is rejected when exit runs."""
        return _encode_uint248_substrate(
            cls._ALLOWED_EXIT_SLIPPAGE, slippage_wad, "slippage WAD"
        )
