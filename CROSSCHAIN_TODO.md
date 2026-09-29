# Crosschain SDK: open items

Follow-ups from the review of the crosschain SDK, resolved one at a time.
Delete an entry in the change that resolves it; delete this file when it is empty.

## Before the crosschain contracts are public

- [ ] **Arbitrum → HyperEVM pilot deployment.** The CCIP factory at
  `0x3a745EaC243ea7563CCbD5890dbCA1b05CEe1e0D` has symmetric routes and five
  enabled 18-decimal test assets. Before requesting the HTEST dispatcher, fund
  the Arbitrum factory above the current `requestDispatcher` quote (its initial
  0.0005 ETH balance was insufficient), deploy and configure the Arbitrum hub
  vault and HyperEVM spoke vault, and deploy/register the three crosschain
  fuses on the hub. The SDK now proves this topology through a full in-simulation
  CCIP lifecycle. After the live deployment, add its addresses to the pinned
  topology and run the same shared lifecycle before the first live asset
  transfer. HyperEVM delivery must use its 30M-gas big blocks; Chainlink CCIP
  transmitters already do so. Add `HYPEREVM_PROVIDER_URL` to CI so the pinned
  rehearsal runs there.

- [ ] **Unblock USDC acceptance for Arbitrum → HyperEVM.** The shared lifecycle
  and three recovery scenarios now include native USDC (6 decimals), retaining
  HTEST (18 decimals). The USDC configuration rehearsal covers timelocked factory
  enablement, vault/executor/dispatcher deployment, real USDC/USD feeds on both
  chains, raw-unit amounts and local vault rounding. At USDC pins Arbitrum
  `509959086` / HyperEVM `47183474`, the HyperEVM → Arbitrum token lane is open,
  but Arbitrum → HyperEVM is still closed; both factories still disable USDC.
  Value-moving USDC scenarios explicitly skip on that verified token-lane blocker,
  never on arbitrary errors. After Chainlink opens the outbound lane, re-pin both
  chains, update the pinned readiness facts and require all four USDC scenarios
  to pass without skips. Execute the rehearsed factory asset governance for the
  live pilot. Neither HTEST passing nor USDC setup alone is USDC acceptance;
  simulated delivery remains distinct from live CCIP delivery verification.
  `scripts/crosschain_readiness.py` and the separate readiness workflow now
  report both token lanes, factory gates and observed block hashes/timestamps.
  Re-pin candidates are observations, not finality guarantees: re-check the
  chosen final block pair before updating acceptance pins. The report never
  changes pins or executes governance. The workflow requires both RPC secrets
  and only becomes scheduled after reaching the default branch.
  Feed age is checked only at the pinned block; the configured middleware ignores
  feed timestamps, so simulated clock advancement does not validate live freshness.

- [ ] **Pilot factory build provenance.** Record the exact source commit/build
  configuration for the deployed CCIP factories. Their `CONFIG_DELAY()` is 300
  seconds, matching the pilot document, while the local contracts branch declares
  24 hours. The rehearsal reads the deployed delay; compiling the fuses from the
  current branch does not prove the factories came from that same source.

- [ ] **Foundry compiler CI.** Add a GitHub Actions job for the Solidity-source
  deployment rehearsal: pin the Foundry version, check out the exact public
  `ipor-fusion` contracts ref with all dependencies, set
  `IPOR_FUSION_CONTRACTS_DIR`, and run the compiler/deployment test with the
  HyperEVM and Arbitrum RPC secrets. The workflow must not depend on a private
  repository path or download an unpinned compiler at test time.

- [ ] **Solidity mirrors ahead of upstream.** `IporFusionMarkets.CROSSCHAIN`
  (54), the crosschain substrate decoder and the crosschain module docstrings
  mirror contracts not yet in the public `ipor-fusion` repository. Once they
  land: bump the pinned ref in `tests/test_solidity_mirrors.py`, remove 54
  from `_AHEAD_OF_UPSTREAM`, and check every cited Solidity path resolves.
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
