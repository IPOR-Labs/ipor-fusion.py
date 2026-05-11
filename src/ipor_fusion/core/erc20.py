from eth_typing import ChecksumAddress

from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.types import Amount, Decimals


class ERC20(ContractWrapper):
    """Standard ERC-20 token interface wrapper.

    Each method returns a `Call[T]`. Chain `.call()` for reads, `.send()` for
    writes, or hand the `Call` to `VaultSimulator`.
    """

    def transfer(self, to: ChecksumAddress, amount: Amount) -> Call[None]:
        return self._write("transfer(address,uint256)", to, amount)

    def approve(self, spender: ChecksumAddress, amount: Amount) -> Call[None]:
        return self._write("approve(address,uint256)", spender, amount)

    def balance_of(self, account: ChecksumAddress) -> Call[Amount]:
        return self._view(
            "balanceOf(address)", account, output_types=["uint256"], decoder=Amount
        )

    def decimals(self) -> Call[Decimals]:
        return self._view("decimals()", output_types=["uint256"], decoder=Decimals)

    def symbol(self) -> Call[str]:
        return self._view("symbol()", output_types=["string"])

    def name(self) -> Call[str]:
        return self._view("name()", output_types=["string"])

    def total_supply(self) -> Call[Amount]:
        return self._view("totalSupply()", output_types=["uint256"], decoder=Amount)

    def allowance(
        self, owner: ChecksumAddress, spender: ChecksumAddress
    ) -> Call[Amount]:
        return self._view(
            "allowance(address,address)",
            owner,
            spender,
            output_types=["uint256"],
            decoder=Amount,
        )
