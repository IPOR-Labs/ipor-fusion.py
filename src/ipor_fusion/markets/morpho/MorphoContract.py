from eth_abi import encode, decode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3.types import TxReceipt

from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.types import MorphoBlueMarketId, Amount, Shares


class MorphoContract:

    def __init__(
        self, transaction_executor: TransactionExecutor, address: ChecksumAddress
    ):
        self.__address = address
        self.__transaction_executor = transaction_executor

    def id_to_market_params(self, morpho_blue_market_id: MorphoBlueMarketId):
        # idToMarketParams(bytes32 id) external view returns (MarketParams memory)
        sig = function_signature_to_4byte_selector("idToMarketParams(bytes32)")
        encoded_args = encode(["bytes32"], [bytes.fromhex(morpho_blue_market_id)])
        read = self.__transaction_executor.read(self.__address, sig + encoded_args)
        (
            (
                loan_token,
                collateral_token,
                oracle,
                irm,
                lltv,
            ),
        ) = decode(["(address,address,address,address,uint256)"], read)
        return MorphoContract.MarketParams(
            loan_token,
            collateral_token,
            oracle,
            irm,
            lltv,
        )

    def position(
        self, morpho_blue_market_id: MorphoBlueMarketId, user: ChecksumAddress
    ):
        # position(bytes32 id, address user) external view returns (Position memory p);
        sig = function_signature_to_4byte_selector("position(bytes32,address)")
        encoded_args = encode(
            ["bytes32", "address"], [bytes.fromhex(morpho_blue_market_id), user]
        )
        read = self.__transaction_executor.read(self.__address, sig + encoded_args)
        (
            (
                supply_shares,
                borrow_shares,
                collateral,
            ),
        ) = decode(["(uint256,uint128,uint128)"], read)
        return MorphoContract.Position(
            supply_shares,
            borrow_shares,
            collateral,
        )

    def market(self, morpho_blue_market_id: MorphoBlueMarketId):
        # market(bytes32 id) external view returns (Market memory m)
        sig = function_signature_to_4byte_selector("market(bytes32)")
        encoded_args = encode(["bytes32"], [bytes.fromhex(morpho_blue_market_id)])
        read = self.__transaction_executor.read(self.__address, sig + encoded_args)
        (
            (
                total_supply_assets,
                total_supply_shares,
                total_borrow_assets,
                total_borrow_shares,
                last_update,
                fee,
            ),
        ) = decode(["(uint128,uint128,uint128,uint128,uint128,uint128)"], read)
        return MorphoContract.Market(
            total_supply_assets,
            total_supply_shares,
            total_borrow_assets,
            total_borrow_shares,
            last_update,
            fee,
        )

    def accrue_interest(self, morpho_blue_market_id: MorphoBlueMarketId) -> TxReceipt:
        # accrueInterest(MarketParams memory marketParams)
        params = self.id_to_market_params(morpho_blue_market_id)

        sig = function_signature_to_4byte_selector(
            "accrueInterest((address,address,address,address,uint256))"
        )
        encoded_args = encode(
            ["(address,address,address,address,uint256)"],
            [
                [
                    params.loan_token,
                    params.collateral_token,
                    params.oracle,
                    params.irm,
                    params.lltv,
                ]
            ],
        )
        return self.__transaction_executor.execute(self.__address, sig + encoded_args)

    class MarketParams:
        loan_token: ChecksumAddress
        collateral_token: ChecksumAddress
        oracle: ChecksumAddress
        irm: ChecksumAddress
        lltv: int

        def __init__(
            self,
            loan_token: ChecksumAddress,
            collateral_token: ChecksumAddress,
            oracle: ChecksumAddress,
            irm: ChecksumAddress,
            lltv: int,
        ):
            self.loan_token = loan_token
            self.collateral_token = collateral_token
            self.oracle = oracle
            self.irm = irm
            self.lltv = lltv

    class Position:
        supply_shares: Shares
        borrow_shares: Shares
        collateral: Amount

        def __init__(
            self,
            supply_shares: Shares,
            borrow_shares: Shares,
            collateral: Amount,
        ):
            self.supply_shares = supply_shares
            self.borrow_shares = borrow_shares
            self.collateral = collateral

    class Market:
        total_supply_assets: Amount
        total_supply_shares: Shares
        total_borrow_assets: Amount
        total_borrow_shares: Shares
        last_update: int
        fee: int

        def __init__(
            self,
            total_supply_assets: Amount,
            total_supply_shares: Shares,
            total_borrow_assets: Amount,
            total_borrow_shares: Shares,
            last_update: int,
            fee: int,
        ):
            self.total_supply_assets = total_supply_assets
            self.total_supply_shares = total_supply_shares
            self.total_borrow_assets = total_borrow_assets
            self.total_borrow_shares = total_borrow_shares
            self.last_update = last_update
            self.fee = fee
