from dataclasses import dataclass

from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import Timestamp

from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.fuses.base import FuseAction
from ipor_fusion.types import Amount


@dataclass(slots=True)
class VestingData:
    """Snapshot of reward vesting schedule from the on-chain manager."""

    vesting_time: Timestamp
    update_balance_timestamp: Timestamp
    transferred_tokens: Amount
    last_update_balance: Amount


def _vesting_data_decoder(value: tuple) -> VestingData:
    vesting_time, update_balance_timestamp, transferred_tokens, last_update_balance = (
        value
    )
    return VestingData(
        vesting_time=vesting_time,
        update_balance_timestamp=update_balance_timestamp,
        transferred_tokens=transferred_tokens,
        last_update_balance=last_update_balance,
    )


def _address_list_decoder(value: list) -> list[ChecksumAddress]:
    return [Web3.to_checksum_address(item) for item in value]


class RewardsManager(ContractWrapper):
    """Manager for claiming and transferring rewards from PlasmaVault."""

    def transfer(
        self, asset: ChecksumAddress, to: ChecksumAddress, amount: Amount
    ) -> Call[None]:
        return self._write("transfer(address,address,uint256)", asset, to, amount)

    def balance_of(self) -> Call[Amount]:
        return self._view("balanceOf()", output_types=["uint256"], decoder=Amount)

    def get_vesting_data(self) -> Call[VestingData]:
        return self._view(
            "getVestingData()",
            output_types=["(uint32,uint32,uint128,uint128)"],
            decoder=_vesting_data_decoder,
        )

    def get_rewards_fuses(self) -> Call[list[ChecksumAddress]]:
        return self._view(
            "getRewardsFuses()",
            output_types=["address[]"],
            decoder=_address_list_decoder,
        )

    def is_reward_fuse_supported(self, fuse: ChecksumAddress) -> Call[bool]:
        return self._view("isRewardFuseSupported(address)", fuse, output_types=["bool"])

    def claim_rewards(self, claims: list[FuseAction]) -> Call[None]:
        data = FuseAction.encode_execute_payload(
            claims, "claimRewards((address,bytes)[])"
        )
        return Call(to=self._address, data=data, ctx=self._ctx)

    def update_balance(self) -> Call[None]:
        return self._write("updateBalance()")
