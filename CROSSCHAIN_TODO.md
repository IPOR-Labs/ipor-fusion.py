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

## API and efficiency

- [ ] **`erc20_balance_slot` makes up to 33 round trips** (`core/simulation.py`).
  Probe every candidate slot in one `eth_simulateV1` call, one holder per slot.

## Polish

- [ ] `StargateChain` defaults `local_decimals` and `shared_decimals` to 6;
  make them required like `CcipChain.local_decimals`.
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
