import logging
import os

from dotenv import load_dotenv

from ipor_fusion.PlasmaVaultSystemFactory import PlasmaVaultSystemFactory
from ipor_fusion.Roles import Roles

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

    result = system.access_manager().get_accounts_with_roles(
        [role.value for role in Roles]
    )

    with open(file="plasma_vault.md", mode="w", encoding="utf-8") as f:
        for key, value in result.items():
            f.write(f"{Roles.get_name(key)}({key}):\n")
            for account in value:
                f.write(f"- {account}\n")
            f.write("\n")


if __name__ == "__main__":
    main()
