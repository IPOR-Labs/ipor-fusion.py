# HyperCore (market 55) — open items

Delete an entry in the change that resolves it; delete this file when it is empty.

## Where the HyperEVM HIP-3 flow stands in this SDK

The historical reference is the 47-transaction HyperEVM mainnet run pinned in
`tests/fixtures/hypercore_hip3_flow.json` (11 creates, an old market-id
change, one long and one short xyz:NVDA cycle). Its vault is abandoned. The
market-55 reporter, settlement fuse and pending hook broadcast for that vault
remain bound to it and cannot serve the new vault. "Works" below means proven
by a test in this repository, not by that run.

| Layer | Status | Evidence |
|---|---|---|
| SDK expresses the historical run's calls: fuse wiring, old market-id change (pre-hooks included), approve, deposit, EVM -> Core, spot -> HIP-3 dex, IOC/GTC orders, cancel, reduce-only close, dex -> spot, Core -> EVM, balance refresh, redeem | 36 of 36 calls | `tests/test_hypercore_flow.py`: byte parity with the on-chain calldata; this does not prescribe a migration for the new vault |
| Market-55 deployment identity | 12 contracts | `tests/fixtures/hypercore_market55.json` and `tests/test_hypercore_deployment.py`: code, fuse market id/version, creation transactions, deployer and the three vault-bound contracts at block 47732052 |
| Fuses run on the live node with the HyperCore precompiles | old vault only, market 54 | `tests/test_hypercore_live.py` is opt-in and checks the historical vault's current state. Independent `eth_call` / `eth_estimateGas` calls carry no earlier EVM state and cannot settle Core actions, so this never proves a step-by-step cycle or a market-55 vault |
| Sequence simulation (`VaultSimulator`, `eth_simulateV1`) | not possible on this node | the precompiles fail inside `eth_simulateV1`; no historical Core state, so no replay of past transactions either |
| State reads: pending action and nonce (`HyperCorePendingReader`), the precompiles (`HyperCoreReader`), the NAV identity (`read_hypercore_nav` == `PlasmaVault.balance_fuse_value`) | offline done; market-55 path pending | `tests/test_hypercore_readers.py` offline; the old vault's live identity at block 47453068 is historical evidence only |
| `vault info` / MCP: market 55 label, decoded substrates with HIP-3 coordinates, NAV legs vs the balance fuse, pending state, perp markets with coin names | done | `hypercore` block (`HyperCoreSection`), `tests/test_cli_hypercore.py`, `tests/test_mcp_hypercore.py`; HyperEVM only, vaults with a market-55 balance fuse |
| Events: `HyperCoreActionEnqueued`/`Settled`, the fuse events, the settlement fuse's and the reporter's | done | `fuses/hypercore_events.py`; `tests/test_hypercore_events.py` pins the topics to the run's receipts and the per-action receipt shape |
| Send pipeline (plan with fingerprint, calldata review, throwaway keystore, pinned-nonce send, settle window, `/info` verification) | outside the SDK | the run's tooling; not re-homed here (see "Beyond the SDK") |
| An end-to-end run executed from this SDK | not done | needs a funded signer and an explicit go; asynchronous Core effects (fills, spot credits) are only visible through Hyperliquid `/info` |

## SDK — PR 2, in progress

- [ ] **`eth_simulateV1` for market 55.** Re-probe when the node is upgraded
  and record the result here; until then the live test stays `eth_call`-only.
- [ ] **Encoders never exercised on-chain.** `HyperCoreMarginFuse.enter`,
  `HyperCoreBuilderFeeFuse.enter`, `HyperCoreSendFuse.spot_send`,
  `HyperCoreCancelFuse.cancel_by_oid` are verified against the Solidity
  structs only; the run never called them. Add them to the live dry-run once
  a vault grants what they need.
- [ ] **Settlement reporter / REPORTED mode.** The settlement fuse is callable
  only by the reporter contract; the SDK has no reader for the reporter
  (observers, report digest, `Report` type) and no REPORTED-mode rehearsal.
- [ ] **New market-55 vault.** The 12 broadcast addresses and creation
  transactions are pinned in `tests/fixtures/hypercore_market55.json`. Reuse
  the seven market fuses, capital-flow hook and pending reader. The broadcast
  settlement reporter, its settlement fuse and pending-action hook are bound
  to the abandoned market-54 vault and cannot be reused. Fill the fixture's
  `new_vault_deployment` slots with the new vault and its three bound
  contracts after the deployment, then point the opt-in live dry-run at the
  new vault and its granted fuses/substrates. No live deployment is authorized
  by this SDK change. Once the contracts land in the pinned Solidity mirror
  revision, remove `HYPERCORE` from `_AHEAD_OF_UPSTREAM`.
- [ ] **Address book and docs.** `ipor-abi` `mainnet-hyperevm-fusion/addresses.json`
  does not yet publish these HyperCore fuse, hook, reporter or reader addresses;
  `ipor.io/llms.txt` has no HyperCore / market 55 page and no market-id list.

## Beyond the SDK (decisions pending)

- [ ] **Send pipeline.** Whether the plan / decode / real-node dry-run /
  verify stages become a `fusion hypercore …` CLI (signing and keystores stay
  outside), or live elsewhere. Until decided, the SDK offers encoders,
  `Call.calldata`, `estimate_gas` and the readers above.
- [ ] **A run executed from this SDK.** Funded signer, amounts, gas caps,
  abort conditions, `/info` checks after every Core action, and an explicit
  go; only this closes the "works end to end" column above.

## Composed crosschain -> HyperCore spoke

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
