import logging

from web3 import Web3

from ipor_fusion.PlasmaSystem import PlasmaSystem

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Token contract addresses on Base network
wsteth_address = Web3.to_checksum_address(
    "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452"
)  # Lido Wrapped Staked ETH - liquid staking derivative token
weth_address = Web3.to_checksum_address(
    "0x4200000000000000000000000000000000000006"
)  # Wrapped ETH token on Base network
variableDebtBasWETH_address = Web3.to_checksum_address(
    "0x24e6e0795b3c7c71D965fCc4f371803d1c1DcA1E"
)  # Aave V3 variable debt token representing borrowed WETH
aBaswstETH_address = Web3.to_checksum_address(
    "0x99CBC45ea5bb7eF3a5BC08FB1B7E56bB2442Ef0D"
)  # Aave V3 interest-bearing token representing supplied WStETH collateral


class BalanceLogger:
    @staticmethod
    def log_balances(system: PlasmaSystem, msg: str):
        """
        Log current balances across different positions for strategy monitoring.

        This helper tracks:
        - Direct token holdings in the vault
        - Aave V3 collateral positions (aTokens)
        - Aave V3 debt positions (debt tokens)

        Args:
            system: Plasma system interface
            msg: Description of the current state
        """
        log.info("[%s]", msg)

        # Direct WStETH holdings in vault
        wsteth = system.erc20(wsteth_address).balance_of(
            system.plasma_vault().address()
        )
        if wsteth > 0:
            log.info(
                "    wsteth balance: %s WStETH",
                wsteth / 1e18,
            )

        # Direct WETH holdings in vault
        log.info(
            "      weth balance: %s WETH",
            system.erc20(weth_address).balance_of(system.plasma_vault().address())
            / 1e18,
        )

        # Aave V3 collateral position (interest-bearing aTokens)
        log.info(
            "aave collateral: %s aWStETH",
            system.erc20(aBaswstETH_address).balance_of(system.plasma_vault().address())
            / 1e18,
        )

        # Aave V3 debt position (variable rate debt tokens)
        log.info(
            "  aave borrowed: %s dWETH",
            system.erc20(variableDebtBasWETH_address).balance_of(
                system.plasma_vault().address()
            )
            / 1e18,
        )
        log.info("----")
