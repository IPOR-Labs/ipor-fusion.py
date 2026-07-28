"""Single source of truth for `vault info` output-field documentation.

The CLI writes these strings into the JSON payload as ``<field>_note`` keys.
They are also the intended source for the MCP models' Pydantic
``Field(description=...)`` once those blocks are typed, so that the payload
prose and the published output schema cannot drift apart.

Lives in the SDK (not in `cli/` or `mcp/`) because the two adapters are
siblings: MCP must not import `cli.*`, and the CLI must not depend on the
`mcp` extra.

Stability: the KEYS are API — adding or removing one changes what a consumer
can look up. The STRINGS are not; they are editorial and get reworded freely,
including to correct a factual claim about on-chain behavior.

Fee wording follows the IPOR web app.
"""

# Keyed by output block, then by field within that block. Nested rather than
# flat because field names repeat across blocks (`raw`, `formatted`, `error`)
# and the per-block key set is what a schema drift-guard asserts against.
DOCS: dict[str, dict[str, str]] = {
    "fees": {
        "fee_manager": "FeeManager contract governing this vault's fees.",
        "ipor_dao_fee_recipient": (
            "Address receiving the IPOR DAO's share of the performance and "
            "management fees."
        ),
        "deposit_fee_percent": (
            "Onboarding Contribution: charged when a deposit is made into the "
            "vault, in shares rather than assets. null when not known - most "
            "often because this vault's FeeManager predates the deposit fee."
        ),
        "request_fee_percent": (
            "Offboarding Contribution for Scheduled Withdrawals: charged at "
            "request time. Applies only to the Scheduled Withdrawal path, "
            "never to an Instant Withdrawal – a user pays one path, never both."
        ),
        "withdraw_fee_percent": (
            "Offboarding Contribution for Instant Withdrawals: charged when "
            "performing an instant withdrawal. Applies only to the Instant "
            "Withdrawal path, never to a scheduled withdrawal request – a user "
            "pays one path, never both."
        ),
        "performance_fee_percent": (
            "Performance Fee the vault actually charges, on share-price gains "
            "above the high-water mark (marked to market, so unrealized gains "
            "count)."
        ),
        "performance_fee_manager_percent": (
            "The same fee as recorded by the FeeManager. Expected to equal "
            "performance_fee_percent; a mismatch means the vault and its "
            "FeeManager have drifted out of sync."
        ),
        "performance_fee_recipients": (
            "Named recipients of the performance fee and their shares. "
            "Excludes the IPOR DAO, whose share is the remainder up to "
            "performance_fee_manager_percent."
        ),
        "high_water_mark": (
            "The performance fee applies only to gains above this exchange "
            "rate: the assets one whole share converts to, in underlying-asset "
            "units scaled by 10**asset_decimals. null when not known - most "
            "often because this vault's FeeManager predates high-water-mark "
            "support."
        ),
        "management_fee_percent": (
            "Management Fee the vault actually charges, annualized on total assets."
        ),
        "management_fee_manager_percent": (
            "The same fee as recorded by the FeeManager. Expected to equal "
            "management_fee_percent; a mismatch means the vault and its "
            "FeeManager have drifted out of sync."
        ),
        "management_fee_recipients": (
            "Named recipients of the management fee and their shares. Excludes "
            "the IPOR DAO, whose share is the remainder up to "
            "management_fee_manager_percent."
        ),
        "management_fee_last_update_timestamp": (
            "Unix timestamp of the last management-fee accrual update; 0 if "
            "never accrued."
        ),
        "unrealized_management_fee": (
            "Management fee accrued but not yet collected, in underlying asset "
            "units. NOT deducted from total_assets, which is gross of fees per "
            "ERC-4626; subtract this to get net assets."
        ),
    },
    # Exit fees are NOT here — they live in the top-level `fees` object;
    # `fees` below is the cross-reference that keeps a reader from concluding
    # the vault charges nothing on exit.
    "withdraw_manager_details": {
        "withdraw_window_seconds": (
            "Length of the window, starting at request time, in which a "
            "scheduled withdrawal can be executed (also requires the vault "
            "Alpha to release funds after the request)."
        ),
        "shares_to_release": (
            "Current shares the vault Alpha has approved for release via "
            "releaseFunds()."
        ),
        "last_release_funds_timestamp": (
            "Release timestamp set by the last releaseFunds() call; 0 if never released."
        ),
        "total_pending_shares": (
            "Sum of shares across all pending withdrawal requests."
        ),
        "fees": (
            "The two Offboarding Contributions (for Scheduled and for Instant "
            "Withdrawals) are reported in the top-level `fees` object, not here."
        ),
    },
}
