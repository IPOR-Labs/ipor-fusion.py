"""Offline unit tests for the Keeper authentication client.

All network I/O is mocked (``urlopen`` is patched), so these run without a
keeper, an RPC, or any real wallet. The signing key below is a throwaway test
fixture — it controls nothing and is unknown to any real keeper deployment.
"""

import base64
import io
import json
import time
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

from ipor_fusion.core.keeper import KeeperClient, KeeperError

# Throwaway secp256k1 key — a test fixture, not a secret (cf. test_web3_context.py).
TEST_PRIVATE_KEY = "0x" + "11" * 32
TEST_ACCOUNT = Account.from_key(TEST_PRIVATE_KEY)
TEST_WALLET = Web3.to_checksum_address(TEST_ACCOUNT.address)
BASE_URL = "https://keeper.test"
TOKEN = "header.payload.signature"  # opaque to this commit (no JWT parsing yet)


def _resp(raw: bytes) -> MagicMock:
    """A urlopen() return value: a context manager whose read() yields `raw`."""
    resp = MagicMock()
    resp.read.return_value = raw
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _json(payload: dict) -> MagicMock:
    return _resp(json.dumps(payload).encode())


def _jwt(exp: float | None) -> str:
    """A minimal JWT-shaped string whose payload carries `exp` (or none).

    Only the middle (payload) segment is meaningful — the client reads `exp`
    from it and never verifies the signature.
    """
    payload = {} if exp is None else {"exp": exp}
    segment = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    return f"hdr.{segment.decode()}.sig"


class _FakeKeeper:
    """Routes urlopen() calls by URL, recording each Request for assertions.

    `verify_errors` is a queue of HTTP status codes returned (as errors) by
    successive /api/auth/verify calls; once exhausted, verify succeeds.
    """

    def __init__(
        self,
        nonce: int = 7,
        token: str = TOKEN,
        verify_errors: list[int] | None = None,
    ) -> None:
        self.nonce = nonce
        self.token = token
        self.verify_errors = list(verify_errors or [])
        self.calls: list = []

    def __call__(self, request, timeout=None):
        self.calls.append(request)
        url = request.full_url
        if "/api/auth/nonce/" in url:
            return _json({"nonce": self.nonce})
        if url.endswith("/api/auth/sign"):
            return _json({"token": self.token})
        if url.endswith("/api/auth/verify"):
            if self.verify_errors:
                status = self.verify_errors.pop(0)
                raise HTTPError(url, status, "err", {}, io.BytesIO(b'{"error":"x"}'))
            return _json({"valid": True, "walletAddress": TEST_WALLET})
        raise AssertionError(f"unexpected {request.get_method()} {url}")

    def calls_to(self, suffix: str) -> list:
        return [c for c in self.calls if c.full_url.endswith(suffix)]


def _client() -> KeeperClient:
    return KeeperClient(TEST_ACCOUNT, BASE_URL)


# ── Signature compatibility (the keystone) ────────────────────────────────


def test_signature_recovers_to_wallet():
    """The client's signature recovers to its own wallet under the keeper's
    documented message format.

    `keeper_message` below is an *independent* literal (not imported from
    `_login_message`), so this catches accidental drift in the client's message
    format. It does NOT prove parity with the Java `Eip191SignatureVerifier` —
    a format wrong in both places, or wrong against the real keeper, would still
    pass. True parity is covered by a keeper-sourced vector or the live smoke
    test."""
    fake = _FakeKeeper()
    with patch("ipor_fusion.core.keeper.urlopen", fake):
        _client().authenticate()

    body = json.loads(fake.calls_to("/api/auth/sign")[0].data)
    # Reconstruct the message the *keeper* builds (mirrors AuthController), then
    # recover from the signature our client produced.
    keeper_message = (
        "Sign in to IPOR Fusion\n"
        f"Wallet: {body['walletAddress']}\n"
        f"Timestamp: {body['timestamp']}\n"
        f"Nonce: {body['nonce']}"
    )
    recovered = Account.recover_message(
        encode_defunct(text=keeper_message), signature=body["signature"]
    )
    assert recovered == TEST_WALLET
    assert body["walletAddress"] == TEST_WALLET
    assert body["nonce"] == fake.nonce


# ── Handshake behaviour ───────────────────────────────────────────────────


def test_authenticate_returns_token():
    fake = _FakeKeeper()
    with patch("ipor_fusion.core.keeper.urlopen", fake):
        assert _client().authenticate() == TOKEN


def test_authenticate_fetches_fresh_nonce_each_call():
    fake = _FakeKeeper()
    client = _client()
    with patch("ipor_fusion.core.keeper.urlopen", fake):
        client.authenticate()
        client.authenticate()
    assert len(fake.calls_to(f"/api/auth/nonce/{TEST_WALLET}")) == 2


def test_request_reuses_cached_token():
    """A second request reuses the cached token — only one handshake total."""
    fake = _FakeKeeper()
    client = _client()
    with patch("ipor_fusion.core.keeper.urlopen", fake):
        result = client.verify()
        client.verify()

    assert result == {"valid": True, "walletAddress": TEST_WALLET}
    assert len(fake.calls_to("/api/auth/sign")) == 1  # cached after first auth
    verify_req = fake.calls_to("/api/auth/verify")[0]
    assert verify_req.get_header("Authorization") == f"Bearer {TOKEN}"


# ── Token caching & expiry ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",  # not three segments
        "hdr.aaaaa.sig",  # payload raises binascii.Error (bad base64 length)
        "hdr.@@@.sig",  # payload decodes to b'' -> JSON decode fails
        "hdr."
        + base64.urlsafe_b64encode(b"123").decode()
        + ".sig",  # payload is valid JSON but not a dict
        "hdr."
        + base64.urlsafe_b64encode(b'{"exp":"soon"}').decode()
        + ".sig",  # `exp` present but not a number
        _jwt(None),  # valid dict payload, no `exp` claim
        _jwt(time.time() + 3600),  # `exp` well in the future
    ],
)
def test_unexpired_or_unreadable_token_is_cached(token):
    """Tokens with no readable/near `exp` are reused — a single handshake."""
    # Rule: use the cache whenever available, invalidate on 401. Reading `exp` is
    # only a proactive-refresh optimization, so an unreadable `exp` still caches.
    fake = _FakeKeeper(token=token)
    client = _client()
    with patch("ipor_fusion.core.keeper.urlopen", fake):
        client.verify()
        client.verify()
    assert len(fake.calls_to("/api/auth/sign")) == 1


def test_expired_token_triggers_reauth():
    """A token at/near its `exp` is refreshed — a handshake per request."""
    fake = _FakeKeeper(token=_jwt(time.time() - 10))  # already past
    client = _client()
    with patch("ipor_fusion.core.keeper.urlopen", fake):
        client.verify()
        client.verify()
    assert len(fake.calls_to("/api/auth/sign")) == 2


# ── 401 re-authentication ─────────────────────────────────────────────────


def test_401_triggers_single_reauth_and_retry():
    """A 401 discards the token, re-authenticates once, and the retry succeeds."""
    fake = _FakeKeeper(token=_jwt(time.time() + 3600), verify_errors=[401])
    client = _client()
    with patch("ipor_fusion.core.keeper.urlopen", fake):
        result = client.verify()

    assert result == {"valid": True, "walletAddress": TEST_WALLET}
    assert len(fake.calls_to("/api/auth/verify")) == 2  # 401, then success
    assert len(fake.calls_to("/api/auth/sign")) == 2  # re-auth after the 401


def test_token_cached_after_401_recovery():
    """The token minted during 401 recovery is cached — no third handshake."""
    fake = _FakeKeeper(token=_jwt(time.time() + 3600), verify_errors=[401])
    client = _client()
    with patch("ipor_fusion.core.keeper.urlopen", fake):
        client.verify()  # sign #1 (cold) -> 401 -> sign #2 -> success
        client.verify()  # reuses the post-recovery token

    assert len(fake.calls_to("/api/auth/sign")) == 2  # no third handshake
    assert len(fake.calls_to("/api/auth/verify")) == 3  # 401, success, success


def test_persistent_401_propagates_after_one_retry():
    """If the retry also 401s, the error surfaces (no infinite loop)."""
    fake = _FakeKeeper(token=_jwt(time.time() + 3600), verify_errors=[401, 401])
    client = _client()
    with patch("ipor_fusion.core.keeper.urlopen", fake):
        with pytest.raises(KeeperError) as excinfo:
            client.verify()

    assert excinfo.value.status_code == 401
    assert len(fake.calls_to("/api/auth/verify")) == 2  # exactly one retry


def test_non_401_error_is_not_retried():
    """Non-401 failures propagate immediately without a retry."""
    fake = _FakeKeeper(token=_jwt(time.time() + 3600), verify_errors=[500])
    client = _client()
    with patch("ipor_fusion.core.keeper.urlopen", fake):
        with pytest.raises(KeeperError) as excinfo:
            client.verify()

    assert excinfo.value.status_code == 500
    assert len(fake.calls_to("/api/auth/verify")) == 1  # no retry


def test_nonce_request_is_unauthenticated():
    fake = _FakeKeeper()
    with patch("ipor_fusion.core.keeper.urlopen", fake):
        _client().authenticate()
    nonce_req = fake.calls_to(f"/api/auth/nonce/{TEST_WALLET}")[0]
    assert nonce_req.get_header("Authorization") is None


# ── from_env ──────────────────────────────────────────────────────────────


def test_from_env_uses_key_and_default_url(monkeypatch):
    monkeypatch.setenv("FUSION_PRIVATE_KEY", TEST_PRIVATE_KEY)
    monkeypatch.delenv("FUSION_KEEPER_URL", raising=False)
    client = KeeperClient.from_env()
    assert client.base_url == "https://api.mainnet.ipor.io"
    assert client.wallet_address == TEST_WALLET


def test_from_env_strips_trailing_slash_from_custom_url(monkeypatch):
    monkeypatch.setenv("FUSION_PRIVATE_KEY", TEST_PRIVATE_KEY)
    monkeypatch.setenv("FUSION_KEEPER_URL", "http://localhost:9066/")
    assert KeeperClient.from_env().base_url == "http://localhost:9066"


def test_from_env_requires_private_key(monkeypatch):
    monkeypatch.delenv("FUSION_PRIVATE_KEY", raising=False)
    with pytest.raises(KeeperError, match="FUSION_PRIVATE_KEY"):
        KeeperClient.from_env()


def test_from_env_rejects_invalid_key(monkeypatch):
    monkeypatch.setenv("FUSION_PRIVATE_KEY", "not-a-key")
    with pytest.raises(KeeperError, match="valid private key"):
        KeeperClient.from_env()


# ── HTTP error mapping ────────────────────────────────────────────────────


def test_http_error_preserves_status_code():
    err = HTTPError(
        f"{BASE_URL}/api/auth/nonce/{TEST_WALLET}",
        404,
        "Not Found",
        {},
        io.BytesIO(b'{"error":"user not found"}'),
    )
    with patch("ipor_fusion.core.keeper.urlopen", side_effect=err):
        with pytest.raises(KeeperError) as excinfo:
            _client().authenticate()
    assert excinfo.value.status_code == 404
    assert "404" in str(excinfo.value)


def test_unreachable_keeper_raises():
    with patch("ipor_fusion.core.keeper.urlopen", side_effect=URLError("refused")):
        with pytest.raises(KeeperError, match="unreachable"):
            _client().authenticate()


def test_invalid_json_raises():
    with patch("ipor_fusion.core.keeper.urlopen", return_value=_resp(b"not json")):
        with pytest.raises(KeeperError, match="invalid JSON"):
            _client().authenticate()


def test_empty_nonce_body_raises():
    # Empty body -> _http returns {} -> nonce missing. Exercises the empty-body path.
    with patch("ipor_fusion.core.keeper.urlopen", return_value=_resp(b"")):
        with pytest.raises(KeeperError, match="did not return a nonce"):
            _client().authenticate()


def test_missing_token_raises():
    with patch(
        "ipor_fusion.core.keeper.urlopen",
        side_effect=[_json({"nonce": 1}), _json({})],
    ):
        with pytest.raises(KeeperError, match="did not return a token"):
            _client().authenticate()
