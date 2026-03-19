<p align="center">
    <img height="80px" width="80px" src="https://ipor.io/images/ipor-fusion.svg" alt="IPOR Fusion Python SDK"/>
    <h1 align="center">IPOR Fusion Python SDK</h1>
</p>

`ipor_fusion` is the official Python SDK for **IPOR Fusion Plasma Vaults** — typed abstractions for DeFi protocol interactions on EVM chains through a fuse adapter pattern.

Maintained by <a href="https://ipor.io">IPOR Labs AG</a>.

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

## Quickstart

### Install

```bash
pip install ipor-fusion
```

### Connect and execute

```python
from ipor_fusion import Web3Context, PlasmaVault, AaveV3SupplyFuse
from web3 import Web3

# 1. Create a Web3 context with your provider and private key
ctx = Web3Context.from_url(
    url="https://arb-mainnet.g.alchemy.com/v2/YOUR_KEY",
    private_key="0x...",
)

# 2. Wrap the PlasmaVault contract
vault = PlasmaVault(ctx, Web3.to_checksum_address("0xVAULT_ADDRESS"))

# 3. Build a fuse action (e.g. supply USDC to Aave V3)
fuse = AaveV3SupplyFuse(Web3.to_checksum_address("0xFUSE_ADDRESS"))
action = fuse.supply(
    Web3.to_checksum_address("0xUSDC_ADDRESS"),
    amount=1_000_000,  # 1 USDC (6 decimals)
)

# 4. Execute on-chain
receipt = vault.execute([action])
```

## Architecture

The SDK uses a **fuse adapter pattern**:

- **Fuses** encode protocol-specific calls into `FuseAction` objects (pure calldata, no state)
- **PlasmaVault** batches and executes `FuseAction` sequences on-chain via `execute()`
- **Web3Context** manages provider connections, signing, and transaction dispatch

```
Fuse.method()  -->  FuseAction  -->  PlasmaVault.execute([actions])  -->  on-chain tx
```

### Core modules (`ipor_fusion.core`)

| Module | Purpose |
|--------|---------|
| `Web3Context` | Provider connection, signing, tx dispatch |
| `PlasmaVault` | ERC-4626 vault — execute, deposit, withdraw |
| `AccessManager` | Role-based access control |
| `RewardsManager` | Claim and vest rewards |
| `WithdrawManager` | Time-windowed withdrawal requests |
| `PriceOracleMiddleware` | Asset price feeds |

### Supported protocols (`ipor_fusion.fuses`)

| Protocol | Fuses |
|----------|-------|
| Aave V3 | `AaveV3SupplyFuse`, `AaveV3BorrowFuse` |
| Morpho | `MorphoSupplyFuse`, `MorphoCollateralFuse`, `MorphoBorrowFuse`, `MorphoFlashLoanFuse`, `MorphoClaimFuse` |
| Uniswap V3 | `UniswapV3SwapFuse`, `UniswapV3NewPositionFuse`, `UniswapV3ModifyPositionFuse`, `UniswapV3CollectFuse` |
| Ramses V2 | `RamsesV2NewPositionFuse`, `RamsesV2ModifyPositionFuse`, `RamsesV2CollectFuse`, `RamsesClaimFuse` |
| Compound V3 | `CompoundV3SupplyFuse` |
| Gearbox V3 | `GearboxSupplyFuse`, `GearboxStakeFuse` |
| ERC-4626 | `ERC4626SupplyFuse` |
| Fluid Instadapp | `FluidInstadappSupplyFuse`, `FluidInstadappStakingFuse` |
| Universal | `UniversalTokenSwapperFuse` |

### Supported networks

- **Ethereum** mainnet
- **Arbitrum** One
- **Base**

## Development

```bash
poetry install                                              # Install dependencies
poetry run pytest tests/test_fuse_encoding.py -n auto -v    # Unit tests (fast, no Docker)
poetry run pytest -v -s                                     # All tests (needs Docker + .env)
poetry run black ./                                         # Format
poetry run pylint --rcfile=pylintrc.toml --verbose --recursive=y .  # Lint
poetry run mypy .                                           # Type check
```

Integration tests require Docker (Anvil) and provider URLs in `.env`:

```bash
cp .env.example .env
# Edit .env with ARBITRUM_PROVIDER_URL, ETHEREUM_PROVIDER_URL, BASE_PROVIDER_URL
```

## Examples

For full usage patterns, see the example repository: [ipor-fusion-alpha-example](https://github.com/IPOR-Labs/ipor-fusion-alpha-example)
