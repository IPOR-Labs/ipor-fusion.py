import logging
import os
from dataclasses import dataclass
from typing import List

from dotenv import load_dotenv
from eth_typing import ChecksumAddress

from ipor_fusion.PlasmaVaultSystemFactory import PlasmaVaultSystemFactory
from ipor_fusion.Roles import Roles

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

load_dotenv()

provider_url = os.getenv("BASE_PROVIDER_URL")
plasma_vault_address = os.getenv("PLASMA_VAULT_ADDRESS")
anvil_private_key = os.getenv("PRIVATE_KEY")


@dataclass
class RoleAccountGrouped:
    account: ChecksumAddress
    role_id: List[int]
    is_member: bool
    execution_delay: int


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
                    f"- {role_name} ({role_id_text}, execution_delay={role_account.execution_delay}):\n"
                )
            f.write("\n")


if __name__ == "__main__":
    main()
