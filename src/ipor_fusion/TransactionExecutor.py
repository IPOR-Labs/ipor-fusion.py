from web3.types import TxReceipt

ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
]


class TransactionExecutor:
    DEFAULT_TRANSACTION_MAX_PRIORITY_FEE = 2_000_000_000
    GAS_PRICE_MARGIN = 25

    def __init__(self, web3, account, gas_multiplier=1.25):
        self._web3 = web3
        self._account = account
        self._gas_multiplier = gas_multiplier

    def execute(self, contract_address: str, function: bytes) -> TxReceipt:
        nonce = self._web3.eth.get_transaction_count(self._account.address)
        gas_price = self._web3.eth.gas_price
        max_fee_per_gas = self.calculate_max_fee_per_gas(gas_price)
        max_priority_fee_per_gas = self.get_max_priority_fee(gas_price)
        data = f"0x{function.hex()}"
        estimated_gas = int(
            self._gas_multiplier
            * self._web3.eth.estimate_gas(
                {"to": contract_address, "from": self._account.address, "data": data}
            )
        )

        transaction = {
            "chainId": self._web3.eth.chain_id,
            "gas": estimated_gas,
            "maxFeePerGas": max_fee_per_gas,
            "maxPriorityFeePerGas": max_priority_fee_per_gas,
            "to": contract_address,
            "from": self._account.address,
            "nonce": nonce,
            "data": data,
        }

        signed_tx = self._web3.eth.account.sign_transaction(
            transaction, self._account.key
        )
        tx_hash = self._web3.eth.send_raw_transaction(signed_tx.raw_transaction)
        receipt = self._web3.eth.wait_for_transaction_receipt(tx_hash)
        assert receipt["status"] == 1, "Transaction failed"
        return receipt

    def __read_token_balance(self, holder, token):
        contract = self._web3.eth.contract(
            address=token,
            abi=ERC20_ABI,
        )
        return contract.functions.balanceOf(holder).call()

    def balance_of(self, holder: str, asset: str) -> int:
        return self.__read_token_balance(holder, asset)

    def calculate_max_fee_per_gas(self, gas_price):
        return gas_price + self.percent_of(gas_price, self.GAS_PRICE_MARGIN)

    def get_max_priority_fee(self, gas_price):
        return min(self.DEFAULT_TRANSACTION_MAX_PRIORITY_FEE, gas_price // 10)

    @staticmethod
    def percent_of(value, percentage):
        return value * percentage // 100
