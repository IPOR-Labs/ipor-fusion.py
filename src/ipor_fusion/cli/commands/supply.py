#!/usr/bin/env python3
"""
Show command for fusion CLI
"""
from typing import Optional

import click
from eth_abi import decode
from eth_utils import function_signature_to_4byte_selector
from web3.exceptions import ContractCustomError

from .base import BaseCommand
from ..logging_config import LoggingConfig
from ...PlasmaVaultSystemFactory import PlasmaVaultSystemFactory


logger = LoggingConfig.get_logger(__name__)


@click.command()
@click.option(
    "--market",
    help="Market e.g. aave_v3",
    required=True,
)
@click.option(
    "--asset",
    help="Asset address to supply",
    required=True,
)
@click.option(
    "--config-file",
    help="Asset address to supply",
)
@click.option("--amount", type=int, help="Amount to supply", required=True)
@click.option(
    "--simulate", type=bool, help="Simulate supply", default=False, is_flag=True
)
def supply(
    market: str,
    asset: str,
    amount: int,
    config_file: Optional[str],
    simulate: Optional[bool] = False,
):
    try:
        config = BaseCommand.load_config(config_file)

        plasma_system = PlasmaVaultSystemFactory(
            provider_url=config.rpc_url, private_key=config.private_key
        ).get(plasma_vault_address=config.plasma_vault_address)

        if market == "aave_v3":
            supply = plasma_system.aave_v3().supply(
                asset_address=asset, amount=amount, e_mode=300
            )
            if simulate:
                vault__simulate = plasma_system.plasma_vault().simulate([supply])
            else:
                try:
                    #
                    tx = plasma_system.plasma_vault().execute([supply])
                    logger.info("SUCCESS", tx)
                except ContractCustomError as e:
                    sig = "AccessManagedUnauthorized(address)"
                    selector = function_signature_to_4byte_selector(sig).hex()

                    error_data = e.args[0]  # Surowy hex kod
                    selector_for_error = error_data[:10]
                    (address,) = decode(["address"], bytes.fromhex(error_data[10:]))
                    code = selector_for_error.replace("0x", "")
                    if code == selector:
                        logger.error(f"AccessManagedUnauthorized(address={address})", e)
                        logger.error(f"alph={plasma_system.alpha()}", e)
                        logger.error(f"config.private_key={config.private_key}", e)
                    else:
                        logger.error("Unknown contract error", e)

                    logger.error(f"alph={plasma_system.alpha()}", e)
                    logger.error(f"config.private_key={config.private_key}", e)

        else:
            click.secho("Market not supported", fg="red")
            return

    except Exception as e:
        click.secho(f"Error loading configuration: {e}", fg="red")
        raise
