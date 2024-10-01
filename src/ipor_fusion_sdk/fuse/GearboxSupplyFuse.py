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


class GearboxSupplyFuse(Fuse):
    PROTOCOL_ID = "gearbox-v3"
    ENTER = "enter"
    EXIT = "exit"
    CLAIM = "claim"
    MAX_UINT256 = (1 << 256) - 1

    def __init__(
        self,
        d_token_address: str,
        erc4626_fuse_address: str,
        farmd_token_address: str,
        farm_fuse_address: str,
        claim_fuse_address: str,
    ):
        self.d_token_address = self._require_non_null(
            d_token_address, "dTokenAddress is required"
        )
        self.erc4626_fuse_address = self._require_non_null(
            erc4626_fuse_address, "erc4626FuseAddress is required"
        )
        self.farmd_token_address = self._require_non_null(
            farmd_token_address, "farmdTokenAddress is required"
        )
        self.farm_fuse_address = self._require_non_null(
            farm_fuse_address, "farmFuseAddress is required"
        )
        self.claim_fuse_address = self._require_non_null(
            claim_fuse_address, "claimFuseAddress is required"
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
            and market_id.market_id == self.d_token_address
        )

    def create_fuse_enter_action(
        self, market_id: MarketId, amount: int
    ) -> List[FuseActionDynamicStruct]:
        erc4626_supply_fuse_enter_data = Erc4626SupplyFuseEnterData(
            market_id.market_id, amount
        )
        gearbox_v3_farmd_supply_fuse_enter_data = GearboxV3FarmdSupplyFuseEnterData(
            self.MAX_UINT256, self.farmd_token_address
        )
        return [
            FuseActionDynamicStruct(
                self.erc4626_fuse_address,
                erc4626_supply_fuse_enter_data.function_call(),
            ),
            FuseActionDynamicStruct(
                self.farm_fuse_address,
                gearbox_v3_farmd_supply_fuse_enter_data.function_call(),
            ),
        ]

    def create_fuse_exit_action(
        self, market_id: MarketId, amount: int
    ) -> List[FuseActionDynamicStruct]:
        gearbox_v3_farmd_supply_fuse_exit_data = GearboxV3FarmdSupplyFuseExitData(
            amount, self.farmd_token_address
        )
        erc4626_supply_fuse_exit_data = Erc4626SupplyFuseExitData(
            market_id.market_id, self.MAX_UINT256
        )
        return [
            FuseActionDynamicStruct(
                self.farm_fuse_address,
                gearbox_v3_farmd_supply_fuse_exit_data.function_call(),
            ),
            FuseActionDynamicStruct(
                self.erc4626_fuse_address, erc4626_supply_fuse_exit_data.function_call()
            ),
        ]

    def create_fuse_claim_action(
        self, market_id: MarketId
    ) -> List[FuseActionDynamicStruct]:
        claim_data = b""  # Assuming no specific data for the claim action
        return [FuseActionDynamicStruct(self.claim_fuse_address, claim_data)]


class GearboxV3FarmdSupplyFuseEnterData:
    def __init__(self, d_token_amount: int, farmd_token: str):
        self.d_token_amount = d_token_amount
        self.farmd_token = farmd_token

    def encode(self) -> bytes:
        return encode(["uint256", "address"], [self.d_token_amount, self.farmd_token])

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("enter((uint256,address))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()


class GearboxV3FarmdSupplyFuseExitData:
    def __init__(self, d_token_amount: int, farmd_token: str):
        self.d_token_amount = d_token_amount
        self.farmd_token = farmd_token

    def encode(self) -> bytes:
        return encode(["uint256", "address"], [self.d_token_amount, self.farmd_token])

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("exit((uint256,address))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()
