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
        pytest.fail(f"{source} is required in this run: {reason}")
    pytest.skip(reason)


def provider_url(env_var: str) -> str:
    url = os.environ.get(env_var)
    if not url:
        unavailable(env_var, f"{env_var} not set")
    return url


def archive_context(env_var: str, block: int) -> Web3Context:
    """A context pinned to ``block`` on the provider in ``env_var``, once the
    provider has served that block (an archive node); later SDK errors are
    test failures, not infrastructure."""
    ctx = Web3Context.from_url(provider_url(env_var))
    try:
        ctx.web3.eth.get_block(block)
    except (Web3Exception, requests.RequestException, OSError) as exc:
        unavailable(
            env_var, f"{env_var} cannot serve block {block}: {type(exc).__name__}"
        )
    return ctx.at_block(block)
