"""Explicit, test-only HyperCore model for ``VaultSimulator``.

The current HyperEVM RPC ignores code overrides at native precompile addresses.
Callers must redirect contract source to the ordinary shadow addresses below,
compile that source, and override the deployed callers' code in the simulation.
This models EVM validation only; it cannot prove HyperCore accepts an action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import keccak
from web3 import Web3

from ipor_fusion.readers.hypercore import (
    HyperCoreAccountMarginSummary,
    HyperCorePerpAssetInfo,
    HyperCorePosition,
    HyperCoreSpotBalance,
    HyperCoreTokenInfo,
)

HYPERCORE_SHADOW_CORE_WRITER = Web3.to_checksum_address(
    "0x4444444444444444444444444444444444444444"
)
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


def hypercore_shadow_address(precompile: int) -> ChecksumAddress:
    """Ordinary EVM address corresponding to a HyperCore read precompile."""
    if precompile not in _PRECOMPILES:
        raise ValueError(f"unsupported HyperCore precompile: {precompile:#x}")
    return Web3.to_checksum_address(f"0x{precompile + 0x100:040x}")


def _abi(types: list[str], values: tuple[Any, ...]) -> bytes:
    return encode(types, values)


def _runtime(cases: list[tuple[bytes, bytes]]) -> bytes:
    """Dispatch on the exact ABI input, returning one pre-encoded ABI result."""
    code = bytearray.fromhex("36600060003736600020")
    jumps: list[tuple[int, bytes]] = []
    for argument, result in cases:
        code.extend(b"\x80\x7f" + keccak(argument) + b"\x14\x61\x00\x00\x57")
        jumps.append((len(code) - 3, result))
    code.extend(b"\x50\x00")
    data_patches: list[tuple[int, bytes]] = []
    for jump, result in jumps:
        label = len(code)
        code[jump : jump + 2] = label.to_bytes(2, "big")
        size = len(result)
        code.extend(b"\x5b\x50\x61" + size.to_bytes(2, "big") + b"\x61\x00\x00")
        data_patches.append((len(code) - 2, result))
        code.extend(b"\x60\x00\x39\x61" + size.to_bytes(2, "big") + b"\x60\x00\xf3")
    for patch, result in data_patches:
        code[patch : patch + 2] = len(code).to_bytes(2, "big")
        code.extend(result)
    if len(code) >= 65536:
        raise ValueError("HyperCore model runtime exceeds PUSH2 addressing")
    return bytes(code)


def _writer_runtime() -> bytes:
    # LOG0 records the complete sendRawAction calldata in the simulated receipt.
    return bytes.fromhex("366000600037366000a000")


@dataclass(frozen=True, slots=True)
class HyperCoreSimulationModel:
    """Caller-supplied precompile answers for one simulated baseline state.

    Unknown ABI inputs return no data, making ``HyperCoreLib`` fail closed.
    Values stay fixed within a simulated block. Call ``with_hypercore_model``
    on a later block to supply new balances or positions; its L1 block number
    advances automatically for each simulated block with calls.
    """

    l1_block_number: int
    core_users: dict[ChecksumAddress, bool] = field(default_factory=dict)
    spot_balances: dict[tuple[ChecksumAddress, int], HyperCoreSpotBalance] = field(
        default_factory=dict
    )
    account_summaries: dict[
        tuple[int, ChecksumAddress], HyperCoreAccountMarginSummary
    ] = field(default_factory=dict)
    token_infos: dict[int, HyperCoreTokenInfo] = field(default_factory=dict)
    perp_asset_infos: dict[int, HyperCorePerpAssetInfo] = field(default_factory=dict)
    positions: dict[tuple[ChecksumAddress, int], HyperCorePosition] = field(
        default_factory=dict
    )
    withdrawables: dict[ChecksumAddress, int] = field(default_factory=dict)
    oracle_prices: dict[int, int] = field(default_factory=dict)
    mark_prices: dict[int, int] = field(default_factory=dict)

    def state_overrides(
        self, l1_block_number: int
    ) -> dict[ChecksumAddress, dict[str, str]]:
        """Runtime-code overrides for the ordinary shadow addresses."""
        cases: dict[int, list[tuple[bytes, bytes]]] = {key: [] for key in _PRECOMPILES}
        for (user, token), value in self.spot_balances.items():
            cases[0x801].append(
                (
                    _abi(["address", "uint64"], (user, token)),
                    _abi(
                        ["uint64", "uint64", "uint64"],
                        (value.total, value.hold, value.entry_ntl),
                    ),
                )
            )
        for (dex, user), value in self.account_summaries.items():
            cases[0x80F].append(
                (
                    _abi(["uint32", "address"], (dex, user)),
                    _abi(
                        ["int64", "uint64", "uint64", "int64"],
                        (
                            value.account_value,
                            value.margin_used,
                            value.ntl_pos,
                            value.raw_usd,
                        ),
                    ),
                )
            )
        for user, exists in self.core_users.items():
            cases[0x810].append((_abi(["address"], (user,)), _abi(["bool"], (exists,))))
        for token, value in self.token_infos.items():
            output = (
                value.name,
                value.spots,
                value.deployer_trading_fee_share,
                value.deployer,
                value.evm_contract,
                value.sz_decimals,
                value.wei_decimals,
                value.evm_extra_wei_decimals,
            )
            cases[0x80C].append(
                (
                    _abi(["uint64"], (token,)),
                    _abi(
                        ["(string,uint64[],uint64,address,address,uint8,uint8,int8)"],
                        (output,),
                    ),
                )
            )
        for index, value in self.perp_asset_infos.items():
            output = (
                value.coin,
                value.margin_table_id,
                value.sz_decimals,
                value.max_leverage,
                value.only_isolated,
            )
            cases[0x80A].append(
                (
                    _abi(["uint32"], (index,)),
                    _abi(["(string,uint32,uint8,uint8,bool)"], (output,)),
                )
            )
        for (user, index), value in self.positions.items():
            argument = _abi(["address", "uint32"], (user, index))
            output = _abi(
                ["int64", "uint64", "int64", "uint32", "bool"],
                (
                    value.szi,
                    value.entry_ntl,
                    value.isolated_raw_usd,
                    value.leverage,
                    value.is_isolated,
                ),
            )
            cases[0x800].append((argument, output))
            cases[0x813].append((argument, output))
        for user, amount in self.withdrawables.items():
            cases[0x803].append(
                (_abi(["address"], (user,)), _abi(["uint64"], (amount,)))
            )
        for precompile, values in (
            (0x806, self.mark_prices),
            (0x807, self.oracle_prices),
        ):
            for index, price in values.items():
                cases[precompile].append(
                    (_abi(["uint32"], (index,)), _abi(["uint64"], (price,)))
                )
        cases[0x809].append((b"", _abi(["uint64"], (l1_block_number,))))
        overrides = {
            hypercore_shadow_address(precompile): {
                "code": "0x" + _runtime(entries).hex()
            }
            for precompile, entries in cases.items()
        }
        overrides[HYPERCORE_SHADOW_CORE_WRITER] = {
            "code": "0x" + _writer_runtime().hex()
        }
        return overrides
