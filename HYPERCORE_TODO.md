# HyperCore (market 55) — open items

Delete an entry in the change that resolves it; delete this file when it is empty.

## Where the HyperEVM HIP-3 flow stands in this SDK

The reference is the 47-transaction HyperEVM mainnet run pinned in
`tests/fixtures/hypercore_hip3_flow.json` (11 creates, the market 47 -> 54
migration, one long and one short xyz:NVDA cycle). "Works" below means proven
by a test in this repository, not by that run.

| Layer | Status | Evidence |
|---|---|---|
| SDK expresses the run's calls: fuse wiring, migration (pre-hooks included), approve, deposit, EVM -> Core, spot -> HIP-3 dex, IOC/GTC orders, cancel, reduce-only close, dex -> spot, Core -> EVM, balance refresh, redeem | 36 of 36 calls | `tests/test_hypercore_flow.py`: byte parity with the on-chain calldata |
| Fuses run on the live node with the HyperCore precompiles | one probe | `eth_call` of the run's DepositFuse `execute` from the signer on the current state succeeded |
| Dry-runs of individual, currently valid actions at the latest state | not yet | opt-in `eth_call` / `eth_estimateGas` test (below); independent calls carry no earlier EVM state and cannot settle Core actions, so this never proves a step-by-step cycle |
| Sequence simulation (`VaultSimulator`, `eth_simulateV1`) | not possible on this node | the precompiles fail inside `eth_simulateV1`; no historical Core state, so no replay of past transactions either |
| State reads (pending action, action nonce, precompile NAV), events, `vault info` / MCP | missing | reader items below |
| Send pipeline (plan with fingerprint, calldata review, throwaway keystore, pinned-nonce send, settle window, `/info` verification) | outside the SDK | the run's tooling; not re-homed here (see "Beyond the SDK") |
| An end-to-end run executed from this SDK | not done | needs a funded signer and an explicit go; asynchronous Core effects (fills, spot credits) are only visible through Hyperliquid `/info` |

## SDK — PR 2, in progress

- [ ] **Readers.** `HyperCorePendingReader` (`pendingState`, `isPending`), the
  precompile reads as `Call`s (`spotBalance`, `l1BlockNumber`,
  `accountMarginSummary(uint32 dex, address user)`, `position2`,
  `perpAssetInfo`, `tokenInfo`, `coreUserExists`), and the NAV identity
  (granted spot tokens at oracle price + Σ enabled-dex `accountValue`).
- [ ] **Live dry-run test (opt-in).** `eth_call` + `eth_estimateGas` of the
  individual actions of the long cycle that are valid on the current state,
  from the signer on the latest block, skipping without
  `HYPEREVM_PROVIDER_URL`; never `eth_simulateV1` for market 55. Each call
  is independent (no earlier EVM state, no Core settlement), so a revert
  caused by the vault's current pending state is not an SDK regression, and
  the full sequence stays a live-run gap. Re-probe `eth_simulateV1` when the
  node is upgraded and record the result here.
- [ ] **`vault info` / MCP.** Market 55 label, decoded substrates, pending
  state, settlement mode, action nonce, HIP-3 dex/asset labels; `models.py`
  and the `_full_vault_info_dict` fixture mirror.
- [ ] **Events.** `HyperCoreActionEnqueued`, `HyperCoreActionSettled` and the
  fuse events (first parameter is the fuse address as `version`), so the
  parity test can also assert the receipt shape.
- [ ] **Encoders never exercised on-chain.** `HyperCoreMarginFuse.enter`,
  `HyperCoreBuilderFeeFuse.enter`, `HyperCoreSendFuse.spot_send`,
  `HyperCoreCancelFuse.cancel_by_oid` are verified against the Solidity
  structs only; the run never called them. Add them to the live dry-run once
  a vault grants what they need.
- [ ] **Settlement reporter / REPORTED mode.** The settlement fuse is callable
  only by the reporter contract; the SDK has no reader for the reporter
  (observers, report digest, `Report` type) and no REPORTED-mode rehearsal.
- [ ] **Market id: HyperCore is 55, crosschain keeps 54** (contracts-team
  decision, 2026-09-29). The SDK mirrors 55 ahead of upstream. The market-55
  fuse set for HyperEVM is prepared on the contracts side (deployment only;
  the vault migrates 54 → 55 in a separate step, the same wiring sequence the
  parity fixture covers) but was not broadcast as of 2026-10-01; once it is,
  pin the real addresses here and in a fixture with `MARKET_ID() == 55`
  checks, and point the live dry-run at the fuses the vault actually
  whitelists. The first HyperEVM test vault (`0x41C4…05C8`, live on mainnet)
  still runs on 54 until that migration. **Known limitation until then:** once the crosschain SDK
  lands, `vault info` and the MCP tools label that vault's market 54 as
  crosschain and the crosschain decoder **mis-types** its SpotToken and
  PerpMarket words as plausible `EXECUTOR` / `REMOTE_VAULT` rows while the
  other tags come back raw; this branch alone shows the market as
  `no_decoder`. Do not read either output as that vault's HyperCore state.
  When the contracts land: bump the mirror-test pinned ref, drop `HYPERCORE`
  from `_AHEAD_OF_UPSTREAM`, and update downstream consumers (registry,
  monitoring) that keyed on 54.
- [ ] **Address book and docs.** `ipor-abi` `mainnet-hyperevm-fusion/addresses.json`
  carries none of the HyperCore fuse, hook, reporter or reader addresses;
  `ipor.io/llms.txt` has no HyperCore / market 55 page and no market-id list.

## Beyond the SDK (decisions pending)

- [ ] **Send pipeline.** Whether the plan / decode / real-node dry-run /
  verify stages become a `fusion hypercore …` CLI (signing and keystores stay
  outside), or live elsewhere. Until decided, the SDK offers encoders,
  `Call.calldata`, `estimate_gas` and the readers above.
- [ ] **A run executed from this SDK.** Funded signer, amounts, gas caps,
  abort conditions, `/info` checks after every Core action, and an explicit
  go; only this closes the "works end to end" column above.

## Composed crosschain -> HyperCore spoke (after the crosschain SDK lands)

- [ ] Rehearse the hub lifecycle with a HyperCore PlasmaVault as the remote
  vault: the dispatcher needs `WHITELIST_ROLE` on the spoke; DEPOSIT and
  REDEEM commands revert while a HyperCore action is pending; `redeem` pays
  only from EVM USDC (market 55 has no instant-withdraw fuse), so the spoke
  unwinds Core before the hub recalls; `maxRedeem` / `maxWithdraw` ignore
  all of that; the hub's attestation band bounds spoke equity swings relative
  to principal (`N·|Δ| ≤ 0.2·P`), so HIP-3 notional must stay small against
  the deposited principal.
- [ ] USDC on the CCIP lane and both pilot factories is an external gate;
  test assets cannot exercise the HyperCore leg (Core token 0 is USDC).
