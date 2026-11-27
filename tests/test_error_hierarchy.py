"""The SDK's error model: every exception it defines derives from
`IporFusionError`, and the builtin bases it keeps say what kind of failure it
is (``ValueError`` for bad input)."""

import importlib
import inspect
import pickle
import pkgutil

import pytest
from eth_abi.exceptions import DecodingError
from web3.exceptions import TimeExhausted
from web3.types import Nonce

import ipor_fusion
from ipor_fusion import (
    BalanceSlotNotFoundError,
    ContractNotFoundError,
    CrosschainDiscoveryError,
    EmptyCallResultError,
    ExternalStateError,
    ExternalStateEventDecodeError,
    HyperCoreEventDecodeError,
    HyperCoreValuationError,
    IporFusionError,
    LogScanError,
    LogSourceUnavailable,
    MorphoApiError,
    MorphoMarketNotFoundError,
    NotPlasmaVaultError,
    OwnerNotFoundError,
    SimulationError,
    SimulationRunError,
    TransactionError,
    TransactionOutcomeUnknown,
    TransactionTimeout,
    UnsupportedChainError,
    UnsupportedContractVersionError,
    UnsupportedVaultVersionError,
    VerifierResultUnavailable,
    parse_tx_hash,
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


def test_every_sdk_exception_lives_in_the_errors_module():
    elsewhere = sorted(
        f"{cls.__module__}.{cls.__qualname__}"
        for cls in _sdk_exceptions()
        if cls.__module__ != "ipor_fusion.errors"
    )
    assert elsewhere == []


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
        (HyperCoreEventDecodeError, ValueError),
        (ExternalStateError, ValueError),
        (HyperCoreValuationError, ValueError),
        (BalanceSlotNotFoundError, ValueError),
        (UnsupportedContractVersionError, ValueError),
        (MorphoApiError, RuntimeError),
        (SimulationRunError, RuntimeError),
        (VerifierResultUnavailable, LookupError),
        (OwnerNotFoundError, LookupError),
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
        TransactionOutcomeUnknown,
        TransactionTimeout,
        VerifierResultUnavailable,
        OwnerNotFoundError,
    ],
)
def test_chain_and_remote_failures_are_not_bad_input(error):
    assert not issubclass(error, ValueError)


def test_transaction_timeout_is_web3s_time_exhausted():
    tx_hash = parse_tx_hash("0x" + "ab" * 32)
    error = TransactionTimeout("timed out", tx_hash, Nonce(7))
    assert isinstance(error, TimeExhausted)
    assert isinstance(error, TransactionOutcomeUnknown)
    assert not isinstance(error, TransactionError)
    assert error.tx_hash == tx_hash
    assert error.nonce == 7
    assert str(error) == f"timed out, tx_hash={tx_hash}, nonce=7"


@pytest.mark.parametrize("error", [TransactionOutcomeUnknown, TransactionTimeout])
def test_an_unknown_outcome_survives_pickling(error):
    tx_hash = parse_tx_hash("0x" + "cd" * 32)
    restored = pickle.loads(pickle.dumps(error("lost", tx_hash, Nonce(9))))  # noqa: S301 -- our own bytes
    assert type(restored) is error
    assert restored.tx_hash == tx_hash
    assert restored.nonce == 9
    assert str(restored) == f"lost, tx_hash={tx_hash}, nonce=9"


def test_empty_call_result_is_no_longer_an_abi_decoding_error():
    assert not issubclass(EmptyCallResultError, DecodingError)
