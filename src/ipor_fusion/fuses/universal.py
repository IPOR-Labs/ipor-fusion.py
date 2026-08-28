from enum import Enum, auto

from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import Fuse, FuseAction
from ipor_fusion.types import Amount


class UniversalTokenSwapperAbi(Enum):
    """ABI revision of the deployed UniversalTokenSwapperFuse contract.

    Deployed fuses are immutable, so both revisions coexist across chains:
    older deployments (e.g. Base) accept only the LEGACY signature, newer
    ones (e.g. HyperEVM, markets 12 and 1202) only the MIN_AMOUNT_OUT one.
    Pick the member matching the fuse address this instance is bound to.
    """

    LEGACY = auto()
    MIN_AMOUNT_OUT = auto()


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

    @staticmethod
    def _encode_address(tag: int, address: ChecksumAddress) -> bytes:
        payload = bytes.fromhex(address.removeprefix("0x"))
        if len(payload) != 20:
            raise ValueError(f"not a 20-byte address: {address}")
        return bytes([tag]) + b"\x00" * 11 + payload

    @classmethod
    def token(cls, address: ChecksumAddress) -> bytes:
        """Asset allowed as swap input/output."""
        return cls._encode_address(cls._TOKEN, address)

    @classmethod
    def target(cls, address: ChecksumAddress) -> bytes:
        """Contract the swap executor may call (router, or a token that
        receives an `approve` call as part of the swap batch)."""
        return cls._encode_address(cls._TARGET, address)

    @classmethod
    def slippage(cls, wad: int) -> bytes:
        """Vault-side slippage cap as a WAD fraction (1e16 = 1%)."""
        if not 0 <= wad < 1 << 248:
            raise ValueError(f"slippage WAD out of range: {wad}")
        return bytes([cls._SLIPPAGE]) + wad.to_bytes(31, "big")


class UniversalTokenSwapperFuse(Fuse):
    """Fuse for executing arbitrary token swaps through whitelisted DEX targets."""

    def __init__(
        self,
        address: ChecksumAddress,
        abi: UniversalTokenSwapperAbi = UniversalTokenSwapperAbi.LEGACY,
    ):
        super().__init__(address)
        self._abi = abi

    def __eq__(self, other: object) -> bool:
        return super().__eq__(other) and self._abi is other._abi  # type: ignore[attr-defined]

    def __hash__(self) -> int:
        return hash((type(self), self._address, self._abi))

    def swap(
        self,
        *,
        token_in: ChecksumAddress,
        token_out: ChecksumAddress,
        amount_in: Amount,
        targets: list[ChecksumAddress],
        data: list[bytes],
        min_amount_out: Amount | None = None,
    ) -> FuseAction:
        self._validate_address(token_in, "token_in")
        self._validate_address(token_out, "token_out")
        self._validate_amount(amount_in, "amount_in")
        if len(targets) != len(data):
            raise ValueError(
                f"targets and data must have the same length, got {len(targets)} and {len(data)}"
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
                "min_amount_out is required with abi=UniversalTokenSwapperAbi.MIN_AMOUNT_OUT"
                " (pass 0 to rely on the vault-side slippage cap only)"
            )
        self._validate_non_negative(min_amount_out, "min_amount_out")
        return self._action_raw(
            "enter((address,address,uint256,uint256,(address[],bytes[])))",
            [[token_in, token_out, amount_in, min_amount_out, [targets, data]]],
        )
