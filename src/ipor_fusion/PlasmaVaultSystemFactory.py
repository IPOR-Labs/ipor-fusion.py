from eth_account import Account
from web3 import Web3

from ipor_fusion.PlasmaVaultSystemFactoryBase import PlasmaVaultSystemFactoryBase
from ipor_fusion.TransactionExecutor import TransactionExecutor


class PlasmaVaultSystemFactory(PlasmaVaultSystemFactoryBase):

    def __init__(self, provider_url: str, account: Account):
        web3 = Web3(Web3.HTTPProvider(provider_url))
        transaction_executor = TransactionExecutor(web3, account)
        super().__init__(transaction_executor)
