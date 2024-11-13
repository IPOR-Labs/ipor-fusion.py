from eth_account import Account

from ipor_fusion.PlasmaVaultSystemFactory import PlasmaVaultSystemFactory

# Variables
PROVIDER_URL = "https://arb-mainnet.g.alchemy.com/v2/XXXXXXXXXXXXXXXXXXXXXXXX"
ACCOUNT = Account.from_key(
    private_key="0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
)
PLASMA_VAULT = "0x3F97CEa640B8B93472143f87a96d5A86f1F5167F"

# Setup PlasmaVault System
system = PlasmaVaultSystemFactory(
    provider_url=PROVIDER_URL,
    account=ACCOUNT,
).get(PLASMA_VAULT)

# Get swap transaction
swap = system.uniswap_v3().swap(
    token_in_address=system.usdc().address(),
    token_out_address=system.usdt().address(),
    fee=100,
    token_in_amount=int(500e6),
    min_out_amount=0,
)

# Get new position transaction
new_position = system.ramses_v2().new_position(
    token0=system.usdc().address(),
    token1=system.usdt().address(),
    fee=50,
    tick_lower=-100,
    tick_upper=100,
    amount0_desired=int(499e6),
    amount1_desired=int(499e6),
    amount0_min=0,
    amount1_min=0,
    deadline=int(time.time()) + 100,
    ve_ram_token_id=0,
)

# Execute transactions on PlasmaVault in batch
tx_result = system.plasma_vault().execute([swap, new_position])
