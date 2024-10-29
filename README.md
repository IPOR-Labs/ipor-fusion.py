<p align="center">
    <img height="80px" width="80px" src="https://ipor.io/images/ipor-fusion.svg" alt="IPOR Fusion Python SDK"/>
    <h1 align="center">IPOR Fusion Python SDK</h1>
</p>

`ipor_fusion` package is the official IPOR Fusion Software Development Kit (SDK) for Python. It allows Python 
developers to 
write software, that interacts with **IPOR Fusion Plasma Vaults** smart contracts deployed on Ethereum Virtual 
Machine (EVM) blockchains.

`ipor-fusion.py` repository is maintained by <a href="https://ipor.io">IPOR Labs AG</a>.

<table>
  <tr>
    <td><strong>Workflow</strong></td>
    <td>
        <a href="https://github.com/IPOR-Labs/ipor-fusion.py/actions/workflows/ci.yml">
            <img src="https://github.com/IPOR-Labs/ipor-fusion.py/actions/workflows/ci.yml/badge.svg" alt="CI">
        </a>
        <a href="https://github.com/IPOR-Labs/ipor-fusion.py/actions/workflows/cd.yml">
            <img src="https://github.com/IPOR-Labs/ipor-fusion.py/actions/workflows/cd.yml/badge.svg" alt="CD">
        </a>
    </td>
  </tr>
  <tr>
    <td><strong>Social</strong></td>
    <td>
        <a href="https://discord.com/invite/bSKzq6UMJ3">
            <img alt="Chat on Discord" src="https://img.shields.io/discord/832532271734587423?logo=discord&logoColor=white">
        </a>
        <a href="https://x.com/ipor_io">
            <img alt="X (formerly Twitter) URL" src="https://img.shields.io/twitter/url?url=https%3A%2F%2Fx.com%2Fipor_io&style=flat&logo=x&label=%40ipor_io&color=green">
        </a>
        <a href="https://t.me/IPOR_official_broadcast">
            <img alt="IPOR Official Broadcast" src="https://img.shields.io/badge/-t?logo=telegram&logoColor=white&logoSize=%3D&label=ipor">
        </a>
    </td>
  </tr>
  <tr>
    <td><strong>Code</strong></td>
    <td>
        <a href="https://pypi.org/project/ipor-fusion">
            <img alt="PyPI version" src="https://img.shields.io/pypi/v/ipor-fusion?color=blue">
        </a>
        <a href="https://github.com/IPOR-Labs/ipor-fusion.py/blob/main/LICENSE">
            <img alt="GitHub License" src="https://img.shields.io/github/license/IPOR-Labs/ipor-fusion?color=blue">
        </a>
        <a href="https://github.com/IPOR-Labs/ipor-fusion.py/blob/main/pyproject.toml">
            <img alt="Python Version" src="https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2FIPOR-Labs%2Fipor-fusion.py%2Frefs%2Fheads%2Fmain%2Fpyproject.toml">
        </a>
        <a href="https://github.com/IPOR-Labs/ipor-fusion.py/blob/main/pyproject.toml">
            <img alt="Code style: black" src="https://img.shields.io/badge/code%20style-black-000000.svg">
        </a>
    </td>
  </tr>
</table>

#### Install dependencies

```bash
poetry install
```

#### Setup ARBITRUM_PROVIDER_URL environment variable

Some node providers are not supported. It's working with QuickNode but not with Alchemy.

```bash
export ARBITRUM_PROVIDER_URL="https://..."
```

#### Run tests

```bash
poetry run pytest -v -s
```

#### Run pylint

```bash 
poetry run pylint --rcfile=pylintrc.toml --verbose --recursive=y .
```

#### Run black

```bash 
poetry run black ./
```

## Example of usage

```python
ALPHA_WALLET="0x..."

uniswap_v3_swap_fuse = UniswapV3SwapFuse(ARBITRUM.PILOT.V5.UNISWAP_V3_SWAP_FUSE)
ramses_v2_new_position_fuse = RamsesV2NewPositionFuse(ARBITRUM.PILOT.V5.RAMSES_V2_NEW_POSITION_FUSE)
ramses_claim_fuse = RamsesClaimFuse(ARBITRUM.PILOT.V5.RAMSES_CLAIM_FUSE)
plasma_vault = PlasmaVault(ARBITRUM.PILOT.V5.PLASMA_VAULT)
rewards_claim_manager = RewardsClaimManager(ARBITRUM.PILOT.V5.REWARDS_CLAIM_MANAGER)

swap = uniswap_v3_swap_fuse.swap(
    token_in_address=ARBITRUM.USDC,
    token_out_address=ARBITRUM.USDT,
    fee=100,
    token_in_amount=int(500e6),
    min_out_amount=0,
)

new_position = ramses_v2_new_position_fuse.new_position(
    token0=ARBITRUM.USDC,
    token1=ARBITRUM.USDT,
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

tx_result = plasma_vault.execute([swap, new_position])

(_, new_token_id, _, _, _, _, _, _, _, _,) = ramses_v2_new_position_fuse.extract_data_form_new_position_enter_event(
    tx_result
)

claim_action = ramses_claim_fuse.claim(
    token_ids=[new_token_id],
    token_rewards=[[ARBITRUM.RAMSES.V2.RAM, ARBITRUM.RAMSES.V2.X_REM]],
)

# some time later ... claim RAM rewards
rewards_claim_manager.claim_rewards([claim_action])

ram_after_claim = rewards_claim_manager.balance_of(ARBITRUM.RAMSES.V2.RAM)

assert ram_after_claim > 0

# Transfer REM to ALPHA wallet
rewards_claim_manager.transfer(
    ARBITRUM.RAMSES.V2.RAM, ALPHA_WALLET, ram_after_claim
)

ram_after_transfer = ram.balance_of(ALPHA_WALLET)
assert ram_after_transfer > 0

usdc_before_swap_ram = usdc.balance_of(ALPHA_WALLET)

# swap RAM -> USDC
path = [ARBITRUM.RAMSES.V2.RAM, 10000, ARBITRUM.WETH, 500, ARBITRUM.USDC]
uniswap_v3_universal_router.swap(ARBITRUM.RAMSES.V2.RAM, path, ram_after_transfer)

usdc_after_swap_ram = usdc.balance_of(ALPHA_WALLET)

rewards_in_usdc = usdc_after_swap_ram - usdc_before_swap_ram
assert rewards_in_usdc > 0

# Transfer USDC to rewards_claim_manager
usdc.transfer(to=ARBITRUM.PILOT.V5.REWARDS_CLAIM_MANAGER, amount=rewards_in_usdc)

usdc_after_transfer = usdc.balance_of(ALPHA_WALLET)
assert usdc_after_transfer == 0

# Update balance on rewards_claim_manager
rewards_claim_manager.update_balance()
rewards_claim_manager_balance_before_vesting = rewards_claim_manager.balance_of(
    ARBITRUM.USDC
)
assert rewards_claim_manager_balance_before_vesting > 0

rewards_claim_manager.update_balance()

# One month later
anvil.move_time(DAY)
rewards_claim_manager.update_balance()

rewards_claim_manager_balance_after_vesting = rewards_claim_manager.balance_of(
    ARBITRUM.USDC
)

assert rewards_claim_manager_balance_after_vesting == 0
```