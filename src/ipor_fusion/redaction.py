"""Removes provider URLs from text meant for a person or a log.

RPC provider URLs carry API keys in the path, the query string or the userinfo,
and requests and web3 quote them in their exception messages.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# A URL runs to the next whitespace, since quotes and brackets are legal inside
# one and a key may follow them; only punctuation closing it stays in the text.
# The lookbehind tries each run of scheme characters once, so long hex in an error
# stays linear; a run that is no valid scheme ("0xhttp") comes out masked whole.
_URL = re.compile(r"(?<![a-zA-Z0-9+.\-])[a-zA-Z0-9+.\-]+://\S*[^\s.,;:)'\">]")
# urllib3 names only the path and query: "Max retries exceeded with url: /v2/key"
_REQUEST_TARGET = re.compile(r"\burl: /\S*")
_HOST = re.compile(r"(?:[a-zA-Z0-9.\-]+|\[[0-9a-fA-F:.]+\])(?::\d+)?")


def mask_provider_url(url: str) -> str:
    """Render a provider URL as scheme and host only. An unparsable value is
    rendered as ``***``: what looks like its host may be part of a key."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "***"
    host = parts.netloc.rpartition("@")[2]
    if not parts.scheme or not _HOST.fullmatch(host):
        return "***"
    return f"{parts.scheme}://{host}"


def redact_provider_urls(text: str) -> str:
    """``text`` with every URL reduced to scheme and host, and every request
    target urllib3 quotes on its own replaced."""
    text = _URL.sub(lambda match: mask_provider_url(match.group()), text)
    return _REQUEST_TARGET.sub("url: <redacted>", text)
