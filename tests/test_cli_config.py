import os

import yaml
from click.testing import CliRunner

from ipor_fusion.cli.commands.config import show, init, update


class TestInitCommand:
    """Test the init command"""

    PLASMA_VAULT_ADDRESS = "0x6f66b845604dad6E80b2A1472e6cAcbbE66A8C40"
    PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

    def setup_method(self):
        """Set up test fixtures"""
        self.runner = CliRunner()
        self.rpc_url = os.getenv("ETHEREUM_PROVIDER_URL")

    def test_init_with_prompts(self, tmp_path):
        """Test init command with interactive prompts"""
        inputs = [
            self.rpc_url,
            self.PLASMA_VAULT_ADDRESS,
            "y",  # name
            "y",  # set a private key
            self.PRIVATE_KEY,
            self.PRIVATE_KEY,
            "n",  # encrypt private key
        ]

        config_path = tmp_path / "ipor-fusion-config.yaml"

        result = self.runner.invoke(
            init, input="\n".join(inputs), args=["--config-file", str(config_path)]
        )
        assert result.exit_code == 0
        assert "Configuration file created" in result.output

        assert config_path.exists()

        content = config_path.read_text()
        config_data = yaml.safe_load(content)

        assert config_data["chain_configs"][0]["rpc_url"] == self.rpc_url
        assert (
            config_data["chain_configs"][0]["plasma_vaults"][0]["plasma_vault_address"]
            == self.PLASMA_VAULT_ADDRESS
        )
        assert (
            config_data["chain_configs"][0]["plasma_vaults"][0]["private_key"]
            == self.PRIVATE_KEY
        )

    def test_init_with_prompts_overwrite_config(self, tmp_path):
        self.test_init_with_prompts(tmp_path)
        inputs = [
            self.rpc_url,
            self.PLASMA_VAULT_ADDRESS,
            "y",  # use the default vault name
            "n",  # set a private key
            "y",  # overwrite
        ]

        config_path = tmp_path / "ipor-fusion-config.yaml"

        result = self.runner.invoke(
            init, input="\n".join(inputs), args=["--config-file", str(config_path)]
        )
        assert result.exit_code == 0
        assert "Configuration file already exists at" in result.output

    def test_config_show(self, tmp_path):
        self.test_init_with_prompts(tmp_path)

        config_path = tmp_path / "ipor-fusion-config.yaml"

        result = self.runner.invoke(show, args=["--config-file", str(config_path)])
        assert result.exit_code == 0

    def test_config_update(self, tmp_path):
        """Test init command with interactive prompts"""
        inputs = [
            self.rpc_url,
            self.PLASMA_VAULT_ADDRESS,
            "y",  # name
            "n",  # set a private key
        ]

        config_path = tmp_path / "ipor-fusion-config.yaml"

        result = self.runner.invoke(
            init, input="\n".join(inputs), args=["--config-file", str(config_path)]
        )
        assert result.exit_code == 0
        assert "Configuration file created" in result.output

        config_path = tmp_path / "ipor-fusion-config.yaml"

        result = self.runner.invoke(update, args=["--config-file", str(config_path)])
        assert result.exit_code == 0

    def test_init_with_prompts_priv_key_not_match(self, tmp_path):
        """Test init command with interactive prompts"""
        inputs = [
            self.rpc_url,
            self.PLASMA_VAULT_ADDRESS,
            "y",  # name
            "y",  # set a private key
            self.PRIVATE_KEY,
            "wrong_private_key",
        ]

        config_path = tmp_path / "ipor-fusion-config.yaml"

        result = self.runner.invoke(
            init, input="\n".join(inputs), args=["--config-file", str(config_path)]
        )
        assert result.exit_code == 1
        assert "Private keys do not match!" in result.output

    def test_init_invalid_rpc_url_schema(self):
        """Test init command with interactive prompts"""
        inputs = [
            self.rpc_url.replace("https", "invalid_protocol"),
            self.PLASMA_VAULT_ADDRESS,
        ]

        result = self.runner.invoke(init, input="\n".join(inputs))
        assert result.exit_code == 1
        assert "Invalid RPC URL!" in result.output

    def test_init_provider_does_not_respond_correctly(self):
        """Test init command with interactive prompts"""
        inputs = [
            "https://eth-mainnet.g.alchemy.com/v2/invalid_access_token",
            self.PLASMA_VAULT_ADDRESS,
        ]

        result = self.runner.invoke(init, input="\n".join(inputs))
        assert result.exit_code == 1
        assert "Error connecting to RPC provider" in result.output
