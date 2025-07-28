#!/usr/bin/env python3
"""
Tests for encryption functionality
"""

import tempfile
import yaml
from pathlib import Path
from click.testing import CliRunner

from ipor_fusion.cli.config import ConfigManager, FusionConfig
from ipor_fusion.cli.encryption import EncryptionManager
from ipor_fusion.cli.commands.init import init
from ipor_fusion.cli.commands.encrypt import encrypt


class TestEncryption:
    """Test encryption functionality"""

    def setup_method(self):
        """Set up test fixtures"""
        self.runner = CliRunner()
        self.temp_dir = tempfile.mkdtemp()
        self.original_cwd = Path.cwd()
        Path(self.temp_dir).mkdir(exist_ok=True)

    def teardown_method(self):
        """Clean up after tests"""
        import shutil
        shutil.rmtree(self.temp_dir)

    def test_encryption_manager(self):
        """Test basic encryption and decryption"""
        private_key = "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        password = "test_password_123"
        
        # Test encryption
        encrypted = EncryptionManager.encrypt_private_key(private_key, password)
        assert encrypted != private_key
        assert EncryptionManager.is_encrypted_private_key(encrypted)
        assert not EncryptionManager.is_encrypted_private_key(private_key)
        
        # Test decryption
        decrypted = EncryptionManager.decrypt_private_key(encrypted, password)
        assert decrypted == private_key
        
        # Test wrong password
        try:
            EncryptionManager.decrypt_private_key(encrypted, "wrong_password")
            assert False, "Should have raised ValueError"
        except ValueError:
            pass

    def test_config_with_encryption(self):
        """Test creating config with encrypted private key"""
        plasma_vault = "0x1234567890123456789012345678901234567890"
        provider_url = "https://example.com/rpc"
        private_key = "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        password = "test_password_123"
        
        # Create config with encryption
        config_path = ConfigManager.create_config(
            plasma_vault_address=plasma_vault,
            provider_url=provider_url,
            private_key=private_key,
            encrypt_private_key=True,
            encryption_password=password,
        )
        
        # Load config and verify encryption
        config = ConfigManager.load_config(str(config_path))
        assert config.is_private_key_encrypted()
        assert config.private_key != private_key
        
        # Test decryption
        decrypted = config.get_decrypted_private_key(password)
        assert decrypted == private_key

    def test_init_command_with_encryption(self):
        """Test init command with encryption flag"""
        inputs = [
            "0x1234567890123456789012345678901234567890",  # plasma vault address
            "https://example.com/rpc",  # provider url
            "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",  # private key
            "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",  # confirm private key
            "test_password_123",  # encryption password
            "test_password_123",  # confirm encryption password
        ]
        
        result = self.runner.invoke(
            init, 
            ["--encrypt-private-key"],
            input="\n".join(inputs)
        )
        
        assert result.exit_code == 0
        assert "Configuration file created" in result.output
        assert "Private key has been encrypted" in result.output
        
        # Verify the config file has encrypted private key
        config_path = Path("ipor-fusion-config.yaml")
        assert config_path.exists()
        
        with open(config_path, "r") as f:
            data = yaml.safe_load(f)
            assert data["private_key"] != "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"

    def test_encrypt_existing_config(self):
        """Test encrypting an existing config file"""
        # First create a config without encryption
        config_path = ConfigManager.create_config(
            plasma_vault_address="0x1234567890123456789012345678901234567890",
            provider_url="https://example.com/rpc",
            private_key="0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        )
        
        # Verify it's not encrypted
        config = ConfigManager.load_config(str(config_path))
        assert not config.is_private_key_encrypted()
        
        # Now encrypt it
        password = "test_password_123"
        updated_path = ConfigManager.encrypt_existing_private_key(
            str(config_path), password
        )
        
        # Verify it's now encrypted
        config = ConfigManager.load_config(str(updated_path))
        assert config.is_private_key_encrypted()
        
        # Test decryption
        decrypted = config.get_decrypted_private_key(password)
        assert decrypted == "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"

    def test_encrypt_command(self):
        """Test the encrypt CLI command"""
        # First create a config without encryption
        config_path = ConfigManager.create_config(
            plasma_vault_address="0x1234567890123456789012345678901234567890",
            provider_url="https://example.com/rpc",
            private_key="0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        )
        
        # Run encrypt command
        inputs = [
            "test_password_123",  # encryption password
            "test_password_123",  # confirm encryption password
        ]
        
        result = self.runner.invoke(
            encrypt,
            input="\n".join(inputs)
        )
        
        assert result.exit_code == 0
        assert "Private key encrypted successfully" in result.output
        
        # Verify the config is now encrypted
        config = ConfigManager.load_config(str(config_path))
        assert config.is_private_key_encrypted()

    def test_encryption_info(self):
        """Test encryption info method"""
        info = EncryptionManager.get_encryption_info()
        assert "method" in info
        assert "key_derivation" in info
        assert "iterations" in info
        assert info["method"] == "Fernet (AES-128-CBC)"
        assert info["key_derivation"] == "PBKDF2-HMAC-SHA256"
        assert info["iterations"] == 100000 