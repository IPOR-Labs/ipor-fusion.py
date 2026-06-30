# pyright: reportAttributeAccessIssue=false
"""Unit tests for `fusion market morpho-blue` and the Morpho API client."""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from eth_abi import encode
from web3 import Web3

from ipor_fusion.cli import config_store
from ipor_fusion.cli.config_store import FusionConfig, save_config
from ipor_fusion.cli.main import cli
from ipor_fusion.cli.morpho_api import (
    MorphoApiError,
    MorphoApiMarket,
    VaultAllocation,
    VaultFlowCap,
    fetch_market,
    fetch_vault,
)

MARKET_ID = "ad656d430bb3d8c1469bf45c8ad4ebae1b04be04757c69fa424eec78d7b3f4dc"
LOAN_TOKEN = Web3.to_checksum_address("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
COLLATERAL_TOKEN = Web3.to_checksum_address(
    "0xd2af830e8cbdfed6cc11bab697bb25496ed6fa62"
)
ORACLE = Web3.to_checksum_address("0x7c65985c35181d51ef7571fa40211b57659b7d80")
IRM = Web3.to_checksum_address("0x870ac11d48b15db9a138cf899d20f13f79ba00bc")
VAULT_ADDR = Web3.to_checksum_address("0xf9bddd4a9b3a45f980e11fdde96e16364ddbec49")


def _market_raw(supply=1_008_277, borrow=908_170, fee=0):
    return encode(
        ["uint128", "uint128", "uint128", "uint128", "uint128", "uint128"],
        [supply, supply, borrow, borrow, 1700000000, fee],
    )


def _params_raw():
    return encode(
        ["address", "address", "address", "address", "uint256"],
        [LOAN_TOKEN, COLLATERAL_TOKEN, ORACLE, IRM, 915 * 10**15],
    )


def _rate_raw(rate_wad=10**9):
    return encode(["uint256"], [rate_wad])


PUBLIC_ALLOCATOR_ETH = "0xfd32fA2ca22c76dD6E550706Ad913FC6CE91c75D"
ADMIN_ADDR = "0x75a1253432356f90611546a487b5350CEF08780D"


def _api_market(with_flow_cap=True, role_granted=True):
    flow_cap = (
        VaultFlowCap(
            fee_wei=0,
            max_in=749_817_674_338,
            max_out=2_000_182_325_662,
            admin=ADMIN_ADDR,
        )
        if with_flow_cap
        else None
    )
    allocators = [ADMIN_ADDR]
    if role_granted:
        allocators = [PUBLIC_ALLOCATOR_ETH] + allocators
    vault = VaultAllocation(
        vault_address=VAULT_ADDR,
        vault_name="Yearn OG USDC",
        vault_symbol="ymvOG-USDC",
        asset_symbol="USDC",
        asset_decimals=6,
        total_assets=2_026_303_515_163,
        supply_assets=0,
        supply_cap=1_500_000_000_000,
        allocators=allocators,
        flow_cap=flow_cap,
    )
    return MorphoApiMarket(
        market_id="0x" + MARKET_ID,
        lltv=915 * 10**15,
        loan_token=LOAN_TOKEN,
        loan_symbol="USDC",
        loan_decimals=6,
        collateral_token=COLLATERAL_TOKEN,
        collateral_symbol="WOUSD",
        collateral_decimals=18,
        oracle=ORACLE,
        irm=IRM,
        supply_assets=1_008_277,
        borrow_assets=908_170,
        liquidity_assets=100_107,
        utilization=0.9007,
        supply_apy=0.0327,
        borrow_apy=0.0364,
        fee_wad=0,
        timestamp=1777532711,
        vaults=[vault],
    )


def _setup_provider(tmp_path, monkeypatch):
    config_dir = tmp_path / ".fusion"
    config_file = config_dir / "config.json"
    cache_dir = tmp_path / ".cache"
    monkeypatch.setattr(config_store, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_store, "CONFIG_FILE", config_file)
    monkeypatch.setattr(config_store, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(config_store, "CACHE_FILE", cache_dir / "contract_cache.json")
    monkeypatch.setattr(
        config_store,
        "DEPLOYMENT_CACHE_FILE",
        cache_dir / "deployment_cache.json",
    )
    save_config(
        FusionConfig(
            providers={"1": "https://rpc.example.com"},
            etherscan_api_key=None,
            vaults=[],
        )
    )


@patch("ipor_fusion.cli.market_cmd.fetch_market")
@patch("ipor_fusion.cli.market_cmd.Web3Context")
class TestMorphoBlueCommand:
    def test_renders_market_state_and_vaults(
        self, mock_ctx_cls, mock_fetch, tmp_path, monkeypatch
    ):
        _setup_provider(tmp_path, monkeypatch)
        ctx = MagicMock()
        # Command path: market_params() → market() → rates()
        # rates() internally reads market() + market_params() + IRM
        # market_params() → market() → IRM borrowRateView()
        ctx.call.side_effect = [
            _params_raw(),
            _market_raw(),
            _rate_raw(),
        ]
        mock_ctx_cls.from_url.return_value = ctx
        mock_fetch.return_value = _api_market()

        runner = CliRunner()
        result = runner.invoke(
            cli, ["market", "morpho-blue", MARKET_ID, "--chain", "ethereum"]
        )

        assert result.exit_code == 0, result.output
        assert "Morpho Blue market 0x" + MARKET_ID in result.output
        assert "ethereum (id 1)" in result.output
        assert LOAN_TOKEN in result.output
        assert COLLATERAL_TOKEN in result.output
        assert ORACLE in result.output
        assert IRM in result.output
        assert "91.50%" in result.output  # LLTV
        assert "Borrow APY" in result.output
        assert "Supply APY" in result.output
        # PublicAllocator singleton
        assert PUBLIC_ALLOCATOR_ETH in result.output
        # Connected vaults section
        assert "ymvOG-USDC" in result.output
        assert VAULT_ADDR in result.output
        assert "fee 0.000000 ETH" in result.output
        # Per-vault allocators block + role flag
        assert "Allocators for ymvOG-USDC" in result.output
        assert "PublicAllocator — public reallocate enabled" in result.output
        assert "PublicAllocator admin" in result.output
        # Reallocate hint
        assert "Reallocate parameters" in result.output

    def test_no_api_skips_vault_section(
        self, mock_ctx_cls, mock_fetch, tmp_path, monkeypatch
    ):
        _setup_provider(tmp_path, monkeypatch)
        ctx = MagicMock()
        # market_params() → market() → IRM borrowRateView()
        ctx.call.side_effect = [
            _params_raw(),
            _market_raw(),
            _rate_raw(),
        ]
        mock_ctx_cls.from_url.return_value = ctx

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["market", "morpho-blue", MARKET_ID, "--chain", "1", "--no-api"],
        )

        assert result.exit_code == 0, result.output
        assert mock_fetch.call_count == 0
        assert "skipped (--no-api)" in result.output

    def test_api_failure_renders_warning_but_succeeds(
        self, mock_ctx_cls, mock_fetch, tmp_path, monkeypatch
    ):
        _setup_provider(tmp_path, monkeypatch)
        ctx = MagicMock()
        # market_params() → market() → IRM borrowRateView()
        ctx.call.side_effect = [
            _params_raw(),
            _market_raw(),
            _rate_raw(),
        ]
        mock_ctx_cls.from_url.return_value = ctx
        mock_fetch.side_effect = MorphoApiError("network down")

        runner = CliRunner()
        result = runner.invoke(
            cli, ["market", "morpho-blue", MARKET_ID, "--chain", "1"]
        )

        assert result.exit_code == 0
        assert "unavailable (network down)" in result.output

    def test_json_output(self, mock_ctx_cls, mock_fetch, tmp_path, monkeypatch):
        _setup_provider(tmp_path, monkeypatch)
        ctx = MagicMock()
        # market_params() → market() → IRM borrowRateView()
        ctx.call.side_effect = [
            _params_raw(),
            _market_raw(),
            _rate_raw(),
        ]
        mock_ctx_cls.from_url.return_value = ctx
        mock_fetch.return_value = _api_market()

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["market", "morpho-blue", MARKET_ID, "--chain", "1", "--json"],
        )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["market_id"] == "0x" + MARKET_ID
        assert data["chain_id"] == 1
        assert data["market_params"]["loan_token"] == LOAN_TOKEN
        assert data["market_params"]["irm"] == IRM
        assert data["state"]["liquidity_assets"] == "100107"  # supply - borrow
        assert "borrow_apy" in data["rates"]
        assert data["public_allocator"] == PUBLIC_ALLOCATOR_ETH
        assert len(data["vaults"]) == 1
        vault_json = data["vaults"][0]
        assert vault_json["public_allocator_config"]["max_in"] == "749817674338"
        assert vault_json["public_allocator_config"]["admin"] == ADMIN_ADDR
        assert PUBLIC_ALLOCATOR_ETH in vault_json["allocators"]

    def test_invalid_market_id_rejected(
        self, mock_ctx_cls, mock_fetch, tmp_path, monkeypatch
    ):
        _setup_provider(tmp_path, monkeypatch)
        runner = CliRunner()
        result = runner.invoke(cli, ["market", "morpho-blue", "0xdead", "--chain", "1"])
        assert result.exit_code != 0
        assert "invalid Morpho market ID" in result.output


class TestMorphoApiClient:
    def test_fetch_market_parses_full_response(self):
        body = {
            "data": {
                "marketById": {
                    "marketId": "0x" + MARKET_ID,
                    "lltv": "915000000000000000",
                    "loanAsset": {
                        "address": LOAN_TOKEN,
                        "symbol": "USDC",
                        "decimals": 6,
                    },
                    "collateralAsset": {
                        "address": COLLATERAL_TOKEN,
                        "symbol": "WOUSD",
                        "decimals": 18,
                    },
                    "oracle": {"address": ORACLE},
                    "irmAddress": IRM,
                    "state": {
                        "supplyAssets": 1008277,
                        "borrowAssets": 908170,
                        "liquidityAssets": 100107,
                        "utilization": 0.9,
                        "supplyApy": 0.03,
                        "borrowApy": 0.036,
                        "fee": 0,
                        "timestamp": 1777532711,
                    },
                    "supplyingVaults": [
                        {
                            "address": VAULT_ADDR,
                            "symbol": "ymvOG-USDC",
                            "name": "Yearn OG USDC",
                            "asset": {"symbol": "USDC", "decimals": 6},
                            "state": {
                                "totalAssets": 2026303515163,
                                "allocation": [
                                    {
                                        "market": {"marketId": "0x" + MARKET_ID},
                                        "supplyAssets": 0,
                                        "supplyCap": 1500000000000,
                                    },
                                    {
                                        "market": {"marketId": "0xdeadbeef"},
                                        "supplyAssets": 99,
                                        "supplyCap": 0,
                                    },
                                ],
                            },
                            "allocators": [
                                {"address": PUBLIC_ALLOCATOR_ETH},
                                {"address": ADMIN_ADDR},
                            ],
                            "publicAllocatorConfig": {
                                "fee": 0,
                                "admin": ADMIN_ADDR,
                                "flowCaps": [
                                    {
                                        "market": {"marketId": "0x" + MARKET_ID},
                                        "maxIn": 749817674338,
                                        "maxOut": 2000182325662,
                                    },
                                ],
                            },
                        },
                    ],
                }
            }
        }
        with patch("ipor_fusion.cli.morpho_api.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(body).encode()
            mock_open.return_value.__enter__.return_value = mock_resp

            result = fetch_market(MARKET_ID, 1)

        assert result.market_id == "0x" + MARKET_ID
        assert result.lltv == 915 * 10**15
        assert result.loan_symbol == "USDC"
        assert len(result.vaults) == 1
        vault = result.vaults[0]
        assert vault.vault_symbol == "ymvOG-USDC"
        # Picks the allocation matching this market, not the deadbeef one
        assert vault.supply_assets == 0
        assert vault.supply_cap == 1500000000000
        assert vault.flow_cap is not None
        assert vault.flow_cap.max_in == 749817674338
        assert vault.flow_cap.fee_wei == 0
        assert vault.flow_cap.admin == ADMIN_ADDR
        assert vault.allocators == [PUBLIC_ALLOCATOR_ETH, ADMIN_ADDR]

    def test_fetch_market_handles_missing_market(self):
        with patch("ipor_fusion.cli.morpho_api.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(
                {"data": {"marketById": None}}
            ).encode()
            mock_open.return_value.__enter__.return_value = mock_resp

            try:
                fetch_market(MARKET_ID, 1)
            except MorphoApiError as exc:
                assert "not found" in str(exc)
            else:
                raise AssertionError("expected MorphoApiError")

    def test_fetch_market_propagates_graphql_errors(self):
        with patch("ipor_fusion.cli.morpho_api.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(
                {"errors": [{"message": "bad query"}]}
            ).encode()
            mock_open.return_value.__enter__.return_value = mock_resp

            try:
                fetch_market(MARKET_ID, 1)
            except MorphoApiError as exc:
                assert "bad query" in str(exc)
            else:
                raise AssertionError("expected MorphoApiError")

    def test_fetch_market_handles_vault_without_public_allocator(self):
        body = {
            "data": {
                "marketById": {
                    "marketId": "0x" + MARKET_ID,
                    "lltv": "0",
                    "loanAsset": {
                        "address": LOAN_TOKEN,
                        "symbol": "USDC",
                        "decimals": 6,
                    },
                    "collateralAsset": {
                        "address": COLLATERAL_TOKEN,
                        "symbol": "X",
                        "decimals": 18,
                    },
                    "oracle": {"address": ORACLE},
                    "irmAddress": IRM,
                    "state": None,
                    "supplyingVaults": [
                        {
                            "address": VAULT_ADDR,
                            "symbol": "v",
                            "name": "v",
                            "asset": {"symbol": "USDC", "decimals": 6},
                            "state": {"totalAssets": 0, "allocation": []},
                            "publicAllocatorConfig": None,
                        }
                    ],
                }
            }
        }
        with patch("ipor_fusion.cli.morpho_api.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(body).encode()
            mock_open.return_value.__enter__.return_value = mock_resp

            result = fetch_market(MARKET_ID, 1)

        assert result.vaults[0].flow_cap is None
        assert result.vaults[0].supply_assets == 0


# ── meta-morpho subcommand + fetch_vault ──────────────────────────────


def _v2_id_data_for(loan, collateral, oracle, irm, lltv):
    """Encode an idData payload as Vault V2 does for MarketV1 caps."""
    return (
        "0x"
        + encode(
            [
                "string",
                "address",
                "address",
                "address",
                "address",
                "address",
                "uint256",
            ],
            ["this/marketParams", loan, loan, collateral, oracle, irm, lltv],
        ).hex()
    )


def _v2_response():
    return {
        "data": {
            "vaultV2ByAddress": {
                "address": "0xB885F6d448dA7E2C642Ec31190B629E40E87B069",
                "name": "Yearn OG USDC",
                "symbol": "yOG-USDC-V2",
                "asset": {"address": LOAN_TOKEN, "symbol": "USDC", "decimals": 6},
                "totalAssets": 857_498_945_341,
                "idleAssets": 0,
                "liquidity": 844_616_692_135,
                "sharePrice": 1.018620,
                "performanceFee": 0,
                "performanceFeeRecipient": "0x" + "0" * 40,
                "managementFee": 0,
                "managementFeeRecipient": "0x" + "0" * 40,
                "maxApy": 1.71,
                "owner": {"address": "0x" + "1" * 40},
                "curator": {"address": "0x" + "2" * 40},
                "allocators": [{"allocator": {"address": "0x" + "3" * 40}}],
                "sentinels": [],
                "liquidityAdapter": {
                    "__typename": "MorphoMarketV1Adapter",
                    "address": "0x" + "5" * 40,
                    "type": "MorphoMarketV1",
                    "assets": 100,
                },
                "adapters": {
                    "items": [
                        {
                            "__typename": "MorphoMarketV1Adapter",
                            "address": "0x" + "5" * 40,
                            "type": "MorphoMarketV1",
                            "assets": 100,
                        }
                    ]
                },
                "caps": {
                    "items": [
                        {
                            "id": "0xcap0",
                            "idData": _v2_id_data_for(
                                LOAN_TOKEN,
                                COLLATERAL_TOKEN,
                                ORACLE,
                                IRM,
                                915 * 10**15,
                            ),
                            "type": "MarketV1",
                            "absoluteCap": 1_500_000_000_000,
                            "relativeCap": "1000000000000000000",
                            "allocation": 57,
                        },
                        {
                            "id": "0xcap1",
                            "idData": "0x",
                            "type": "Adapter",
                            "absoluteCap": 1000,
                            "relativeCap": "1000000000000000000",
                            "allocation": 0,
                        },
                    ]
                },
            }
        }
    }


class TestMetaMorphoCommand:
    def test_renders_v2_vault(self, tmp_path, monkeypatch):
        _setup_provider(tmp_path, monkeypatch)
        body = _v2_response()
        with patch("ipor_fusion.cli.morpho_api.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(body).encode()
            mock_open.return_value.__enter__.return_value = mock_resp

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "market",
                    "meta-morpho",
                    "0xB885F6d448dA7E2C642Ec31190B629E40E87B069",
                    "--chain",
                    "1",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "Morpho Vault V2 — Yearn OG USDC" in result.output
        assert "Curator:" in result.output
        assert "MorphoMarketV1" in result.output  # adapter type
        assert "Morpho Blue market caps (1):" in result.output
        # Decoded MarketParams
        assert LOAN_TOKEN in result.output
        assert COLLATERAL_TOKEN in result.output
        assert "915000000000000000" in result.output  # lltv

    def test_v2_market_filter_keeps_only_match(self, tmp_path, monkeypatch):
        _setup_provider(tmp_path, monkeypatch)
        body = _v2_response()
        with patch("ipor_fusion.cli.morpho_api.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(body).encode()
            mock_open.return_value.__enter__.return_value = mock_resp

            v = fetch_vault("0xB885F6d448dA7E2C642Ec31190B629E40E87B069", 1)

        market_caps = [c for c in v.caps if c.cap_type == "MarketV1"]
        assert len(market_caps) == 1
        cap = market_caps[0]
        assert cap.loan_token == LOAN_TOKEN
        assert cap.collateral_token == COLLATERAL_TOKEN
        assert cap.lltv == 915 * 10**15
        assert cap.market_id is not None
        assert cap.market_id.startswith("0x")
        assert cap.absolute_cap == 1_500_000_000_000
        assert cap.allocation == 57
        assert cap.room == 1_500_000_000_000 - 57

    def test_renders_v1_vault_when_v2_not_found(self, tmp_path, monkeypatch):
        _setup_provider(tmp_path, monkeypatch)
        v2_not_found = {
            "data": {"vaultV2ByAddress": None},
            "errors": [{"status": "NOT_FOUND"}],
        }
        v1_body = {
            "data": {
                "vaultByAddress": {
                    "address": VAULT_ADDR,
                    "name": "Test V1 Vault",
                    "symbol": "tv1",
                    "asset": {"address": LOAN_TOKEN, "symbol": "USDC", "decimals": 6},
                    "state": {
                        "totalAssets": 1_000_000_000,
                        "fee": 10**17,
                        "owner": "0x" + "1" * 40,
                        "curator": "0x" + "2" * 40,
                        "guardian": "0x" + "3" * 40,
                        "feeRecipient": "0x" + "4" * 40,
                        "allocation": [
                            {
                                "market": {
                                    "marketId": "0x" + MARKET_ID,
                                    "lltv": "915000000000000000",
                                    "loanAsset": {"symbol": "USDC", "decimals": 6},
                                    "collateralAsset": {"symbol": "WOUSD"},
                                    "state": {
                                        "supplyAssets": 1000,
                                        "borrowAssets": 500,
                                        "supplyApy": 0.03,
                                    },
                                },
                                "supplyAssets": 0,
                                "supplyCap": 1_500_000_000_000,
                            }
                        ],
                    },
                    "allocators": [{"address": PUBLIC_ALLOCATOR_ETH}],
                    "publicAllocatorConfig": {
                        "fee": 0,
                        "admin": ADMIN_ADDR,
                        "flowCaps": [
                            {
                                "market": {"marketId": "0x" + MARKET_ID},
                                "maxIn": 750_000_000_000,
                                "maxOut": 0,
                            }
                        ],
                    },
                }
            }
        }
        responses = iter([v2_not_found, v1_body])
        with patch("ipor_fusion.cli.morpho_api.urlopen") as mock_open:

            def side_effect(*_args, **_kwargs):
                resp = MagicMock()
                resp.read.return_value = json.dumps(next(responses)).encode()
                ctx_mgr = MagicMock()
                ctx_mgr.__enter__.return_value = resp
                return ctx_mgr

            mock_open.side_effect = side_effect
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["market", "meta-morpho", VAULT_ADDR, "--chain", "1"],
            )

        assert result.exit_code == 0, result.output
        assert "MetaMorpho V1 — Test V1 Vault" in result.output
        assert "[PublicAllocator]" in result.output
        assert "Status:           enabled" in result.output

    def test_unknown_vault_errors(self, tmp_path, monkeypatch):
        _setup_provider(tmp_path, monkeypatch)
        not_found = {
            "data": {"vaultV2ByAddress": None},
            "errors": [{"status": "NOT_FOUND"}],
        }
        v1_not_found = {
            "data": {"vaultByAddress": None},
            "errors": [{"status": "NOT_FOUND"}],
        }
        responses = iter([not_found, v1_not_found])
        with patch("ipor_fusion.cli.morpho_api.urlopen") as mock_open:

            def side_effect(*_args, **_kwargs):
                resp = MagicMock()
                resp.read.return_value = json.dumps(next(responses)).encode()
                ctx_mgr = MagicMock()
                ctx_mgr.__enter__.return_value = resp
                return ctx_mgr

            mock_open.side_effect = side_effect
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["market", "meta-morpho", VAULT_ADDR, "--chain", "1"],
            )

        assert result.exit_code != 0
        assert "not found" in result.output

    def test_json_output_v2(self, tmp_path, monkeypatch):
        _setup_provider(tmp_path, monkeypatch)
        body = _v2_response()
        with patch("ipor_fusion.cli.morpho_api.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(body).encode()
            mock_open.return_value.__enter__.return_value = mock_resp

            runner = CliRunner()
            result = runner.invoke(
                cli,
                [
                    "market",
                    "meta-morpho",
                    "0xB885F6d448dA7E2C642Ec31190B629E40E87B069",
                    "--chain",
                    "1",
                    "--json",
                ],
            )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["version"] == "v2"
        assert data["name"] == "Yearn OG USDC"
        market_caps = [c for c in data["caps"] if c["type"] == "MarketV1"]
        assert len(market_caps) == 1
        assert market_caps[0]["loan_token"] == LOAN_TOKEN
        assert market_caps[0]["lltv"] == str(915 * 10**15)
