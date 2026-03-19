from enum import IntEnum


class Roles(IntEnum):
    """AccessManager role identifiers for PlasmaVault permissions."""

    ADMIN_ROLE = 0
    OWNER_ROLE = 1
    GUARDIAN_ROLE = 2
    TECH_PLASMA_VAULT_ROLE = 3
    IPOR_DAO_ROLE = 4
    ATOMIST_ROLE = 100
    ALPHA_ROLE = 200
    FUSE_MANAGER_ROLE = 300
    TECH_PERFORMANCE_FEE_MANAGER_ROLE = 400
    TECH_MANAGEMENT_FEE_MANAGER_ROLE = 500
    CLAIM_REWARDS_ROLE = 600
    TECH_REWARDS_CLAIM_MANAGER_ROLE = 601
    TRANSFER_REWARDS_ROLE = 700
    WHITELIST_ROLE = 800
    CONFIG_INSTANT_WITHDRAWAL_FUSES_ROLE = 900

    @classmethod
    def get_name(cls, value: int) -> str:
        try:
            return cls(value).name
        except ValueError:
            return f"UNKNOWN_ROLE_{value}"
