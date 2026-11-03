"""Text read from a contract is chosen by whoever deployed it.

Callers print it, log it and hand it to other programs, so it must not carry
control sequences, invisible characters or unbounded length. This does not make
the wording trustworthy: a clean string can still say anything.
"""

import unicodedata

MAX_ONCHAIN_TEXT_CHARS = 256

# Control, format (bidi overrides, zero-width and tag characters), surrogate,
# private use, and line and paragraph separators.
_REMOVED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Zl", "Zp"})
_LINE_BREAKS = frozenset("\t\n\r\f\v\u2028\u2029")


def sanitize_onchain_text(value: str) -> str:
    """``value`` as one line of visible text, at most `MAX_ONCHAIN_TEXT_CHARS`
    long plus an ellipsis when cut. Line breaks and tabs become spaces, so words
    stay apart; other removed characters disappear."""
    cleaned = "".join(
        " " if character in _LINE_BREAKS else character
        for character in value
        if character in _LINE_BREAKS
        or unicodedata.category(character) not in _REMOVED_CATEGORIES
    )
    if len(cleaned) > MAX_ONCHAIN_TEXT_CHARS:
        return cleaned[:MAX_ONCHAIN_TEXT_CHARS] + "…"
    return cleaned
