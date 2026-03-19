from eth_abi import decode
from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import TxReceipt

from ipor_fusion.core.contract import ContractWrapper
from ipor_fusion.fuses.base import FuseAction


class RewardsManager(ContractWrapper):

    def transfer(
        self, asset: ChecksumAddress, to: ChecksumAddress, amount: int
    ) -> TxReceipt:
        return self._send("transfer(address,address,uint256)", asset, to, amount)

    def balance_of(self) -> int:
        (value,) = decode(["uint256"], self._call("balanceOf()"))
        return value

    def get_vesting_data(self) -> tuple[int, int, int, int]:
        result = self._call("getVestingData()")
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
        (value,) = decode(["address[]"], self._call("getRewardsFuses()"))
        return [Web3.to_checksum_address(item) for item in list(value)]

    def is_reward_fuse_supported(self, fuse: ChecksumAddress) -> bool:
        (value,) = decode(["bool"], self._call("isRewardFuseSupported(address)", fuse))
        return value

    def claim_rewards(self, claims: list[FuseAction]) -> TxReceipt:
        data = FuseAction.encode_execute_payload(
            claims, "claimRewards((address,bytes)[])"
        )
        return self._ctx.send(self._address, data)

    def update_balance(self) -> TxReceipt:
        return self._send("updateBalance()")
