"""Gates of the tests that need a live provider or network source.

Locally a missing or unreachable source skips the test. A run that names the
source in ``IPOR_FUSION_REQUIRE_LIVE`` (comma-separated: provider env var names
such as ``BASE_PROVIDER_URL``, or ``GITHUB`` for the Solidity mirrors) fails
instead, so a green CI run means the live checks ran rather than skipped.
Messages carry the exception type only: provider URLs embed API keys.
"""

from __future__ import annotations

import os
from typing import NoReturn

import pytest
import requests
from web3 import Web3
from web3.exceptions import Web3Exception

from ipor_fusion import Web3Context


def required() -> frozenset[str]:
    return frozenset(
        name.strip()
        for name in os.environ.get("IPOR_FUSION_REQUIRE_LIVE", "").split(",")
        if name.strip()
    )


def unavailable(source: str, reason: str) -> NoReturn:
    """Skip, or fail when this run requires ``source``."""
    if source in required():
        pytest.fail(f"{source} is required in this run: {reason}", pytrace=False)
    pytest.skip(reason)


def provider_url(env_var: str) -> str:
    url = os.environ.get(env_var)
    if not url:
        unavailable(env_var, f"{env_var} not set")
    return url


# A provider that cannot be reached, refuses the key, or has pruned the state.
_INFRA_ERRORS = (Web3Exception, requests.RequestException, OSError)


def archive_context(env_var: str, block: int, contract: str) -> Web3Context:
    """A context pinned to ``block`` on the provider in ``env_var``, once it
    has connected and served ``contract``'s code at that block (historical
    state, not just the header); later SDK errors are test failures, not
    infrastructure."""
    url = provider_url(env_var)
    # The failure is reported outside the except block: a chained traceback
    # would print the request URL, which embeds the API key.
    failure: str | None = None
    try:
        ctx = Web3Context.from_url(url)
        code = ctx.web3.eth.get_code(Web3.to_checksum_address(contract), block)
    except _INFRA_ERRORS as exc:
        failure = type(exc).__name__
    if failure is not None:
        unavailable(env_var, f"{env_var} cannot serve state at {block}: {failure}")
    if not code:
        unavailable(env_var, f"{env_var} returned no code for {contract} at {block}")
    return ctx.at_block(block)
