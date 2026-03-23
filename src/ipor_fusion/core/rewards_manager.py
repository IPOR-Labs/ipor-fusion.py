from dataclasses import dataclass

from eth_abi import decode
from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import Timestamp, TxReceipt

from ipor_fusion.core.contract import ContractWrapper
from ipor_fusion.fuses.base import FuseAction
from ipor_fusion.types import Amount


@dataclass(slots=True)
class VestingData:
    """Snapshot of reward vesting schedule from the on-chain manager."""

    vesting_time: Timestamp
    update_balance_timestamp: Timestamp
    transferred_tokens: Amount
    last_update_balance: Amount


class RewardsManager(ContractWrapper):
    """Manager for claiming and transferring rewards from PlasmaVault."""

    def transfer(
        self, asset: ChecksumAddress, to: ChecksumAddress, amount: Amount
    ) -> TxReceipt:
        return self._send("transfer(address,address,uint256)", asset, to, amount)

    def balance_of(self) -> Amount:
        (value,) = decode(["uint256"], self._call("balanceOf()"))
        return Amount(value)

    def get_vesting_data(self) -> VestingData:
        result = self._call("getVestingData()")
        (
            (
                vesting_time,
                update_balance_timestamp,
                transferred_tokens,
                last_update_balance,
            ),
        ) = decode(["(uint32,uint32,uint128,uint128)"], result)
        return VestingData(
            vesting_time=vesting_time,
            update_balance_timestamp=update_balance_timestamp,
            transferred_tokens=transferred_tokens,
            last_update_balance=last_update_balance,
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
