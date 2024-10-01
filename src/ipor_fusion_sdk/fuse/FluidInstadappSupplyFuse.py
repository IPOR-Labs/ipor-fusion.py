from typing import List

from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion_sdk.fuse.Erc4626SupplyFuse import (
    Erc4626SupplyFuseExitData,
    Erc4626SupplyFuseEnterData,
)
from ipor_fusion_sdk.fuse.Fuse import Fuse
from ipor_fusion_sdk.fuse.FuseActionDynamicStruct import FuseActionDynamicStruct
from ipor_fusion_sdk.operation.BaseOperation import MarketId


# Assuming the MarketId, Fuse, and FuseActionDynamicStruct classes are defined as per previous translations


class FluidInstadappSupplyFuse(Fuse):
    PROTOCOL_ID = "fluid-instadapp"
    ENTER = "enter"
    EXIT = "exit"
    CLAIM = "claim"
    MAX_UINT256 = (
        1 << 256
    ) - 1  # Equivalent to BigInteger.valueOf(2).pow(Uint256.MAX_BIT_LENGTH).subtract(BigInteger.ONE)

    def __init__(
        self,
        fluid_instadapp_pool_token_address: str,
        erc4626_fuse_address: str,
        fluid_instadapp_staking_contract_address: str,
        fluid_instadapp_staking_fuse_address: str,
        fluid_instadapp_claim_fuse_address: str,
    ):
        self.fluid_instadapp_pool_token_address = self._require_non_null(
            fluid_instadapp_pool_token_address,
            "fluidInstadappPoolTokenAddress is required",
        )
        self.erc4626_fuse_address = self._require_non_null(
            erc4626_fuse_address, "erc4626FuseAddress is required"
        )
        self.fluid_instadapp_staking_contract_address = self._require_non_null(
            fluid_instadapp_staking_contract_address,
            "fluidInstadappStakingContractAddress is required",
        )
        self.fluid_staking_fuse_address = self._require_non_null(
            fluid_instadapp_staking_fuse_address,
            "fluidInstadappStakingFuseAddress is required",
        )
        self.fluid_instadapp_claim_fuse_address = self._require_non_null(
            fluid_instadapp_claim_fuse_address,
            "fluidInstadappClaimFuseAddress is required",
        )

    @staticmethod
    def _require_non_null(value, message):
        if value is None:
            raise ValueError(message)
        return value

    def supports(self, market_id: MarketId) -> bool:
        if market_id is None:
            raise ValueError("marketId is required")
        return (
            market_id.protocol_id == self.PROTOCOL_ID
            and market_id.market_id == self.fluid_instadapp_pool_token_address
        )

    def create_fuse_enter_action(
        self, market_id: MarketId, amount: int
    ) -> List[FuseActionDynamicStruct]:
        erc4626_supply_fuse_enter_data = Erc4626SupplyFuseEnterData(
            market_id.market_id, amount
        )

        fluid_staking_enter_data = FluidInstadappStakingSupplyFuseEnterData(
            self.MAX_UINT256, self.fluid_instadapp_staking_contract_address
        )

        return [
            FuseActionDynamicStruct(
                self.erc4626_fuse_address,
                erc4626_supply_fuse_enter_data.function_call(),
            ),
            FuseActionDynamicStruct(
                self.fluid_staking_fuse_address,
                fluid_staking_enter_data.function_call(),
            ),
        ]

    def create_fuse_exit_action(
        self, market_id: MarketId, amount: int
    ) -> List[FuseActionDynamicStruct]:
        fluid_instadapp_staking_exit_data = FluidInstadappStakingSupplyFuseExitData(
            amount, self.fluid_instadapp_staking_contract_address
        )
        erc4626_supply_fuse_exit_data = Erc4626SupplyFuseExitData(
            market_id.market_id, self.MAX_UINT256
        )

        return [
            FuseActionDynamicStruct(
                self.fluid_staking_fuse_address,
                fluid_instadapp_staking_exit_data.function_call(),
            ),
            FuseActionDynamicStruct(
                self.erc4626_fuse_address, erc4626_supply_fuse_exit_data.function_call()
            ),
        ]

    def create_fuse_claim_action(
        self, market_id: MarketId
    ) -> List[FuseActionDynamicStruct]:
        claim_data = b""  # Assuming no specific data for the claim action
        return [
            FuseActionDynamicStruct(self.fluid_instadapp_claim_fuse_address, claim_data)
        ]


class FluidInstadappStakingSupplyFuseEnterData:
    def __init__(self, fluid_token_amount: int, staking_pool_address: str):
        self.fluid_token_amount = fluid_token_amount
        self.staking_pool_address = staking_pool_address

    def encode(self) -> bytes:
        return encode(
            ["uint256", "address"], [self.fluid_token_amount, self.staking_pool_address]
        )

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("enter((uint256,address))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()


class FluidInstadappStakingSupplyFuseExitData:
    def __init__(self, fluid_token_amount: int, staking_pool_address: str):
        self.fluid_token_amount = fluid_token_amount
        self.staking_pool_address = staking_pool_address

    def encode(self) -> bytes:
        return encode(
            ["uint256", "address"], [self.fluid_token_amount, self.staking_pool_address]
        )

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("exit((uint256,address))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()
