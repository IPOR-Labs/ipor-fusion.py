"""Decode `execute` / `claimRewards` calldata into `FuseAction`s, and a
`FuseAction` into the function and arguments its fuse class encoded."""

from __future__ import annotations

from dataclasses import dataclass

from eth_abi import decode
from eth_abi.exceptions import DecodingError
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion.core.contract import _parse_param_types
from ipor_fusion.core.fuse import Fuse
from ipor_fusion.core.fuse_action import FuseAction
from ipor_fusion.types import FuseAddress

_BATCH_SIGNATURES = (
    "execute((address,bytes)[])",
    "claimRewards((address,bytes)[])",
)
_BATCH_SELECTORS = {
    function_signature_to_4byte_selector(sig): sig for sig in _BATCH_SIGNATURES
}


@dataclass(frozen=True)
class DecodedFuseAction:
    """A `FuseAction` read as one call of ``fuse_class``: the Solidity
    ``signature`` it encodes and the ABI-decoded ``args`` (tuples for structs)."""

    fuse: FuseAddress
    fuse_class: type[Fuse]
    signature: str
    args: tuple[object, ...]

    @property
    def function(self) -> str:
        return self.signature[: self.signature.index("(")]

    @property
    def label(self) -> str:
        """``AaveV3SupplyFuse.enter`` style: the class and the function."""
        return f"{self.fuse_class.__name__}.{self.function}"


def decode_execute_calldata(data: bytes) -> list[FuseAction]:
    """The actions of a `PlasmaVault.execute` or `RewardsManager.claim_rewards`
    call's calldata; raises ``ValueError`` for any other function or for data
    that does not decode."""
    signature = _BATCH_SELECTORS.get(bytes(data[:4]))
    if signature is None:
        raise ValueError(
            f"not execute or claimRewards calldata (selector 0x{bytes(data[:4]).hex()})"
        )
    try:
        (actions,) = decode(["(address,bytes)[]"], bytes(data[4:]))
    except DecodingError as exc:
        raise ValueError(f"{signature} calldata does not decode: {exc}") from exc
    return [
        FuseAction(fuse=FuseAddress(Web3.to_checksum_address(fuse)), data=bytes(call))
        for fuse, call in actions
    ]


def _loaded_fuse_classes() -> list[type[Fuse]]:
    found: dict[type[Fuse], None] = {}
    pending = list(Fuse.__subclasses__())
    while pending:
        cls = pending.pop()
        if cls not in found:
            found[cls] = None
            pending += cls.__subclasses__()
    return sorted(found, key=lambda c: (c.__module__, c.__qualname__))


def _signature_for(selector: bytes, fuse_class: type[Fuse]) -> str | None:
    for signature in fuse_class.signatures():
        if function_signature_to_4byte_selector(signature) == selector:
            return signature
    return None


def fuse_classes_for(action: FuseAction) -> list[type[Fuse]]:
    """Every loaded fuse class whose encoders produce ``action``'s selector.
    Several fuses share a shape (``enter((address,uint256))``), so this narrows
    the candidates; the vault's fuse list or the registry name settles them."""
    selector = bytes(action.data[:4])
    return [cls for cls in _loaded_fuse_classes() if _signature_for(selector, cls)]


def decode_fuse_action(action: FuseAction, fuse_class: type[Fuse]) -> DecodedFuseAction:
    """``action`` as a call of ``fuse_class``; raises ``ValueError`` when the
    class encodes no function with its selector or the arguments do not decode."""
    selector = bytes(action.data[:4])
    signature = _signature_for(selector, fuse_class)
    if signature is None:
        raise ValueError(
            f"{fuse_class.__name__} encodes no function with selector 0x{selector.hex()}"
        )
    try:
        args = decode(_parse_param_types(signature), bytes(action.data[4:]))
    except DecodingError as exc:
        raise ValueError(f"{signature} arguments do not decode: {exc}") from exc
    return DecodedFuseAction(
        fuse=action.fuse, fuse_class=fuse_class, signature=signature, args=tuple(args)
    )
