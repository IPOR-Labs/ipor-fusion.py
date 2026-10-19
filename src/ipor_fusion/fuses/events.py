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
            events.append(
                dataclass_type(
                    *(
                        Web3.to_checksum_address(value) if abi == "address" else value
                        for abi, value in zip(abi_types, decoded, strict=True)
                    )
                )
            )
    return events
