from enum import Enum, auto

from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import (
    Fuse,
    FuseAction,
    _encode_address_substrate,
    _encode_uint248_substrate,
)
from ipor_fusion.types import Amount, FuseAddress


class UniversalTokenSwapperAbi(Enum):
    """ABI revision of the deployed UniversalTokenSwapperFuse contract.

    Deployed fuses are immutable, so the revisions coexist across chains:
    older deployments (e.g. Base) accept only the LEGACY signature, the
    HyperEVM ones (markets 12 and 1202) only the MIN_AMOUNT_OUT one, and the
    Arbitrum fuses of the 2026-10-21 contracts release (markets 12 and 1202)
    only TOKENS_TO_RETURN, which adds the tokens the swap calls may leave in
    the executor. Pick the member matching the fuse address this instance is
    bound to.
    """

    LEGACY = auto()
    MIN_AMOUNT_OUT = auto()
    TOKENS_TO_RETURN = auto()


class UniversalTokenSwapperSubstrates:
    """Typed bytes32 substrate encoders for universal-token-swapper markets
    (12_02+). Mirrors `UniversalTokenSwapperSubstrateLib.sol`: the substrate
    is `bytes32(uint256(type) << 248 | payload)` — one tag byte, then a
    31-byte payload (a uint160 address for Token/Target, a WAD fraction for
    Slippage, where 1e18 = 100%).
    """

    _TOKEN = 1
    _TARGET = 2
    _SLIPPAGE = 3

    @classmethod
    def token(cls, address: ChecksumAddress) -> bytes:
        """Asset allowed as swap input/output."""
        return _encode_address_substrate(cls._TOKEN, address, "token")

    @classmethod
    def target(cls, address: ChecksumAddress) -> bytes:
        """Contract the swap executor may call (router, or a token that
        receives an `approve` call as part of the swap batch)."""
        return _encode_address_substrate(cls._TARGET, address, "target")

    @classmethod
    def slippage(cls, wad: int) -> bytes:
        """Vault-side slippage cap as a WAD fraction (1e16 = 1%)."""
        return _encode_uint248_substrate(cls._SLIPPAGE, wad, "slippage WAD")


class UniversalTokenSwapperFuse(Fuse):
    """Fuse for executing arbitrary token swaps through whitelisted DEX targets."""

    def __init__(
        self,
        address: FuseAddress,
        abi: UniversalTokenSwapperAbi = UniversalTokenSwapperAbi.LEGACY,
    ):
        super().__init__(address)
        self._abi = abi

    def swap(
        self,
        *,
        token_in: ChecksumAddress,
        token_out: ChecksumAddress,
        amount_in: Amount,
        targets: list[ChecksumAddress],
        data: list[bytes],
        min_amount_out: Amount | None = None,
        tokens_to_return: list[ChecksumAddress] | None = None,
    ) -> FuseAction:
        """``tokens_to_return`` (TOKENS_TO_RETURN only): intermediate or extra
        output tokens the swap calls may leave in the executor, each a granted
        Token substrate; the executor returns them to the vault."""
        self._validate_address(token_in, "token_in")
        self._validate_address(token_out, "token_out")
        self._validate_amount(amount_in, "amount_in")
        if len(targets) != len(data):
            raise ValueError(
                f"targets and data must have the same length, got {len(targets)} and {len(data)}"
            )
        if (
            tokens_to_return is not None
            and self._abi is not UniversalTokenSwapperAbi.TOKENS_TO_RETURN
        ):
            raise ValueError(
                "tokens_to_return requires abi=UniversalTokenSwapperAbi.TOKENS_TO_RETURN"
            )
        if self._abi is UniversalTokenSwapperAbi.LEGACY:
            if min_amount_out is not None:
                raise ValueError(
                    "min_amount_out requires abi=UniversalTokenSwapperAbi.MIN_AMOUNT_OUT"
                )
            return self._action_raw(
                "enter((address,address,uint256,(address[],bytes[])))",
                [[token_in, token_out, amount_in, [targets, data]]],
            )
        if min_amount_out is None:
            raise ValueError(
                f"min_amount_out is required with abi=UniversalTokenSwapperAbi.{self._abi.name}"
                " (pass 0 to rely on the vault-side slippage cap only)"
            )
        self._validate_non_negative(min_amount_out, "min_amount_out")
        if self._abi is UniversalTokenSwapperAbi.MIN_AMOUNT_OUT:
            return self._action_raw(
                "enter((address,address,uint256,uint256,(address[],bytes[])))",
                [[token_in, token_out, amount_in, min_amount_out, [targets, data]]],
            )
        returned = list(tokens_to_return or [])
        for index, token in enumerate(returned):
            self._validate_address(token, f"tokens_to_return[{index}]")
        return self._action_raw(
            "enter((address,address,uint256,uint256,(address[],bytes[],address[])))",
            [
                [
                    token_in,
                    token_out,
                    amount_in,
                    min_amount_out,
                    [targets, data, returned],
                ]
            ],
        )
