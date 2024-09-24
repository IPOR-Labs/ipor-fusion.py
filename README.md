### IPOR Fusion python SDK

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
poetry run pytest
```