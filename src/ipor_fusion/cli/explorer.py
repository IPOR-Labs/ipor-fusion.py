from __future__ import annotations

import json
import threading
import time
from urllib.parse import urlencode
from urllib.request import urlopen

from ipor_fusion.cli.config_store import load_contract_cache, update_contract_cache

ETHERSCAN_V2_URL = "https://api.etherscan.io/v2/api"

SUPPORTED_CHAINS = {1, 42161, 8453, 10, 137, 56, 43114, 250}

_MAX_RETRIES = 3
_RETRY_DELAY = 1.0


class _RateLimiter:
    def __init__(self, calls_per_second: float = 2.5):
        self._min_interval = 1.0 / calls_per_second
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if (elapsed := now - self._last_call) < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_call = time.monotonic()


_etherscan_limiter = _RateLimiter(2.5)


def get_contract_name(chain_id: int, address: str, api_key: str | None = None) -> str:
    cache = load_contract_cache()
    cache_key = f"{chain_id}:{address}"

    if cached := cache.get(cache_key):
        return cached

    if name := _fetch_contract_name(chain_id, address, api_key):
        update_contract_cache(cache_key, name)
        return name
    return ""


def _fetch_contract_name(
    chain_id: int, address: str, api_key: str | None = None
) -> str | None:
    if chain_id not in SUPPORTED_CHAINS:
        return None
    if not api_key:
        return None

    params: dict[str, str] = {
        "chainid": str(chain_id),
        "module": "contract",
        "action": "getsourcecode",
        "address": address,
        "apikey": api_key,
    }

    url = f"{ETHERSCAN_V2_URL}?{urlencode(params)}"
    for attempt in range(_MAX_RETRIES):
        _etherscan_limiter.wait()
        try:
            with urlopen(url, timeout=10) as resp:  # noqa: S310
                data = json.loads(resp.read().decode())
            if data.get("status") == "1" and data.get("result"):
                return data["result"][0].get("ContractName") or None  # type: ignore[no-any-return]
            if "rate limit" in str(data.get("result", "")).lower():
                time.sleep(_RETRY_DELAY * (attempt + 1))
                continue
        except (OSError, json.JSONDecodeError, KeyError, IndexError):
            pass
        break
    return None
