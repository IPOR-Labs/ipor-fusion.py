from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuses.base import Fuse, FuseAction
from ipor_fusion.types import MAX_UINT256


class FluidInstadappSupplyFuse(Fuse):
    def __init__(
        self,
        erc4626_fuse_address: ChecksumAddress,
        staking_fuse_address: ChecksumAddress,
        pool_token_address: ChecksumAddress,
        staking_contract_address: ChecksumAddress,
    ):
        super().__init__(erc4626_fuse_address)
        self._staking_fuse_address = staking_fuse_address
        self._pool_token_address = pool_token_address
        self._staking_contract_address = staking_contract_address

    def supply_and_stake(
        self, vault_address: ChecksumAddress, amount: int
    ) -> list[FuseAction]:
        enter_data = encode(["address", "uint256"], [vault_address, amount])
        enter_selector = function_signature_to_4byte_selector(
            "enter((address,uint256))"
        )

        stake_data = encode(
            ["uint256", "address"], [MAX_UINT256, self._staking_contract_address]
        )
        stake_selector = function_signature_to_4byte_selector(
            "enter((uint256,address))"
        )

        return [
            FuseAction(fuse=self._address, data=enter_selector + enter_data),
            FuseAction(
                fuse=self._staking_fuse_address, data=stake_selector + stake_data
            ),
        ]

    def unstake_and_withdraw(
        self, vault_address: ChecksumAddress, amount: int
    ) -> list[FuseAction]:
        unstake_data = encode(
            ["uint256", "address"], [amount, self._staking_contract_address]
        )
        unstake_selector = function_signature_to_4byte_selector(
            "exit((uint256,address))"
        )

        exit_data = encode(["address", "uint256"], [vault_address, MAX_UINT256])
        exit_selector = function_signature_to_4byte_selector("exit((address,uint256))")

        return [
            FuseAction(
                fuse=self._staking_fuse_address, data=unstake_selector + unstake_data
            ),
            FuseAction(fuse=self._address, data=exit_selector + exit_data),
        ]
