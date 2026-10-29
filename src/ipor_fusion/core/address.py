def _validate_not_zero_address(value: str, name: str) -> None:
    """Reject an empty or zero address; ``name`` labels it in the error.

    Compares the parsed bytes rather than the text, so every spelling of the
    zero address is caught -- ``0x``- or ``0X``-prefixed, or bare. Input that
    is not hex at all falls through to the encoder, which reports the
    malformed address.
    """
    if not value:
        raise ValueError(f"{name} is required and must not be zero address")
    digits = value[2:] if value[:2].lower() == "0x" else value
    try:
        payload = bytes.fromhex(digits)
    except ValueError:
        return
    if payload == bytes(20):
        raise ValueError(f"{name} must not be zero address")
