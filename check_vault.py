import logging
import os

from dotenv import load_dotenv

from ipor_fusion.PlasmaVaultSystemFactory import PlasmaVaultSystemFactory
from ipor_fusion.Roles import Roles
from ipor_fusion.error.UnsupportedMarketError import UnsupportedMarketError

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

load_dotenv()

provider_url = os.getenv("BASE_PROVIDER_URL")
plasma_vault_address = os.getenv("PLASMA_VAULT_ADDRESS")
anvil_private_key = os.getenv("PRIVATE_KEY")


def main():
    # setup
    system = PlasmaVaultSystemFactory(
        provider_url=provider_url,
        private_key=anvil_private_key,
    ).get(plasma_vault_address)

    result = system.access_manager().get_all_role_accounts()

    unique_accounts = []
    for role_account in result:
        if not role_account.account in unique_accounts:
            unique_accounts.append(role_account.account)

    with open(file="plasma_vault.md", mode="w", encoding="utf-8") as f:
        f.write(f"# PlasmaVault\n# {plasma_vault_address}\n")
        f.write(f"# chain_id={system.chain_id()}\n\n")
        f.write("## Accounts with roles:\n")
        for account in unique_accounts:
            role_accounts = [
                role_account
                for role_account in result
                if role_account.account == account
            ]
            f.write(f"{account} \n")
            for role_account in role_accounts:
                role_name = Roles.get_name(role_account.role_id).ljust(36)
                role_id_text = f"role_id={role_account.role_id}".ljust(11)
                f.write(
                    f"- {role_name} ({role_id_text}, execution_delay={role_account.execution_delay})\n"
                )
            f.write("\n")

        f.write("## getFuses\n")
        for fuse in system.plasma_vault().get_fuses():
            f.write(f"- {fuse}\n")
        f.write("\n")

        check_uniswap_v3(f, system)
        check_ramses_v2(f, system)
        check_aave_v3(f, system)
        check_universal(f, system)
        check_compound_v3(f, system)
        check_fluid_instadapp(f, system)


def check_fluid_instadapp(f, system):
    try:
        system.fluid_instadapp()
        f.write("- fluid_instadapp")
    except UnsupportedMarketError:
        pass


def check_compound_v3(f, system):
    try:
        system.compound_v3()
        f.write("- compound_v3")
    except UnsupportedMarketError:
        pass


def check_universal(f, system):
    try:
        system.universal()
        f.write("- universal")
    except UnsupportedMarketError:
        pass


def check_aave_v3(f, system):
    try:
        system.aave_v3()
        f.write("- aave_v3")
    except UnsupportedMarketError:
        pass


def check_ramses_v2(f, system):
    try:
        system.ramses_v2()
        f.write("- ramses_v2")
    except UnsupportedMarketError:
        pass


def check_uniswap_v3(f, system):
    f.write("## Supported markets\n")
    try:
        system.uniswap_v3()
        f.write("- uniswap_v3")
    except UnsupportedMarketError:
        pass


if __name__ == "__main__":
    main()
