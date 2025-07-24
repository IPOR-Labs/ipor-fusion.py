import logging

from ipor_fusion.PlasmaSystem import PlasmaSystem
from ipor_fusion.helpers import Addresses

log = logging.getLogger(__name__)


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
        wsteth = system.erc20(Addresses.BASE_WSTETH).balance_of(
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
            system.erc20(Addresses.BASE_WETH).balance_of(
                system.plasma_vault().address()
            )
            / 1e18,
        )

        # Aave V3 collateral position (interest-bearing aTokens)
        log.info(
            "aave collateral: %s aWStETH",
            system.erc20(Addresses.base_aBaswstETH).balance_of(
                system.plasma_vault().address()
            )
            / 1e18,
        )

        # Aave V3 debt position (variable rate debt tokens)
        log.info(
            "  aave borrowed: %s dWETH",
            system.erc20(Addresses.BASE_AAVE_V3_VARIABLE_DEBT_WETH).balance_of(
                system.plasma_vault().address()
            )
            / 1e18,
        )
        log.info("----")
