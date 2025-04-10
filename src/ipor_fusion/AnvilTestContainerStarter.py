import logging
import os
import time
from typing import Union, List

import requests
from docker.models.containers import ExecResult
from dotenv import load_dotenv
from eth_typing import ChecksumAddress, BlockNumber
from testcontainers.core.container import DockerContainer
from web3 import Web3, HTTPProvider
from web3.types import RPCEndpoint, Timestamp

from ipor_fusion.types import Period

load_dotenv(verbose=True)


class AnvilTestContainerStarter:
    ANVIL_IMAGE = os.getenv(
        "ANVIL_TEST_CONTAINER",
        "ghcr.io/ipor-labs/foundry:nightly-aa69ed1e46dd61fbf9d73399396a4db4dd527431",
    )

    MAX_WAIT_SECONDS = 1201
    ANVIL_HTTP_PORT = 8545

    def __init__(self, fork_url, fork_block_number=None):
        self.log = logging.getLogger(__name__)
        self._docker_container = DockerContainer(self.ANVIL_IMAGE)
        self._fork_url = fork_url
        self._fork_block_number = fork_block_number
        fork_block_number_flag = ""
        if fork_block_number:
            fork_block_number_flag = f"--fork-block-number {self._fork_block_number}"
        self.anvil_command = f'"anvil --steps-tracing --auto-impersonate --host 0.0.0.0 --fork-url {self._fork_url} {fork_block_number_flag}"'
        self._docker_container.with_exposed_ports(self.ANVIL_HTTP_PORT).with_command(
            self.anvil_command
        )

    def get_anvil_http_url(self):
        return f"http://{self._docker_container.get_container_host_ip()}:{self._docker_container.get_exposed_port(self.ANVIL_HTTP_PORT)}"

    def get_anvil_wss_url(self):
        return f"wss://{self._docker_container.get_container_host_ip()}:{self._docker_container.get_exposed_port(self.ANVIL_HTTP_PORT)}"

    def get_client(self):
        http_url = self.get_anvil_http_url()
        return Web3(HTTPProvider(http_url))

    def execute_in_container(self, command: Union[str, list[str]]) -> tuple[int, bytes]:
        result = self._docker_container.exec(command)
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
        self._docker_container.start()
        self.wait_for_endpoint_ready()
        self.log.info("[CONTAINER] [ANVIL] Anvil container started")

    def reset_fork(self, block_number: BlockNumber):
        self.log.info("[CONTAINER] [ANVIL] Anvil fork reset")
        w3 = self.get_client()
        params = [
            {
                "forking": {
                    "jsonRpcUrl": self._fork_url,
                    "blockNumber": hex(block_number),
                }
            }
        ]

        w3.manager.request_blocking(RPCEndpoint("anvil_reset"), params)

        current_block_number = w3.eth.block_number
        assert (
            w3.eth.block_number == block_number
        ), f"Current block number is {current_block_number}, expected {block_number}"

        self.log.info("[CONTAINER] [ANVIL] Anvil fork reset")

    def current_block_number(self) -> BlockNumber:
        w3 = self.get_client()
        return w3.eth.block_number

    def current_block_timestamp(self) -> Timestamp:
        w3 = self.get_client()
        return w3.eth.get_block(self.current_block_number()).timestamp

    def move_time(self, delta_time_argument: Period):
        self.log.info("[CONTAINER] [ANVIL] Anvil evm increaseTime")
        w3 = self.get_client()

        w3.manager.request_blocking(
            RPCEndpoint("evm_increaseTime"), [delta_time_argument]
        )
        w3.manager.request_blocking(RPCEndpoint("evm_mine"), [])

        self.log.info("[CONTAINER] [ANVIL] Anvil evm increaseTime")

    def grant_market_substrates(
        self,
        _from: ChecksumAddress,
        plasma_vault,
        market_id: int,
        substrates: List[str],
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
