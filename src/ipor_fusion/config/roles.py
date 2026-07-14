import difflib
import re
from enum import IntEnum


class Roles(IntEnum):
    """Predefined roles used in the IPOR Fusion protocol.

    Roles prefixed with 'TECH_' are special system roles that can only
    be assigned to and executed by contracts within the PlasmaVault ecosystem.
    """

    ADMIN_ROLE = 0
    OWNER_ROLE = 1
    GUARDIAN_ROLE = 2
    TECH_PLASMA_VAULT_ROLE = 3
    IPOR_DAO_ROLE = 4
    TECH_CONTEXT_MANAGER_ROLE = 5
    TECH_WITHDRAW_MANAGER_ROLE = 6
    TECH_VAULT_TRANSFER_SHARES_ROLE = 7
    ATOMIST_ROLE = 100
    ALPHA_ROLE = 200
    FUSE_MANAGER_ROLE = 300
    PRE_HOOKS_MANAGER_ROLE = 301
    TECH_PERFORMANCE_FEE_MANAGER_ROLE = 400
    TECH_MANAGEMENT_FEE_MANAGER_ROLE = 500
    CLAIM_REWARDS_ROLE = 600
    TECH_REWARDS_CLAIM_MANAGER_ROLE = 601
    TRANSFER_REWARDS_ROLE = 700
    WHITELIST_ROLE = 800
    CONFIG_INSTANT_WITHDRAWAL_FUSES_ROLE = 900
    WITHDRAW_MANAGER_REQUEST_FEE_ROLE = 901
    WITHDRAW_MANAGER_WITHDRAW_FEE_ROLE = 902
    UPDATE_MARKETS_BALANCES_ROLE = 1000
    UPDATE_REWARDS_BALANCE_ROLE = 1100
    PRICE_ORACLE_MIDDLEWARE_MANAGER_ROLE = 1200
    PUBLIC_ROLE = 2**64 - 1

    @classmethod
    def get_name(cls, value: int) -> str:
        try:
            return cls(value).name
        except ValueError:
            return f"UNKNOWN_ROLE_{value}"

    @classmethod
    def resolve(cls, name: str) -> int:
        """Resolve a human-entered role name to its id (fail-closed).

        Case-insensitive; spaces/hyphens map to underscores and the `_ROLE`
        suffix is optional. Unknown (or blank) names raise `ValueError` listing
        the valid names, with a "did you mean" hint on a near miss.
        """
        key = re.sub(r"[\s\-]+", "_", name.strip()).upper()
        if not key.endswith("_ROLE"):
            key += "_ROLE"
        try:
            return cls[key].value
        except KeyError:
            names = [role.name for role in cls]
            near = difflib.get_close_matches(key, names, n=1)
            hint = f" Did you mean {near[0]}?" if near else ""
            raise ValueError(
                f"Unknown role {name!r}.{hint} Valid: {cls.names_str()}"
            ) from None

    @classmethod
    def names_str(cls) -> str:
        """Canonical role names, comma-separated — for help texts and errors."""
        return ", ".join(role.name for role in cls)
