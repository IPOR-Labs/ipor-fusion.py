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
- [ ] **Runnable crosschain example.** Add an `examples/` flow that opens a
  lane, supplies assets, relays them with `CrosschainSimulator` and reads the
  settled bucket once the referenced deployment's contracts are public.

## External documentation

- [ ] Add the Python crosschain SDK surface to `llms.txt` and `llms-full.txt`
  once the crosschain contracts are public.
- [ ] Correct the crosschain overview: `CrosschainClaimFuse` pulls assets that
  have already returned to the executor; it does not initiate remote-yield
  harvesting.
