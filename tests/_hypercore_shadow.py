"""Build source-redirected HyperCore runtimes for opt-in simulations."""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from _foundry import FoundryContract, SolidityArtifact, compile_foundry_contracts
from eth_typing import ChecksumAddress
from web3 import Web3

_FUSES = (
    "HyperCoreBalanceFuse",
    "HyperCoreBuilderFeeFuse",
    "HyperCoreCancelFuse",
    "HyperCoreDepositFuse",
    "HyperCoreMarginFuse",
    "HyperCoreOrderFuse",
    "HyperCoreSendFuse",
)
_HOOKS = ("HyperCoreCapitalFlowPreHook", "HyperCorePendingActionPreHook")
_PRECOMPILES = (
    0x800,
    0x801,
    0x803,
    0x806,
    0x807,
    0x809,
    0x80A,
    0x80C,
    0x80F,
    0x810,
    0x813,
)
_CORE_WRITER = bytes.fromhex("33" * 20)
_SHADOW_WRITER = bytes.fromhex("44" * 20)


def _contracts() -> tuple[FoundryContract, ...]:
    return tuple(
        FoundryContract(f"contracts/fuses/hypercore/{name}.sol", name)
        for name in _FUSES
    ) + tuple(
        FoundryContract(f"contracts/handlers/pre_hooks/pre_hooks/{name}.sol", name)
        for name in _HOOKS
    )


def _redirect_source(source_root: Path, target_root: Path) -> None:
    shutil.copytree(source_root / "contracts", target_root / "contracts")
    shutil.copyfile(source_root / "foundry.toml", target_root / "foundry.toml")
    for dependency in ("node_modules", "lib"):
        if (source_root / dependency).exists():
            (target_root / dependency).symlink_to(source_root / dependency)
    library = target_root / "contracts/fuses/hypercore/lib/HyperCoreLib.sol"
    source = library.read_text()
    for precompile in _PRECOMPILES:
        old = f"address(0x{precompile:X})"
        if source.count(old) != 1:
            raise ValueError(f"expected one HyperCoreLib constant for {precompile:#x}")
        source = source.replace(old, f"address(0x{precompile + 0x100:X})")
    old_writer = "0x" + _CORE_WRITER.hex()
    if source.count(old_writer) != 1:
        raise ValueError("expected one HyperCoreLib CoreWriter constant")
    library.write_text(source.replace(old_writer, "0x" + _SHADOW_WRITER.hex()))


def _executable_end(code: bytes) -> int:
    metadata_length = int.from_bytes(code[-2:], "big")
    end = len(code) - metadata_length - 2
    if not 0 < end < len(code):
        raise ValueError("invalid Solidity metadata suffix")
    return end


def _assert_only_address_redirects(original: bytes, shadow: bytes) -> None:
    if len(original) != len(shadow):
        raise ValueError("shadow runtime length changed")
    end = _executable_end(original)
    if end != _executable_end(shadow):
        raise ValueError("shadow executable length changed")
    allowed = {
        (old.to_bytes(2, "big"), (old + 0x100).to_bytes(2, "big"))
        for old in _PRECOMPILES
    }
    allowed.add((_CORE_WRITER, _SHADOW_WRITER))
    index = 0
    while index < end:
        opcode = original[index]
        if opcode != shadow[index]:
            raise ValueError(f"shadow opcode changed at {index}")
        width = opcode - 0x5F if 0x60 <= opcode <= 0x7F else 0
        before = original[index + 1 : index + 1 + width]
        after = shadow[index + 1 : index + 1 + width]
        if before != after and (before, after) not in allowed:
            raise ValueError(f"unexpected shadow immediate at {index}")
        index += 1 + width


def _bound_runtime(
    original: SolidityArtifact,
    shadow: SolidityArtifact,
    deployed: bytes,
) -> bytes:
    _assert_only_address_redirects(original.runtime_code, shadow.runtime_code)
    end = _executable_end(original.runtime_code)
    if len(deployed) != len(original.runtime_code) or _executable_end(deployed) != end:
        raise ValueError(
            "deployed HyperCore runtime length differs from compiled source"
        )
    immutable_bytes = {
        index
        for start, length in original.immutable_ranges
        for index in range(start, start + length)
    }
    if any(
        original.runtime_code[index] != deployed[index]
        for index in range(end)
        if index not in immutable_bytes
    ):
        raise ValueError("deployed HyperCore executable differs from compiled source")
    if original.immutable_ranges != shadow.immutable_ranges:
        raise ValueError("shadow immutable layout changed")
    result = bytearray(shadow.runtime_code)
    for start, length in original.immutable_ranges:
        result[start : start + length] = deployed[start : start + length]
    return bytes(result)


def compile_hypercore_shadow_runtimes(
    source_root: Path,
    web3: Web3,
    addresses: dict[str, ChecksumAddress],
    block: int,
    remappings: Sequence[str] = (),
) -> dict[ChecksumAddress, bytes]:
    """Compile both sources and bind shadow code only after deployed-code proof."""
    contracts = _contracts()
    original = compile_foundry_contracts(
        source_root, contracts, remappings=remappings, timeout=300
    )
    with TemporaryDirectory(prefix="hypercore-shadow-") as directory:
        target_root = Path(directory)
        _redirect_source(source_root, target_root)
        shadow = compile_foundry_contracts(
            target_root, contracts, remappings=remappings, timeout=300
        )
    result = {}
    for contract in contracts:
        address = addresses[contract.name]
        deployed = bytes(web3.eth.get_code(address, block_identifier=block))
        result[address] = _bound_runtime(original[contract], shadow[contract], deployed)
    return result
