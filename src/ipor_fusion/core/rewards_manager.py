from eth_abi import encode, decode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.types import TxReceipt

from ipor_fusion.core.context import Web3Context
from ipor_fusion.fuses.base import FuseAction


class RewardsManager:

    def __init__(self, ctx: Web3Context, address: ChecksumAddress):
        self._ctx = ctx
        self._address = Web3.to_checksum_address(address)

    @property
    def address(self) -> ChecksumAddress:
        return self._address

    def transfer(
        self, asset: ChecksumAddress, to: ChecksumAddress, amount: int
    ) -> TxReceipt:
        sig = function_signature_to_4byte_selector("transfer(address,address,uint256)")
        data = sig + encode(["address", "address", "uint256"], [asset, to, amount])
        return self._ctx.send(self._address, data)

    def balance_of(self) -> int:
        sig = function_signature_to_4byte_selector("balanceOf()")
        result = self._ctx.call(self._address, sig)
        (value,) = decode(["uint256"], result)
        return value

    def get_vesting_data(self) -> tuple[int, int, int, int]:
        sig = function_signature_to_4byte_selector("getVestingData()")
        result = self._ctx.call(self._address, sig)
        (
            (
                vesting_time,
                update_balance_timestamp,
                transferred_tokens,
                last_update_balance,
            ),
        ) = decode(["(uint32,uint32,uint128,uint128)"], result)
        return (
            vesting_time,
            update_balance_timestamp,
            transferred_tokens,
            last_update_balance,
        )

    def get_rewards_fuses(self) -> list[ChecksumAddress]:
        sig = function_signature_to_4byte_selector("getRewardsFuses()")
        result = self._ctx.call(self._address, sig)
        (value,) = decode(["address[]"], result)
        return [Web3.to_checksum_address(item) for item in list(value)]

    def is_reward_fuse_supported(self, fuse: ChecksumAddress) -> bool:
        sig = function_signature_to_4byte_selector("isRewardFuseSupported(address)")
        result = self._ctx.call(self._address, sig + encode(["address"], [fuse]))
        (value,) = decode(["bool"], result)
        return value

    def claim_rewards(self, claims: list[FuseAction]) -> TxReceipt:
        bytes_data = [[action.fuse, action.data] for action in claims]
        encoded = encode(["(address,bytes)[]"], [bytes_data])
        data = (
            function_signature_to_4byte_selector("claimRewards((address,bytes)[])")
            + encoded
        )
        return self._ctx.send(self._address, data)

    def update_balance(self) -> TxReceipt:
        sig = function_signature_to_4byte_selector("updateBalance()")
        return self._ctx.send(self._address, sig)
