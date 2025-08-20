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

## CLI Usage

The IPOR Fusion CLI provides commands for managing configuration files and interacting with Plasma Vaults.

### Initializing Configuration

Create a new configuration file:

```bash
# Basic initialization
fusion config init

# With encryption for private key
fusion config init --encrypt-private-key
```

### Managing Encrypted Private Keys

The CLI supports encryption of private keys for enhanced security:

```bash
# Encrypt an existing private key in config
fusion encrypt

# Show configuration (displays encryption status)
fusion show
```

#### Encryption Features

- **PBKDF2-HMAC-SHA256** key derivation with 100,000 iterations
- **Fernet (AES-128-CBC)** encryption
- **Salt-based** encryption for additional security
- **Base64url** encoding for safe storage in YAML

#### Security Best Practices

1. **Always use encryption** for production environments
2. **Store passwords securely** - never commit them to version control
3. **Use strong passwords** with mixed case, numbers, and symbols
4. **Regularly rotate** encryption passwords
5. **Backup encrypted keys** safely

### Configuration File Format

The CLI uses YAML configuration files (`ipor-fusion-config.yaml`):

```yaml
plasma_vault_address: "0x1234567890123456789012345678901234567890"
provider_url: "https://example.com/rpc"
private_key: "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"  # or encrypted
network: "mainnet"
gas_limit: "300000"
gas_price: null
max_priority_fee: null
custom_field: ""
description: ""
notes: ""
```

## Example of usage
For example of usage patterns, check out our example repository at: [https://github.com/IPOR-Labs/ipor-fusion-alpha-example](https://github.com/IPOR-Labs/ipor-fusion-alpha-example)
