"""Crosschain Plasma Vault support, split like the contracts: shared
``messages`` and ``contracts`` (wrapper bases), plus one subpackage per
transport (``stargate``, ``ccip``) with its wire codecs and wrappers.

This namespace exports only the fuse-free half of the package. The fuse
encoders in :mod:`ipor_fusion.fuses.crosschain` import it, and Python runs a
package's ``__init__`` before any of its modules, so anything here that
imported the encoders back would re-enter a half-initialized module. The
modules that need the encoders, ``lane``, ``discovery``, ``transport``,
``simulation`` and the per-transport ``lane``/``transport``, are exported from
:mod:`ipor_fusion` and importable by module path.
"""

from ipor_fusion.crosschain.ccip.codec import (
    CCIP_MESSAGE_SENT_TOPIC,
    Any2EVMMessage,
    CcipMessageSent,
    EVMTokenAmount,
    MessageV1,
    TokenTransferV1,
    ccip_receive_calldata,
)
from ipor_fusion.crosschain.ccip.contracts import (
    CcipCrosschainDispatcher,
    CcipCrosschainExecutor,
    CcipCrosschainFactory,
    CcipObservation,
    CcipRouteConfig,
    SafetyConfig,
)
from ipor_fusion.crosschain.contracts import (
    CrosschainDispatcher,
    CrosschainExecutor,
    CrosschainFactory,
)
from ipor_fusion.crosschain.messages import (
    CCIP_VERSION,
    CODEC_VERSION,
    EMPTY_COMMAND,
    BusinessAction,
    CcipMsgType,
    Command,
    CommandStatus,
    CrosschainTransportKind,
    DeploymentStatus,
    MsgType,
    decode_ccip_envelope,
    decode_command,
    decode_envelope,
)
from ipor_fusion.crosschain.stargate.contracts import (
    ActiveCommand,
    BalanceObservation,
    ExecutorInitParams,
    Observation,
    StargateCrosschainDispatcher,
    StargateCrosschainExecutor,
    StargateCrosschainFactory,
)
from ipor_fusion.crosschain.stargate.layerzero import (
    PACKET_SENT_TOPIC,
    EnforcedOptionParam,
    OptionsBuilder,
    Packet,
    TaxiMessage,
    encode_enforced_options,
    encode_oft_compose_msg,
    lz_compose_calldata,
    lz_receive_calldata,
)

__all__ = [
    "ActiveCommand",
    "Any2EVMMessage",
    "BalanceObservation",
    "BusinessAction",
    "CCIP_MESSAGE_SENT_TOPIC",
    "CCIP_VERSION",
    "CODEC_VERSION",
    "CcipCrosschainDispatcher",
    "CcipCrosschainExecutor",
    "CcipCrosschainFactory",
    "CcipMessageSent",
    "CcipMsgType",
    "CcipObservation",
    "CcipRouteConfig",
    "Command",
    "CommandStatus",
    "CrosschainDispatcher",
    "CrosschainExecutor",
    "CrosschainFactory",
    "CrosschainTransportKind",
    "DeploymentStatus",
    "EMPTY_COMMAND",
    "EVMTokenAmount",
    "EnforcedOptionParam",
    "ExecutorInitParams",
    "MessageV1",
    "MsgType",
    "Observation",
    "OptionsBuilder",
    "PACKET_SENT_TOPIC",
    "Packet",
    "SafetyConfig",
    "StargateCrosschainDispatcher",
    "StargateCrosschainExecutor",
    "StargateCrosschainFactory",
    "TaxiMessage",
    "TokenTransferV1",
    "ccip_receive_calldata",
    "decode_ccip_envelope",
    "decode_command",
    "decode_envelope",
    "encode_enforced_options",
    "encode_oft_compose_msg",
    "lz_compose_calldata",
    "lz_receive_calldata",
]
