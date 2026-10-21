"""Holds an event class to its ``(abi_type, name)`` spec: the decoder picks
the class by name, narrows each value to its ABI type and passes the dict as
the class's ``args``, so the class's ``name`` literal must be the spec's name
and its ``args`` TypedDict must match the spec's keys, order and value types."""

import dataclasses
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


def assert_event_class_mirrors_spec(
    event_class: type, name: str, params: tuple[tuple[str, str], ...]
) -> None:
    fields = {field.name: field for field in dataclasses.fields(event_class)}
    assert fields["name"].default == name
    assert typing.get_type_hints(event_class)["name"] == typing.Literal[name]
    assert_args_mirror_spec(typing.get_type_hints(event_class)["args"], params)
