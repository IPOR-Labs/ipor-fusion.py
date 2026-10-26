"""Address spellings that EIP-55 must reject."""

from web3 import Web3


def miscased(address: str) -> str:
    """``address`` checksummed with one letter's case flipped: the same 20 bytes,
    a wrong checksum. Only a mixed-case result carries a checksum, so the first
    flip that leaves the digits mixed case is taken."""
    checksummed = Web3.to_checksum_address(address)
    for i, c in enumerate(checksummed[2:], start=2):
        if c.isalpha():
            flipped = checksummed[:i] + c.swapcase() + checksummed[i + 1 :]
            digits = flipped[2:]
            if digits not in (digits.lower(), digits.upper()):
                return flipped
    raise AssertionError(f"no wrong checksum is possible for {address}")
