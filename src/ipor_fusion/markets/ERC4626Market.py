from eth_typing import ChecksumAddress

from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.fuse.Erc4626SupplyFuse import Erc4626SupplyFuse
from ipor_fusion.fuse.FuseAction import FuseAction
from ipor_fusion.types import Amount


class ERC4626Market:

    def __init__(
        self,
        chain_id: int,
        transaction_executor: TransactionExecutor,
        fuse_address: ChecksumAddress,
    ):
        if not chain_id:
            raise ValueError("chain_id is required")
        if not transaction_executor:
            raise ValueError("transaction_executor is required")
        if not fuse_address:
            raise ValueError("fuse_address is required")

        self._chain_id = chain_id
        self._transaction_executor = transaction_executor
        self._fuse_address = fuse_address

    def supply(self, vault_address: ChecksumAddress, amount: Amount) -> FuseAction:
        if not vault_address:
            raise ValueError("vault_address is required")
        if not amount:
            raise ValueError("amount is required")
        fuse = Erc4626SupplyFuse(
            fuse_address=self._fuse_address, erc4626_address=vault_address
        )
        return fuse.supply(vault_address=vault_address, amount=amount)

    def withdraw(self, vault_address: ChecksumAddress, amount: Amount) -> FuseAction:
        if not vault_address:
            raise ValueError("vault_address is required")
        if not amount:
            raise ValueError("amount is required")
        fuse = Erc4626SupplyFuse(
            fuse_address=self._fuse_address, erc4626_address=vault_address
        )
        return fuse.withdraw(vault_address=vault_address, amount=amount)
