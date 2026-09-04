# AGENTS.md

`ipor-fusion` is the Python SDK for IPOR Fusion Plasma Vaults: typed fuse
encoders, on-chain readers, and a `fusion` CLI plus `fusion-mcp` MCP server
built on the same SDK. Published to PyPI. It is a library and inspection
tooling, not an automation service: nothing here runs on a schedule.

Related repositories (siblings, referenced by name; clone paths vary):
- `ipor-fusion` — Solidity contracts, the source of truth for market ids, roles and substrates.
- [ipor-fusion-alpha-example](https://github.com/IPOR-Labs/ipor-fusion-alpha-example) — end-to-end SDK usage patterns.

## Commands

```bash
uv sync --all-extras --locked                                 # install, same as CI
uv run ruff format ./                                         # format (CI runs --check)
uv run ruff check ./                                          # lint: bandit S, C901 max-complexity 10, PLR limits
uv run pyright                                                # types, basic mode, src + tests
uv run pytest                                                 # all tests + coverage gate (fail_under = 95)
uv run pytest -m "cli or mcp" --no-cov                        # fast offline subset
uv run pytest tests/test_fuse_encoding.py -k aave --no-cov    # single file / test
uv lock --check                                               # uv.lock in sync with pyproject.toml
```

Run format, check, pyright and pytest after every code change; that is exactly
the sequence in `.github/workflows/python-build.yml`. When ruff `C901` trips,
extract helpers, never suppress. Poetry, black, pylint and mypy are gone.

**Offline vs network.** `test_cli_*` and `test_mcp_*` need no network
(auto-tagged `cli` / `mcp` in `conftest.py`; everything else is `sdk`).
Integration tests (`test_simulate_*`, `test_vault_info_onchain_*`) run against
live RPCs via `eth_simulateV1`, no Docker or Anvil (`ANVIL_WALLET` is just a
named address constant). They auto-skip unless `ETHEREUM_PROVIDER_URL`,
`BASE_PROVIDER_URL` and `ARBITRUM_PROVIDER_URL` are set (`.env` is loaded via
python-dotenv) and the provider supports `eth_simulateV1`. CI has all three as
secrets. Never print `.env` or a provider URL: they embed API keys.

## Conventions

- Commits: conventional (angular) subjects with a scope, as in `git log` —
  `feat(sdk):`, `fix(sdk):`, `refactor(sdk):`, `feat(cli):`, `feat(mcp):`,
  `test(sdk):`, `ci:`, `build:`, `chore:`, `docs:`. `python-semantic-release`
  derives the version from them. Never hand-bump `project.version`; the
  release workflow stamps it and re-locks `uv.lock`.
- No AI attribution trailers or "generated with" lines in commits or PRs.
- Branch and open a PR; `main` only moves through PRs and the release workflow.
- Dependencies: `>=x,<next-major` ranges in `pyproject.toml`, resolved by
  `uv.lock`; `pyright` is pinned exactly.
- English only in code. Comments only for edge cases and non-obvious logic.

## Invariants that must change together

| Value | Where it lives |
|---|---|
| ruff version | `uv.lock` dev group and `rev` in `.pre-commit-config.yaml` |
| Python 3.12 runtime, 3.10 floor | `.python-version` + CI `python-version` default; `requires-python`, ruff `target-version`, pyright `pythonVersion` |
| `IporFusionMarkets`, `Roles` | `market_ids.py`, `config/roles.py` mirror `IporFusionMarkets.sol`, `Roles.sol` in `ipor-fusion/contracts/libraries/` |
| substrate decoders | `substrates.py` registry mirrors each market's `contracts/fuses/<protocol>/*SubstrateLib.sol` or `*FuseLib.sol` |
| `vault_info` JSON shape | `_build_json_output` in `cli/vault_cmd.py`, models in `mcp/models.py` (`extra="forbid"`), `_full_vault_info_dict` fixture in `test_mcp_models.py` |
| CLI command set | every CLI command has a matching tool in `mcp/server.py` |

## Layout (`src/ipor_fusion/`)

- `chains.py` chain registry; `market_ids.py` `IporFusionMarkets`; `types.py`, `errors.py`
- `substrates.py` — public per-market bytes32 substrate decoding (`decode_substrate`,
  `SubstrateInfo`, `format_market_label`, `market_name`; re-exported from `ipor_fusion`),
  used by the CLI, MCP and `readers/lending_health`
- `config/roles.py` — `Roles` IntEnum
- `core/` — `plasma_vault`, `access`, `withdraw_manager`, `rewards_manager`,
  `simulation` (eth_simulateV1), `oracle`, `fusion_factory`, `context`, `contract`, `erc20`
- `fuses/` — per-protocol fuse encoders (aave_v3, compound_v3, erc4626, euler_v2,
  fluid_instadapp, gearbox_v3, merkl, morpho, ramses_v2, uniswap_v3, universal, `events.py`)
- `readers/` — read side: lending_health, oracle_mapping, position_manager, aave_v3,
  compound_v3, morpho, ramses_v2, uniswap_v3
- `cli/` — `main.py` root group; `config_cmd.py`, `market_cmd.py` (+ `morpho_api.py`),
  `vault_cmd.py` orchestration; `vault_fetcher.py` on-chain fetch (`_fetch_vault_data`,
  `_safe_call`); `vault_health.py` checks + reconciliation; `vault_rendering.py` pure
  formatting; `vault_dep_graph.py`; `config_store.py` (XDG `~/.config/ipor-fusion/`,
  `~/.cache/ipor-fusion/`); `explorer.py` Etherscan V2 (single endpoint, needs API key)
- `mcp/` — `server.py` tools call the SDK directly (no subprocess); `models.py` pydantic output
- Entry points: `entry_cli.py`, `entry_mcp.py` via `[project.scripts]`

## Substrate decoders: source of truth

Sync `market_ids.py`, `config/roles.py` and the `substrates.py` registry from
the Solidity libraries above; add missing entries and carry over the substrate
type comments. Every new market id gets a decoder registration in the same
change, sourced from its Solidity substrate lib, never assumed. Every typed
decoder sets a `type_label` (enum name or a protocol tag like `EULER_VAULT`);
a label-less decode renders as a generic placeholder in every consumer.
Plain-address decoding only when the fuses check `isSubstrateAsAssetGranted`;
typed layouts get their own decoder with the enum labels carried over. A
market with no substrate semantics stays unregistered ON PURPOSE and renders
as a loud `no_decoder(NAME)` (e.g. LITE_PSM=48). Never default to
plain-address: markets 40/46/49 once decoded silently to garbage addresses
that way, and downstream consumers alert off these decodes, so a wrong decoder
is worse than none. `decode_substrate` handles plain addresses (12 zero bytes)
and typed substrates (11 zero bytes + type byte, e.g. Ebisu ZAPPER/REGISTRY).

## CLI and MCP

`fusion config` (set-provider, set-etherscan-key, show), `fusion market`
(morpho-blue, meta-morpho), `fusion vault` (add, remove, list, info,
role-accounts, oracle-mapping). `vault info` fans out RPC and API calls with a
`ThreadPoolExecutor`; contract names and token symbols are cached in
`~/.cache/ipor-fusion/contract_cache.json`.

MCP tools are 1:1 with CLI commands. Read-only: `vault_info`, `vault_list`,
`vault_role_accounts`, `vault_oracle_mapping`, `market_morpho_blue`,
`market_meta_morpho`, `config_show`, `server_info`. Mutating: `vault_add`,
`vault_remove`, `config_set_provider`, `config_set_etherscan_key`. Every new
CLI command gets a matching MCP tool. Tool docstrings must list all returned
JSON fields so an agent knows what is available without calling the tool.

`mcp/models.py` forbids extra keys. Adding a top-level field to
`_build_json_output` requires adding it to the matching model (with a default
for back-compat) and to the `_full_vault_info_dict` fixture; `test_mcp_models.py`
enforces the mirror. Conditionally shaped blocks (`withdraw_manager_details`,
`substrates`, `dependency_graph`) are `dict[str, Any]`, so their inner keys
are not validated.

## Domain rules (verified against the contracts)

- `_fetch_vault_data(..., chain_id=0)` (the default) skips lending_health,
  Morpho/Aave position breakdowns and token prices, all guarded by `if chain_id:`.
  Pass a real chain_id or those come back null/empty, silently.
- PlasmaVault serves governance/view functions via `fallback()` delegatecall to
  PlasmaVaultBase, so Etherscan's ABI for the vault address omits callable
  view functions such as `getDependencyBalanceGraph`. An empty dependency graph
  means "unconfigured on-chain", not "missing function".
- WithdrawManager: `request_fee` (scheduled exit) and `withdraw_fee` (instant
  exit) are mutually exclusive paths, never summed. `redeemFromRequest` charges
  no fee; the request already paid `request_fee`.
- The `ERC20_VAULT_BALANCE` dependency edge is required only for markets that
  touch non-underlying idle tokens (ERC20_VAULT_BALANCE substrates minus
  underlying). Single-asset optimizers legitimately omit it; idle underlying is
  already in `totalAssets` via ERC4626 base accounting.
- `ZeroBalanceFuse` markets are capabilities (swap, flash-loan,
  instant-withdrawal, admin) with structurally zero balance, not venues. They
  go in `zero_balance_fuses` and are not counted as liquidity markets.
- Reconciliation: `totalAssets = balance_fuses_sum + underlying_on_vault`.
  Balance fuses already price non-underlying ERC20s (e.g. ERC20BalanceFuse
  values BOLD + BOLDUSDC-gauge in WETH terms). Never add all ERC20 values on
  top of balance fuse totals; that double-counts. `erc20_direct_total` in JSON
  output is informational only.
