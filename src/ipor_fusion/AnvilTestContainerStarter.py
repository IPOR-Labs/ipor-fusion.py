import logging
import os
import time
from typing import Union, List

from docker.models.containers import ExecResult
from dotenv import load_dotenv
from testcontainers.core.container import DockerContainer
from web3 import Web3, HTTPProvider
from web3.types import RPCEndpoint
import requests

load_dotenv(verbose=True)


class AnvilTestContainerStarter:
    ARBITRUM_PROVIDER_URL = "ARBITRUM_PROVIDER_URL"
    ANVIL_CONTAINER = os.getenv(
        "ANVIL_TEST_CONTAINER",
        "ghcr.io/foundry-rs/foundry:nightly-be451fb93a0d0ec52152fb67cc6c36cd8fbd7ae1",
    )

    MAX_WAIT_SECONDS = 1201
    ANVIL_HTTP_PORT = 8545
    CHAIN_ID = 42161
    FORK_BLOCK_NUMBER = 250690377

    def __init__(self):
        self.log = logging.getLogger(__name__)
        self.anvil = DockerContainer(self.ANVIL_CONTAINER)
        self.FORK_URL = os.getenv(self.ARBITRUM_PROVIDER_URL)
        if not self.FORK_URL:
            raise ValueError("Environment variable ARBITRUM_PROVIDER_URL must be set")
        self.ANVIL_COMMAND_FORMAT = f'"anvil --steps-tracing --auto-impersonate --host 0.0.0.0 --fork-url {self.FORK_URL} --fork-block-number {self.FORK_BLOCK_NUMBER}"'
        self.anvil.with_exposed_ports(self.ANVIL_HTTP_PORT).with_command(
            self.ANVIL_COMMAND_FORMAT
        )

    def get_anvil_http_url(self):
        return f"http://{self.anvil.get_container_host_ip()}:{self.anvil.get_exposed_port(self.ANVIL_HTTP_PORT)}"

    def get_anvil_wss_url(self):
        return f"wss://{self.anvil.get_container_host_ip()}:{self.anvil.get_exposed_port(self.ANVIL_HTTP_PORT)}"

    def get_chain_id(self):
        return self.CHAIN_ID

    def get_client(self):
        http_url = self.get_anvil_http_url()
        return Web3(HTTPProvider(http_url))

    def execute_in_container(self, command: Union[str, list[str]]) -> tuple[int, bytes]:
        result = self.anvil.exec(command)
        if isinstance(result, ExecResult) and result.exit_code != 0:
            self.log.error("Error while executing command in container: %s", result)
            raise RuntimeError("Error while executing command in container")
        return result

    def wait_for_endpoint_ready(self, timeout: int = 60) -> None:
        """Wait until the Anvil HTTP endpoint is ready."""
        start_time = time.time()
        while True:
            try:
                web3 = Web3(HTTPProvider(self.get_anvil_http_url()))
                block_number = web3.eth.block_number
                if block_number > 0:
                    self.log.info("[CONTAINER] [ANVIL] Anvil endpoint is ready")
                    return
            except requests.ConnectionError:
                pass  # Ignore connection errors and keep trying

            if time.time() - start_time > timeout:
                raise TimeoutError("Anvil endpoint did not become ready in time")
            time.sleep(1)  # Wait before retrying

    def start(self):
        self.log.info("[CONTAINER] [ANVIL] Anvil container is starting")
        self.anvil.start()
        self.wait_for_endpoint_ready()
        self.log.info("[CONTAINER] [ANVIL] Anvil container started")

    def reset_fork(self, block_number: int):
        self.log.info("[CONTAINER] [ANVIL] Anvil fork reset")
        w3 = self.get_client()
        params = [
            {"forking": {"jsonRpcUrl": self.FORK_URL, "blockNumber": hex(block_number)}}
        ]

        w3.manager.request_blocking(RPCEndpoint("anvil_reset"), params)

        current_block_number = w3.eth.block_number
        assert (
            w3.eth.block_number == block_number
        ), f"Current block number is {current_block_number}, expected {block_number}"

        self.log.info("[CONTAINER] [ANVIL] Anvil fork reset")

    def move_time(self, delta_time_argument: int):
        self.log.info("[CONTAINER] [ANVIL] Anvil evm increaseTime")
        w3 = self.get_client()

        w3.manager.request_blocking(
            RPCEndpoint("evm_increaseTime"), [delta_time_argument]
        )
        w3.manager.request_blocking(RPCEndpoint("evm_mine"), [])

        self.log.info("[CONTAINER] [ANVIL] Anvil evm increaseTime")

    def grant_market_substrates(
        self, _from: str, plasma_vault, market_id: int, substrates: List[str]
    ):
        join = ",".join(substrates)
        cmd = [
            "cast",
            "send",
            "--unlocked",
            f"--from {_from}",
            f"{plasma_vault}",
            '"grantMarketSubstrates(uint256,bytes32[])"',
            f"{market_id}",
            f'"[{join}]"',
        ]
        oneline = " ".join(cmd)
        self.execute_in_container(oneline)
