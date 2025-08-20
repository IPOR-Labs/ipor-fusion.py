import time
from pathlib import Path

import click
import requests
import yaml
from eth_abi import decode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3


class ContractNameResolver:
    def __init__(self, cache_path=None):
        """
        Initialize ContractNameResolver with optional custom cache path.

        :param cache_path: Optional path to the cache file.
                            Defaults to ~/.ipor_fusion_contract_name_cache.yaml
        """
        self._cache_path = (
            cache_path or Path.home() / ".ipor_fusion_contract_name_cache.yaml"
        )

    def _get_scan_api_url(self, chain_id: int):
        """
        Get the scan API URL based on the chain ID.

        :param chain_id: Blockchain network chain ID
        :return: Scan API URL for the given chain
        """
        if chain_id == 42161:
            return "https://api.arbiscan.io/api"
        elif chain_id == 8453:
            return "https://api.basescan.org/api"
        elif chain_id == 1:
            return "https://api.etherscan.io/api"
        else:
            raise ValueError(f"Unsupported chain id: {chain_id}")

    def _load_cache(self):
        """
        Load the contract name cache from file.

        :return: Loaded cache dictionary or empty dict
        """
        if self._cache_path.exists():
            with open(self._cache_path, "r") as cache_file:
                return yaml.safe_load(cache_file) or {}
        return {}

    def _save_cache(self, cache):
        """
        Save the cache to the file.

        :param cache: Cache dictionary to save
        """
        with open(self._cache_path, "w") as cache_file:
            yaml.safe_dump(cache, cache_file)

    def get_contract_name(self, scan_api_access_token: str, system, address: str):
        """
        Retrieve the contract name using scan API, with caching.

        :param scan_api_access_token: API token for the scan service
        :param system: PlasmaSystem instance
        :param address: Contract address to resolve
        :return: Contract name or None
        """
        # Check cache first
        cache = self._load_cache()
        key = f"{system.chain_id()}_{address}"

        if key in cache:
            return cache[key]

        params = {
            "module": "contract",
            "action": "getsourcecode",
            "address": address,
            "apikey": scan_api_access_token,
        }

        try:
            response = requests.get(
                self._get_scan_api_url(system.chain_id()), params=params
            )
            time.sleep(1)  # Rate limiting
            data = response.json()

            if data["status"] == "1" and data["result"]:
                contract_name = data["result"][0]["ContractName"]

                if contract_name:
                    # Special handling for Erc4626SupplyFuse
                    if contract_name == "Erc4626SupplyFuse":
                        sig = function_signature_to_4byte_selector("MARKET_ID()")
                        read = system.transaction_executor().read(
                            Web3.to_checksum_address(address), sig
                        )
                        (market_id,) = decode(["uint256"], read)
                        contract_name = contract_name + f"MarketId{market_id}"

                    # Update cache
                    cache[key] = contract_name
                    self._save_cache(cache)

                    return contract_name
        except Exception as e:
            print(f"Error fetching contract name: {e}")

        return None
