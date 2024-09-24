import logging
import os
import time
from typing import Union
from dotenv import load_dotenv

from testcontainers.core.container import DockerContainer
from web3 import Web3, HTTPProvider

load_dotenv(verbose=True)

class AnvilTestContainerStarter:
  ARBITRUM_PROVIDER_URL = "ARBITRUM_PROVIDER_URL"
  ANVIL_CONTAINER = os.getenv("ANVIL_TEST_CONTAINER",
                              "ghcr.io/foundry-rs/foundry:nightly-be451fb93a0d0ec52152fb67cc6c36cd8fbd7ae1")
  FORK_URL = os.getenv(ARBITRUM_PROVIDER_URL)
  if not FORK_URL:
    raise ValueError("Environment variable ARBITRUM_PROVIDER_URL must be set")
  MAX_WAIT_SECONDS = 1201
  ANVIL_HTTP_PORT = 8545
  CHAIN_ID = 42161
  FORK_BLOCK_NUMBER = 250690377
  ANVIL_COMMAND_FORMAT = f"\"anvil --steps-tracing --auto-impersonate --host 0.0.0.0 --fork-url {FORK_URL} --fork-block-number {FORK_BLOCK_NUMBER}\""

  def __init__(self):
    self.log = logging.getLogger(__name__)
    self.anvil = DockerContainer(self.ANVIL_CONTAINER)
    self.anvil.with_exposed_ports(self.ANVIL_HTTP_PORT).with_command(self.ANVIL_COMMAND_FORMAT)

  def get_anvil_http_url(self):
    return f"http://{self.anvil.get_container_host_ip()}:{self.anvil.get_exposed_port(self.ANVIL_HTTP_PORT)}"

  def get_chain_id(self):
    return self.CHAIN_ID

  def get_client(self):
    http_url = self.get_anvil_http_url()
    return Web3(HTTPProvider(http_url))

  def execute_in_container(self, command: Union[str, list[str]]) -> tuple[int, bytes]:
    result = self.anvil.exec(command)
    if result.exit_code != 0:
      self.log.error(f"Error while executing command in container: {result}")
      raise RuntimeError("Error while executing command in container")

  def start(self):
    self.log.info("[CONTAINER] [ANVIL] Anvil container is starting")
    self.anvil.start()
    time.sleep(3)
    self.log.info("[CONTAINER] [ANVIL] Anvil container started")

  def reset_fork(self, block_number: int):
    self.log.info("[CONTAINER] [ANVIL] Anvil fork reset")
    w3 = self.get_client()
    params = [{"forking": {"jsonRpcUrl": self.FORK_URL, "blockNumber": hex(block_number)}}]

    w3.manager.request_blocking("anvil_reset", params)

    current_block_number = w3.eth.block_number
    assert w3.eth.block_number == block_number, f"Current block number is {current_block_number}, expected {block_number}"

    self.log.info("[CONTAINER] [ANVIL] Anvil fork reset")
