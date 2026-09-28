# Crosschain SDK: open items

Follow-ups from the review of the crosschain SDK, resolved one at a time.
Delete an entry in the change that resolves it; delete this file when it is empty.

## Before the crosschain contracts are public

- [ ] **Solidity mirrors ahead of upstream.** `IporFusionMarkets.CROSSCHAIN`
  (54), the crosschain substrate decoder and the crosschain module docstrings
  mirror contracts not yet in the public `ipor-fusion` repository. Once they
  land: bump the pinned ref in `tests/test_solidity_mirrors.py`, remove 54
  from `_AHEAD_OF_UPSTREAM`, and check every cited Solidity path resolves.
- [ ] **Readiness tests in the default suite.** `tests/test_crosschain_readiness.py`
  reads live, unpinned state and marks HyperEVM checks `xfail(strict=True)`,
  so an external change (CCIP opening USDC to HyperEVM) fails every CI run on
  XPASS. When the lane opens, drop the marks that XPASS; then move the module
  behind its own marker, run it in a scheduled or manual job only, and keep it
  out of the default `pytest` run.
- [ ] **CCIP cancel receipt visibility.** Expose a public getter for
  `cancelRequested(commandId)` on the CCIP executor. Until then the SDK cannot
  distinguish a parked failed command from one whose cancellation receipt is
  in flight, so one attestation approval may race and revert.

## Correctness

- [ ] **Shallow override merge in `VaultSimulator.run()`** (`core/simulation.py`).
  Folding an empty block's overrides replaces the whole `stateDiff` of an
  address, so two `with_erc20_balance` calls on one token in different blocks
  keep only the last holder. Merge `stateDiff` key-wise; reject `state` and
  `stateDiff` on the same address, which `eth_simulateV1` refuses.
- [ ] **Non-canonical EXECUTOR grants pass silently.** An EXECUTOR substrate
  with a non-zero chain id is never matched by the fuses, yet
  `_decode_crosschain` (`substrates.py`) hides the chain slot and
  `discover_deployment` accepts it. Render the slot loudly and raise in
  discovery. The generic encoder stays a faithful mirror of Solidity;
  `CrosschainSubstrateLib.executor_substrate` is the safe constructor.
- [ ] **Envelope decoders accept `NONE`** (`crosschain/messages.py`).
  `decode_envelope` and `decode_ccip_envelope` must reject type 0 and types
  above the last member, as `decodeEnvelope` and `CcipMessages.decode` do.
- [ ] **`ccip_token_lane` assumes the OnRamp 2.x layout** (`crosschain/ccip/chainlink.py`).
  `CcipOnRamp.token_admin_registry` decodes `getStaticConfig()` as the 2.x
  tuple without checking `typeAndVersion`. Gate on the version prefix and
  raise a clear error for unsupported OnRamps.

## API and efficiency

- [ ] **`erc20_balance_slot` makes up to 33 round trips** (`core/simulation.py`).
  Probe every candidate slot in one `eth_simulateV1` call, one holder per slot.
- [ ] **CCIP `propose_balance` shape** (`crosschain/ccip/contracts.py`).
  Take a `BalanceObservation` like the Stargate wrapper and declare the
  `uint256` proposal id it returns; `CcipLane.propose_balance` then stops
  rebuilding the `Call`.
- [ ] **Re-decoding a `Call` by hand** (`stargate/lane.py`, `ccip/lane.py`).
  Five copies of `Call(to=..., data=..., output_types=..., decoder=..., ctx=...)`;
  replace with one helper in `core/contract.py`.

## Polish

- [ ] `SYNTHETIC_TOKEN_SOURCE` (`crosschain/transport.py`): write a 40-digit
  literal instead of slicing a 41-digit one.
- [ ] Remove the unused `checksum()` helper from `crosschain/transport.py`.
- [ ] Remove `StargateLane.__init__`, which only forwards to `super()`.
- [ ] `discover_lane_fuses`: keep failing on two fuses of one role, but name
  both addresses in the error.
- [ ] `crosschain/discovery.py` docstring: spokes are the chains with a
  REMOTE_VAULT grant whose `hasDispatcher` is true, not every dispatcher.
- [ ] Shipped docstrings that mention "the mainnet POC" (`StargateLane`
  default options, `crosschain_market_id`): describe the values without
  referring to a deployment users cannot see.

## External documentation

- [ ] Add the Python crosschain SDK surface to `llms.txt` and `llms-full.txt`
  once the crosschain contracts are public.
- [ ] Correct the crosschain overview: `CrosschainClaimFuse` pulls assets that
  have already returned to the executor; it does not initiate remote-yield
  harvesting.
