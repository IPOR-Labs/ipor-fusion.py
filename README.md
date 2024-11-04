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
import time
from web3 import Web3
from ipor_fusion.contracts import (
    UniswapV3SwapFuse,
    UniswapV3NewPositionFuse,
    PlasmaVault,
    ERC20,
    TransactionExecutor,
)
from ipor_fusion.constants import ARBITRUM

# Initialize Web3 and account
web3 = Web3(Web3.HTTPProvider("YOUR_NODE_URL"))
account = "YOUR_PRIVATE_KEY"  # Or use constants.ANVIL_WALLET_PRIVATE_KEY for testing

# Initialize transaction executor
transaction_executor = TransactionExecutor(web3, account)

# Initialize contracts
uniswap_v3_swap_fuse = UniswapV3SwapFuse(transaction_executor, ARBITRUM.PILOT.V4.UNISWAP_V3_SWAP_FUSE)
uniswap_v3_new_position_fuse = UniswapV3NewPositionFuse(transaction_executor, ARBITRUM.PILOT.V4.UNISWAP_V3_NEW_POSITION_FUSE)
plasma_vault = PlasmaVault(transaction_executor, ARBITRUM.PILOT.V4.PLASMA_VAULT)
usdc = ERC20(transaction_executor, ARBITRUM.USDC)
usdt = ERC20(transaction_executor, ARBITRUM.USDT)

# Swap USDC to USDT
swap = uniswap_v3_swap_fuse.swap(
    token_in_address=ARBITRUM.USDC,
    token_out_address=ARBITRUM.USDT,
    fee=100,
    token_in_amount=int(500e6),
    min_out_amount=0,
)
plasma_vault.execute([swap])

# Check balances after swap
vault_usdc_balance_after_swap = usdc.balance_of(ARBITRUM.PILOT.V4.PLASMA_VAULT)
vault_usdt_balance_after_swap = usdt.balance_of(ARBITRUM.PILOT.V4.PLASMA_VAULT)

# Create a new position with specified parameters
new_position = uniswap_v3_new_position_fuse.new_position(
    token0=ARBITRUM.USDC,
    token1=ARBITRUM.USDT,
    fee=100,
    tick_lower=-100,
    tick_upper=101,
    amount0_desired=int(499e6),
    amount1_desired=int(499e6),
    amount0_min=0,
    amount1_min=0,
    deadline=int(time.time()) + 100,
)

# Execute the creation of the new position
plasma_vault.execute([new_position])

# Check balances after opening the new position
vault_usdc_balance_after_new_position = usdc.balance_of(
    ARBITRUM.PILOT.V4.PLASMA_VAULT
)
vault_usdt_balance_after_new_position = usdt.balance_of(
    ARBITRUM.PILOT.V4.PLASMA_VAULT
)

# Assert on balance changes after creating the new position
usdc_change = vault_usdc_balance_after_new_position - vault_usdc_balance_after_swap
usdt_change = vault_usdt_balance_after_new_position - vault_usdt_balance_after_swap
```