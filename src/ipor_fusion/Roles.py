# SPDX-License-Identifier: BUSL-1.1

from enum import IntEnum


class Roles(IntEnum):
    """Predefined roles used in the IPOR Fusion protocol."""

    ADMIN_ROLE = 0
    """Account with this role has rights to manage the IporFusionAccessManager in general.
       The highest role, which could manage all roles including ADMIN_ROLE and OWNER_ROLE.
       It is recommended to use a MultiSig contract for this role."""

    OWNER_ROLE = 1
    """Account with this role has rights to manage Owners, Guardians, Atomists.
       It is recommended to use a MultiSig contract for this role."""

    GUARDIAN_ROLE = 2
    """Account with this role has rights to cancel time-locked operations, 
       pause restricted methods in PlasmaVault contracts in case of emergency."""

    TECH_PLASMA_VAULT_ROLE = 3
    """Technical role to limit access to methods only from the PlasmaVault contract."""

    IPOR_DAO_ROLE = 4
    """Technical role to limit access to methods only from the PlasmaVault contract."""

    ATOMIST_ROLE = 100
    """Account with this role has rights to manage the PlasmaVault. 
       It is recommended to use a MultiSig contract for this role."""

    ALPHA_ROLE = 200
    """Account with this role has rights to execute the alpha strategy on the PlasmaVault using the execute method."""

    FUSE_MANAGER_ROLE = 300
    """Account with this role has rights to manage the FuseManager contract,
       add or remove fuses, balance fuses and reward fuses."""

    TECH_PERFORMANCE_FEE_MANAGER_ROLE = 400
    """Technical role for the FeeManager. Account with this role has rights to manage the performance fee,
       define the performance fee rate, and manage the performance fee recipient."""

    TECH_MANAGEMENT_FEE_MANAGER_ROLE = 500
    """Technical role for the FeeManager. Account with this role has rights to manage the management fee,
       define the management fee rate, and manage the management fee recipient."""

    CLAIM_REWARDS_ROLE = 600
    """Account with this role has rights to claim rewards from the PlasmaVault using and interacting 
       with the RewardsClaimManager contract."""

    TECH_REWARDS_CLAIM_MANAGER_ROLE = 601
    """Technical role for the RewardsClaimManager contract. 
       Account with this role has rights to claim rewards from the PlasmaVault."""

    TRANSFER_REWARDS_ROLE = 700
    """Account with this role has rights to transfer rewards from the PlasmaVault to the RewardsClaimManager."""

    WHITELIST_ROLE = 800
    """Account with this role has rights to deposit/mint and withdraw/redeem assets from the PlasmaVault."""

    CONFIG_INSTANT_WITHDRAWAL_FUSES_ROLE = 900
    """Account with this role has rights to configure instant withdrawal fuses order."""

    @classmethod
    def get_name(cls, value):
        """Get the role name for a given role value."""
        try:
            return cls(value).name
        except ValueError:
            return f"UNKNOWN_ROLE_{value}"
