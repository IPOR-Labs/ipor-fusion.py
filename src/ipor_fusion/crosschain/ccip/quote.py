"""The native fee of a CCIP send, quoted by the contract that would pay it.

The CCIP factory, executor and dispatcher ask the Router for the fee inside
``CcipSendLib`` and revert ``CcipInsufficientNativeFeeBalance(balance,
required)`` when their balance falls short, so a simulation with the payer's
balance at zero yields the exact quote for the exact message the transport
would send, extra args and token amounts included. Nothing else in the
contracts exposes that figure.
"""

from __future__ import annotations

from eth_abi import decode
from eth_typing import ChecksumAddress
from eth_utils import keccak
from web3 import Web3

from ipor_fusion.core.contract import Call
from ipor_fusion.core.simulation import VaultSimulator

#: Selector of ``CcipInsufficientNativeFeeBalance(uint256 balance, uint256 required)``.
INSUFFICIENT_NATIVE_FEE_SELECTOR: bytes = keccak(
    text="CcipInsufficientNativeFeeBalance(uint256,uint256)"
)[:4]
_NO_VAULT = Web3.to_checksum_address("0x" + "00" * 20)


def quote_ccip_native_fee(
    web3: Web3,
    call: Call,
    *,
    payer: ChecksumAddress,
    from_: ChecksumAddress,
    block: int | str = "latest",
    gas: int | None = None,
) -> int:
    """The native fee ``call`` would pay for its CCIP message, in wei of the
    sending chain.

    ``payer`` is the contract that pays: the factory for ``requestDispatcher``,
    the executor for a supply or command (``from_`` is then the alpha sending
    the vault's ``execute``), the dispatcher for a return. The call must be
    valid at ``block`` up to the fee check; a revert before it, or a call that
    sends no message, raises ``ValueError``.
    """
    simulator = VaultSimulator(web3, vault=_NO_VAULT, alpha=from_, block=block)
    simulator.with_state_override(payer, balance=hex(0))
    simulator.add_call(call, from_=from_, label="quote", gas=gas)
    (result,) = simulator.run().calls
    data = bytes(result.return_data)
    if not result.success and data[:4] == INSUFFICIENT_NATIVE_FEE_SELECTOR:
        _balance, required = decode(["uint256", "uint256"], data[4:])
        return int(required)
    if result.success:
        raise ValueError(
            "the call succeeded with the payer at zero balance: it sends no CCIP message"
        )
    raise ValueError(
        f"the call reverted before the CCIP fee check: {result.revert_reason}"
    )
