"""The SDK's error model: every exception it defines derives from
`IporFusionError`, and the builtin bases it keeps say what kind of failure it
is (``ValueError`` for bad input)."""

import importlib
import inspect
import pkgutil

import pytest
from eth_abi.exceptions import DecodingError

import ipor_fusion
from ipor_fusion import (
    ContractNotFoundError,
    CrosschainDiscoveryError,
    EmptyCallResultError,
    ExternalStateEventDecodeError,
    IporFusionError,
    LogScanError,
    LogSourceUnavailable,
    MorphoApiError,
    MorphoMarketNotFoundError,
    NotPlasmaVaultError,
    SimulationError,
    SimulationRunError,
    TransactionError,
    UnsupportedChainError,
    UnsupportedVaultVersionError,
    VerifierResultUnavailable,
)


def _sdk_exceptions() -> set[type[BaseException]]:
    """Every public exception class a module of the package defines (a private
    one, such as a scan's internal control flow, never reaches a caller)."""
    found: set[type[BaseException]] = set()
    for module_info in pkgutil.walk_packages(
        ipor_fusion.__path__, f"{ipor_fusion.__name__}."
    ):
        module = importlib.import_module(module_info.name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, BaseException)
                and obj.__module__.startswith("ipor_fusion.")
                and not obj.__name__.startswith("_")
            ):
                found.add(obj)
    return found


def test_every_sdk_exception_derives_from_the_base():
    exceptions = _sdk_exceptions()
    assert MorphoApiError in exceptions
    stray = sorted(
        f"{cls.__module__}.{cls.__qualname__}"
        for cls in exceptions
        if not issubclass(cls, IporFusionError)
    )
    assert stray == []


def test_every_sdk_exception_is_exported():
    exported = {
        getattr(ipor_fusion, name)
        for name in ipor_fusion.__all__
        if inspect.isclass(getattr(ipor_fusion, name))
    }
    missing = sorted(cls.__qualname__ for cls in _sdk_exceptions() - exported)
    assert missing == []


@pytest.mark.parametrize(
    ("error", "builtin"),
    [
        (ContractNotFoundError, ValueError),
        (NotPlasmaVaultError, ValueError),
        (UnsupportedChainError, ValueError),
        (UnsupportedVaultVersionError, ValueError),
        (MorphoMarketNotFoundError, ValueError),
        (ExternalStateEventDecodeError, ValueError),
        (MorphoApiError, RuntimeError),
        (SimulationRunError, RuntimeError),
        (VerifierResultUnavailable, LookupError),
    ],
)
def test_builtin_bases_are_kept(error, builtin):
    assert issubclass(error, IporFusionError)
    assert issubclass(error, builtin)


@pytest.mark.parametrize(
    "error",
    [
        CrosschainDiscoveryError,
        EmptyCallResultError,
        LogScanError,
        LogSourceUnavailable,
        MorphoApiError,
        SimulationError,
        SimulationRunError,
        TransactionError,
        VerifierResultUnavailable,
    ],
)
def test_chain_and_remote_failures_are_not_bad_input(error):
    assert not issubclass(error, ValueError)


def test_empty_call_result_is_no_longer_an_abi_decoding_error():
    assert not issubclass(EmptyCallResultError, DecodingError)
