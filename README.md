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
        <a href="https://github.com/IPOR-Labs/ipor-fusion.py/actions/workflows/release.yml">
            <img src="https://github.com/IPOR-Labs/ipor-fusion.py/actions/workflows/release.yml/badge.svg" 
alt="Release">
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
        <a href="https://pypi.org/project/ipor-fusion/">
            <img alt="PyPI version" src="https://img.shields.io/pypi/v/ipor-fusion?color=blue">
        </a>
        <a href="https://github.com/IPOR-Labs/ipor-fusion.py/blob/main/LICENSE">
            <img alt="GitHub License" src="https://img.shields.io/github/license/IPOR-Labs/ipor-fusion?color=blue">
        </a>
        <a href="https://pypi.org/project/ipor-fusion/">
            <img alt="Python Version" src="https://img.shields.io/pypi/pyversions/ipor-fusion">
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

#### Setup environment variables

Copy the `.env.example` file to `.env` and fill in the required provider URLs:

```bash
cp .env.example .env
```

Then edit the `.env` file with your provider URLs for Ethereum, Arbitrum, and Base networks.


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

from ipor_fusion.PlasmaVaultSystemFactory import PlasmaVaultSystemFactory

# Variables
PROVIDER_URL = "https://arb-mainnet.g.alchemy.com/v2/XXXXXXXXXXXXXXXXXXXXXXXX"
PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
PLASMA_VAULT = "0x3F97CEa640B8B93472143f87a96d5A86f1F5167F"

# Setup PlasmaVault System
system = PlasmaVaultSystemFactory(
    PROVIDER_URL,
    PRIVATE_KEY,
).get(PLASMA_VAULT)

# Get swap fuse action
swap = system.uniswap_v3().swap(
    token_in_address=system.usdc().address(),
    token_out_address=system.usdt().address(),
    fee=100,
    token_in_amount=int(500e6),
    min_out_amount=0,
)

# Get new position fuse action
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

# Execute fuse actions on PlasmaVault in batch
tx_result = system.plasma_vault().execute([swap, new_position])


```
