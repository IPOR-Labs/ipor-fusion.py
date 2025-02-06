from typing import Optional

from eth_account import Account
from web3 import Web3

from ipor_fusion.PlasmaVaultSystemFactoryBase import PlasmaVaultSystemFactoryBase
from ipor_fusion.TransactionExecutor import TransactionExecutor


class PlasmaVaultSystemFactory(PlasmaVaultSystemFactoryBase):

    def __init__(self, provider_url: str, private_key: Optional[str] = None):
        if provider_url.startswith(("https://", "http://")):
            self._w3 = Web3(Web3.HTTPProvider(provider_url))
        elif provider_url.startswith(("wss://", "ws://")):
            self._w3 = Web3(Web3.LegacyWebSocketProvider(provider_url))
        else:
            raise ValueError(
                "Invalid provider URL. Must start with http(s):// or ws(s)://"
            )

        # Account setup if private key provided
        self._account = None
        if private_key:
            # pylint: disable=no-value-for-parameter
            self._account = Account.from_key(private_key)

        transaction_executor = TransactionExecutor(self._w3, self._account)

        if not self._w3.is_connected():
            raise ConnectionError(f"Failed to connect to {provider_url}")

        super().__init__(transaction_executor)
