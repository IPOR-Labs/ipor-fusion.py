"""Client for authenticating with the IPOR Fusion Keeper REST API.

The keeper (``ipor-fusion-keeper``) serves vault *alpha configuration* behind a
JWT-protected REST API. Reaching it requires a wallet-signature login:

    GET  /api/auth/nonce/{wallet}  -> {"nonce": <int>}     (unauthenticated)
    POST /api/auth/sign            -> {"token": "<jwt>"}   (EIP-191 signed msg)

:class:`KeeperClient` owns that handshake and exposes an authenticated
:meth:`KeeperClient.request` helper for higher-level keeper calls (reading alpha
config, etc.) to build on.

Transport is stdlib-only (``urllib``) so the core SDK keeps no hard HTTP
dependency; signing reuses ``eth_account``, which is already a core dependency.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_account.signers.local import LocalAccount
from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.errors import IporFusionError

DEFAULT_KEEPER_URL = "https://api.mainnet.ipor.io"
PRIVATE_KEY_ENV = "FUSION_PRIVATE_KEY"
KEEPER_URL_ENV = "FUSION_KEEPER_URL"


class KeeperError(IporFusionError):
    """Raised when a Keeper API call fails (network, HTTP, or authentication)."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class KeeperClient:
    """Authenticates a wallet with the IPOR Fusion Keeper via EIP-191 login.

    Construct with an ``eth_account`` ``LocalAccount`` directly, or — more
    commonly — via :meth:`from_env`, which reads the signing key and base URL
    from the environment. Every authenticated call runs the
    ``nonce -> sign -> token`` handshake; in-memory token caching is layered on
    top separately.
    """

    def __init__(
        self,
        account: LocalAccount,
        base_url: str = DEFAULT_KEEPER_URL,
        *,
        timeout: float = 10.0,
    ) -> None:
        self._account = account
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    @classmethod
    def from_env(cls, *, timeout: float = 10.0) -> "KeeperClient":
        """Build a client from ``FUSION_PRIVATE_KEY`` / ``FUSION_KEEPER_URL``.

        The private key is read from the environment only — never written to
        disk or config. Raises :class:`KeeperError` if it is missing or not a
        valid secp256k1 key.
        """
        private_key = os.environ.get(PRIVATE_KEY_ENV)
        if not private_key:
            raise KeeperError(
                f"{PRIVATE_KEY_ENV} is not set; a wallet private key is required "
                "to authenticate with the Keeper."
            )
        base_url = os.environ.get(KEEPER_URL_ENV) or DEFAULT_KEEPER_URL
        try:
            account = Account.from_key(private_key)
        except Exception as exc:
            # eth_account raises a range of types (ValueError, binascii.Error,
            # eth_keys ValidationError, ...) for malformed keys — all mean the
            # same thing to the caller.
            raise KeeperError(f"{PRIVATE_KEY_ENV} is not a valid private key") from exc
        return cls(account, base_url, timeout=timeout)

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def wallet_address(self) -> ChecksumAddress:
        """Checksummed address of the signing wallet."""
        return Web3.to_checksum_address(self._account.address)

    # ── Authentication handshake ──────────────────────────────────────────

    def authenticate(self) -> str:
        """Run ``nonce -> sign -> token`` and return a fresh JWT.

        Always fetches a new (single-use) nonce: the keeper burns each nonce
        when it mints a token, so a nonce cannot be reused across logins.
        """
        nonce = self._get_nonce()
        timestamp = int(time.time())
        signature = self._sign(self._login_message(timestamp, nonce))
        response = self._http(
            "POST",
            "/api/auth/sign",
            body={
                "walletAddress": self.wallet_address,
                "timestamp": timestamp,
                "nonce": nonce,
                "signature": signature,
            },
            authenticated=False,
        )
        token = response.get("token")
        if not token:
            raise KeeperError("Keeper /api/auth/sign did not return a token")
        return str(token)

    def verify(self) -> dict[str, Any]:
        """Confirm the wallet authenticates successfully (GET /api/auth/verify)."""
        return self.request("GET", "/api/auth/verify")

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Issue an authenticated Keeper request (authenticates first)."""
        return self._http(method, path, body=body, authenticated=True)

    # ── Internals ─────────────────────────────────────────────────────────

    def _get_nonce(self) -> int:
        response = self._http(
            "GET", f"/api/auth/nonce/{self.wallet_address}", authenticated=False
        )
        nonce = response.get("nonce")
        if nonce is None:
            raise KeeperError("Keeper /api/auth/nonce did not return a nonce")
        return int(nonce)

    def _login_message(self, timestamp: int, nonce: int) -> str:
        # Must match the keeper's `AuthController.buildLoginMessage` byte-for-byte:
        # checksummed wallet, unix-seconds timestamp, no trailing newline.
        return (
            "Sign in to IPOR Fusion\n"
            f"Wallet: {self.wallet_address}\n"
            f"Timestamp: {timestamp}\n"
            f"Nonce: {nonce}"
        )

    def _sign(self, message: str) -> str:
        # EIP-191 personal_sign: encode_defunct prepends the
        # "\x19Ethereum Signed Message:\n<len>" prefix the keeper recovers against.
        signed = self._account.sign_message(encode_defunct(text=message))
        return signed.signature.to_0x_hex()

    def _http(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        authenticated: bool,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        data: bytes | None = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if authenticated:
            headers["Authorization"] = f"Bearer {self.authenticate()}"
        request = Request(
            f"{self._base_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with urlopen(request, timeout=self._timeout) as resp:  # noqa: S310
                raw = resp.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").strip()
            raise KeeperError(
                f"Keeper {method} {path} failed: HTTP {exc.code} {detail}".rstrip(),
                status_code=exc.code,
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise KeeperError(f"Keeper unreachable: {exc}") from exc
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise KeeperError(f"Keeper returned invalid JSON: {exc}") from exc
