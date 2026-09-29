# HyperCore (market 55) — open items

Delete an entry in the change that resolves it; delete this file when it is empty.

## Verified so far

- The 47-transaction HyperEVM mainnet run (11 creates, the market 47 -> 54
  migration, one long and one short xyz:NVDA cycle) is pinned in
  `tests/fixtures/hypercore_hip3_flow.json`; `tests/test_hypercore_flow.py`
  rebuilds every call from typed SDK inputs and compares it with the on-chain
  calldata. Deposit, `sendAsset`, order and cancel-by-cloid encoders, the six
  substrate encoders and the decoder are therefore verified against real
  transactions.

## Gaps

- [ ] **Market id: HyperCore is 55, crosschain keeps 54** (contracts-team
  decision, 2026-09-29). The SDK mirrors 55 ahead of upstream. The first
  HyperEVM test vault (`0x41C4…05C8`, live on mainnet) still runs on 54 until
  it is redeployed. **Known limitation until then:** `vault info` and the MCP
  tools label that vault's market 54 as crosschain once the crosschain SDK
  lands, and cannot type its substrates; this branch alone shows it as
  `no_decoder`. Do not read either output as that vault's HyperCore state.
  When the contracts land: bump the mirror-test pinned ref, drop `HYPERCORE`
  from `_AHEAD_OF_UPSTREAM`, and update downstream consumers (registry,
  monitoring) that keyed on 54.
- [ ] **No simulation of the HyperCore leg.** The HyperEVM node's
  `eth_simulateV1` does not execute the HyperCore precompiles (`0x800`–`0x813`
  fail "gas exhausted during precompiled contract execution"), so
  `VaultSimulator` cannot run market-54 actions; historical `eth_call` cannot
  either (no historical Core state), so past transactions cannot be replayed.
  Only `eth_call` / `eth_estimateGas` at the latest block runs the fuses,
  which is the dry-run the live run's own pipeline used. Add an opt-in live
  dry-run test that `eth_call`s the long cycle's EVM half from the signer on
  the current state, and re-probe `eth_simulateV1` when the node is upgraded.
- [ ] **Encoders never exercised on-chain.** `HyperCoreMarginFuse.enter`,
  `HyperCoreBuilderFeeFuse.enter`, `HyperCoreSendFuse.spot_send` and
  `HyperCoreCancelFuse.cancel_by_oid` are verified against the Solidity structs
  only; the live run never called them.
- [ ] **Settlement reporter / REPORTED mode not modeled.** The settlement fuse
  is callable only by the reporter contract; the SDK has no reader for the
  reporter (observers, report digest) and no `Report` type.
- [ ] **Pre-hook governance wrappers missing.** `setPreHookImplementations`,
  `getPreHookSelectors`, `getPreHookImplementation` exist on the public
  contracts but not in `PlasmaVault`; the migration's tx 73 is a strict xfail
  in the parity test until they do.
- [ ] **Readers.** `HyperCorePendingReader` (`pendingState`, `isPending`), the
  precompile reads (`spotBalance`, `l1BlockNumber`, `accountMarginSummary(dex,
  user)`, `position2`, `perpAssetInfo`, `tokenInfo`, `coreUserExists`) and the
  NAV identity (spot + Σ dex `accountValue`) as `Call`s; then `vault info` /
  MCP fields for pending state, settlement mode and action nonce, with the
  model mirror.
- [ ] **Events.** Decoders for `HyperCoreActionEnqueued`, `HyperCoreActionSettled`
  and the fuse events (first parameter is the fuse address as `version`), so
  the parity test can also assert the receipt shape.
- [ ] **Address book.** `ipor-abi` `mainnet-hyperevm-fusion/addresses.json`
  carries none of the HyperCore fuse, hook, reporter or reader addresses;
  `ipor.io/llms.txt` has no HyperCore / market 54 page and no market-id list.
- [ ] **Composed crosschain -> HyperCore spoke rehearsal** (after the crosschain
  SDK lands): the dispatcher needs `WHITELIST_ROLE` on the spoke vault;
  DEPOSIT and REDEEM commands revert while a HyperCore action is pending;
  `redeem` pays only from EVM USDC (market 54 has no instant-withdraw fuse),
  so the spoke must unwind Core before the hub recalls; `maxRedeem` /
  `maxWithdraw` ignore all of that; the hub's attestation band bounds spoke
  equity swings relative to principal (`N·|Δ| ≤ 0.2·P`).
