from eth_abi import decode
from web3 import Web3
from web3.types import TxReceipt


def extract_events(
    receipt: TxReceipt,
    event_signature: str,
    abi_types: list[str],
    dataclass_type,
):
    sig_hash = Web3.keccak(text=event_signature)
    events = []
    for log in receipt["logs"]:
        if log["topics"][0] == sig_hash:
            decoded = decode(abi_types, log["data"])
            events.append(dataclass_type(*decoded))
    return events
