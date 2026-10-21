"""Holds an event's ``args`` TypedDict to its ``(abi_type, name)`` spec: the
decoder narrows each value to its ABI type and casts the dict to the shape,
so the shape's keys, order and value types must match the spec exactly."""

import typing


def _python_type(abi_type: str) -> type:
    if abi_type == "address":
        return str
    if abi_type == "bool":
        return bool
    if abi_type.startswith("bytes"):
        return bytes
    return int


def _base(hint: object) -> object:
    """A NewType's builtin (``ChecksumAddress`` -> ``str``)."""
    while hasattr(hint, "__supertype__"):
        hint = hint.__supertype__  # type: ignore[attr-defined]
    return hint


def assert_args_mirror_spec(shape: type, params: tuple[tuple[str, str], ...]) -> None:
    hints = typing.get_type_hints(shape)
    assert list(hints) == [name for _, name in params]
    for abi_type, name in params:
        assert _base(hints[name]) is _python_type(abi_type), name
