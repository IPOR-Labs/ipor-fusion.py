from eth_account import Account
from web3 import Web3

from ipor_fusion.CheatingTransactionExecutor import CheatingTransactionExecutor
from ipor_fusion.PlasmaVaultSystemFactoryBase import PlasmaVaultSystemFactoryBase


class CheatingPlasmaVaultSystemFactory(PlasmaVaultSystemFactoryBase):

    def __init__(self, provider_url: str, account: Account):
        web3 = Web3(Web3.HTTPProvider(provider_url))
        transaction_executor = CheatingTransactionExecutor(web3, account)
        super().__init__(transaction_executor)
