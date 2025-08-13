import pytest
from pathlib import Path

import yaml
from click.testing import CliRunner
from ipor_fusion.cli.commands.init import init

class TestInitCommand:
    """Test the init command"""

    def setup_method(self):
        """Set up test fixtures"""
        self.runner = CliRunner()

    def test_init_with_prompts(self, tmp_path):
        """Test init command with interactive prompts"""
        inputs = [
            "https://eth-mainnet.g.alchemy.com/v2/4OnUoeIrjs50u3OqXJrSB",
            "0x6f66b845604dad6E80b2A1472e6cAcbbE66A8C40",
            "y",
            "y",
            "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
            "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
            "n"
        ]
        
        config_path = tmp_path / "ipor-fusion-config.yaml"
        
        result = self.runner.invoke(init, input="\n".join(inputs), args=["--config-file", str(config_path)])
        assert result.exit_code == 0
        assert "Configuration file created" in result.output

        assert config_path.exists()

        content = config_path.read_text()
        config_data = yaml.safe_load(content)

        assert config_data["rpc_url"] == "https://eth-mainnet.g.alchemy.com/v2/4OnUoeIrjs50u3OqXJrSB"
        assert config_data["plasma_vault_address"] == "0x6f66b845604dad6E80b2A1472e6cAcbbE66A8C40"
        assert "private_key" in config_data

