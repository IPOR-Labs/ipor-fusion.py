import logging
import os

from dotenv import load_dotenv

from ipor_fusion.PlasmaVaultSystemFactory import PlasmaVaultSystemFactory
from ipor_fusion.Roles import Roles
from ipor_fusion.error.UnsupportedMarketError import UnsupportedMarketError

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

load_dotenv()

arbitrum_provider_url = os.getenv("ARBITRUM_PROVIDER_URL")
base_provider_url = os.getenv("BASE_PROVIDER_URL")
anvil_private_key = os.getenv("PRIVATE_KEY")

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


class ProviderUrlVaultAddressDto:
    def __init__(self, provider_url, address):
        self.provider_url = provider_url
        self.address = address


vaults = []
vaults.append(
    ProviderUrlVaultAddressDto(
        arbitrum_provider_url, "0xea7aB80e2be196152f59ab7Efb3Eb4bff49ad3D4"
    )
)
vaults.append(
    ProviderUrlVaultAddressDto(
        arbitrum_provider_url, "0xea7aB80e2be196152f59ab7Efb3Eb4bff49ad3D4"
    )
)
vaults.append(
    ProviderUrlVaultAddressDto(
        arbitrum_provider_url, "0xfc0c84ab96Dc00E38CbdE2C6252d3c75655b6F9e"
    )
)
vaults.append(
    ProviderUrlVaultAddressDto(
        base_provider_url, "0x55d8d6e5F17F153f3250b229D5AAc9437e908a77"
    )
)


def check_vault(provider_url, plasma_vault_address):
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

    with open(
        file=f"raports/{plasma_vault_address}.md", mode="w", encoding="utf-8"
    ) as f:
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

        f.write(f"access_manager_address = {system.access_manager().address()}\n\n")

        f.write(f"withdraw_manager_address = {system.withdraw_manager().address()}\n")
        if system.withdraw_manager().address() != ZERO_ADDRESS:
            f.write(
                f"- getWithdrawWindow() = {system.withdraw_manager().get_withdraw_window()}\n\n"
            )

        f.write(f"rewards_claim_manager = {system.rewards_claim_manager().address()}\n")
        if system.rewards_claim_manager().address() != ZERO_ADDRESS:
            (
                vesting_time,
                update_balance_timestamp,
                transferred_tokens,
                last_update_balance,
            ) = system.rewards_claim_manager().get_vesting_data()
            f.write(
                f"- getVestingData[vesting_time={vesting_time}, update_balance_timestamp={update_balance_timestamp}, transferred_tokens={transferred_tokens}, last_update_balance={last_update_balance}]\n\n"
            )

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
        f.write("- fluid_instadapp\n")
    except UnsupportedMarketError:
        pass


def check_compound_v3(f, system):
    try:
        system.compound_v3()
        f.write("- compound_v3\n")
    except UnsupportedMarketError:
        pass


def check_universal(f, system):
    try:
        system.universal()
        f.write("- universal\n")
        f.write(
            f"get_market_substrates(UNIVERSAL_TOKEN_SWAPPER=12) = {system.plasma_vault().get_market_substrates(12).hex()}\n"
        )
    except UnsupportedMarketError:
        pass


def check_aave_v3(f, system):
    try:
        system.aave_v3()
        f.write("- aave_v3\n")
    except UnsupportedMarketError:
        pass


def check_ramses_v2(f, system):
    try:
        system.ramses_v2()
        f.write("- ramses_v2\n")
        f.write(
            f"get_market_substrates(RAMSES_V2_POSITIONS=18) = {system.plasma_vault().get_market_substrates(18).hex()}\n"
        )
    except UnsupportedMarketError:
        pass


def check_uniswap_v3(f, system):
    f.write("## Supported markets\n")
    try:
        system.uniswap_v3()
        f.write("- uniswap_v3\n")
    except UnsupportedMarketError:
        pass


def main():
    for vault in vaults:
        check_vault(vault.provider_url, vault.address)


if __name__ == "__main__":
    main()
