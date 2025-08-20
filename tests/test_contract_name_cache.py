import os
import pytest
import yaml
from unittest.mock import Mock, patch
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ipor_fusion.cli.commands.config import get_contract_name


@pytest.fixture
def mock_cache_path(tmp_path):
    cache_path = tmp_path / ".ipor_fusion_contract_name_cache.yaml"
    with patch("pathlib.Path.home", return_value=tmp_path):
        yield cache_path


def test_contract_name_cache_first_call(mock_cache_path):
    # Create a mock system and other required parameters
    mock_system = Mock()
    mock_system.chain_id.return_value = 1
    scan_api_token = "test_token"
    test_address = "0x1234567890123456789012345678901234567890"

    # Mock the requests.get to return a specific contract name
    with patch("requests.get") as mock_get:
        mock_response = Mock()
        mock_response.json.return_value = {
            "status": "1",
            "result": [{"ContractName": "TestContract"}],
        }
        mock_get.return_value = mock_response

        # First call should make an API request
        contract_name = get_contract_name(scan_api_token, mock_system, test_address)

        # Verify the contract name is correct
        assert contract_name == "TestContract"

        # Verify the cache file was created
        assert mock_cache_path.exists()

        # Check cache contents
        with open(mock_cache_path, "r") as f:
            cache = yaml.safe_load(f)

        assert cache is not None
        assert f"1_{test_address}" in cache
        assert cache[f"1_{test_address}"] == "TestContract"


def test_contract_name_cache_second_call(mock_cache_path):
    # Create a mock system and other required parameters
    mock_system = Mock()
    mock_system.chain_id.return_value = 1
    scan_api_token = "test_token"
    test_address = "0x1234567890123456789012345678901234567890"

    # Prepare the cache file
    cache_data = {f"1_{test_address}": "CachedTestContract"}
    with open(mock_cache_path, "w") as f:
        yaml.safe_dump(cache_data, f)

    # Mock the requests.get to ensure it's not called
    with patch("requests.get") as mock_get:
        # Second call should return cached value without making API request
        contract_name = get_contract_name(scan_api_token, mock_system, test_address)

        # Verify the contract name is from cache
        assert contract_name == "CachedTestContract"

        # Ensure no API request was made
        mock_get.assert_not_called()
