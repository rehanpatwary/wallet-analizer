# AGENTS.md — operating instructions for coding agents

This file is the runbook for any AI coding agent (Claude Code, Cursor, Aider,
OpenHands, Kimi, Copilot, …) working in this repository. Read it fully before
running anything.

## What this project is

A forensic analysis toolkit for a **BIP39 mnemonic-derived BTC + LTC wallet**
(legacy BIP44, account 0 external chain was the only active path). It:

1. Derives addresses from the mnemonic and sweeps address types/accounts.
2. Downloads the full transaction history from a self-hosted mempool/esplora
   node (public fallbacks: mempool.space, litecoinspace.org).
3. Builds a per-address ledger (in/out, valued in USD at the live rate on
   each transaction day, Binance daily open).
4. Ranks external (vendor) destinations, correctly attributing **shared
   custodial sweep transactions** (100–300 inputs from many depositors →
   single output) to only our net contribution.
5. Produces a self-contained interactive report (`wallet_report.html`).

## Golden rules (violating these wastes hours)

1. **Never treat an API/server failure as an empty result.** Retry with
   backoff, fail over to the fallback source, and verify each response is a
   valid payload before trusting it. If a source cannot be verified, say so.
2. **Verify data completeness with reconciliation.** The wallet's total
   received must equal total spent when balance is 0. If
   `sum(spent prevouts) > sum(received vouts)`, funding transactions are
   missing — run `make fix-funding`. Esplora `vout` objects have **no
   positional index field**; compute indexes with `enumerate()` — never
   match vin `vout` against `vout["n"]` (the key does not exist).
3. **Commit and push after every meaningful change** (`git push origin main`).
4. **Never hardcode machine paths or the mnemonic.** Import from
   `wallet_config` (`PROJ`, `MNEMONIC`). The mnemonic comes from the
   `WALLET_MNEMONIC` env var or a gitignored `.env` (see `.env.example`).
5. Run everything from the project root (`make` targets handle this).

## Quick start (fresh clone)

```bash
make setup        # pip install
make env          # creates .env — fill in WALLET_MNEMONIC
make restore-data # decompress committed tx archives
make fix-funding  # fetch any funding txs missing from the dumps
make ledger       # rebuild ledger.json / ledger.csv + wallet_report.html
```

`make all` runs the whole chain. `make scan` + `make fetch-txs` re-derive
addresses and re-download history from scratch (slow; needs the mnemonic).

## Pipeline stages

| Stage | Script | Inputs → Outputs |
|---|---|---|
| 1. Address sweep | `scan_all_types_accounts.py` | mnemonic → `found_addresses_*.json`, `scan_all_types_*.json` |
| 2. Tx download | `fetch_all_txs_fast.py` | found addresses → `all_transactions.json` (BTC), `all_ltc_transactions.json` (LTC) |
| 2b. Gap repair | `fetch_missing_funding.py` | tx dumps (+ APIs) → merges missing funding txs into the dumps |
| 3. Ledger + report | `build_ledger.py` | tx dumps → `ledger.json`, `ledger.csv`, `wallet_summary.json`, `vendor_destinations_usd_report.*`, `vendor_tx_usd.csv`, `wallet_report.html` |
| 0–3. Multi-wallet | `run_wallets.py` (`make wallets`) | `wallets.json` → `results/<name>/` per wallet + `results/index.html` |

Stages 0–3 run per wallet with `WALLET_DIR=results/<name>`; secrets (mnemonic
or account xpub) are referenced by env var NAME in `wallets.json` — never
inline. `wallet_derive.py` supports mnemonic seeds and watch-only account
xpubs (xpub/ypub/zpub, tpub/upub/vpub, Ltub/Mtub); derivation was verified
against live chain funded-status for BTC and LTC.

Supporting one-off/diagnostic scripts: `usd_valuation.py`,
`vendor_destinations.py`, `btc_electrum_scan.py` (Electrum-server fallback
sweep), `ltc_*.py` (LTC source failovers), `tx_classifier.py`.

## Configuration (`wallet_config.py`, all overridable by env)

| Env var | Default | Purpose |
|---|---|---|
| `WALLET_MNEMONIC` | — | BIP39 seed. Required by stages 1–2 only. |
| `MEMPOOL_API` | `http://10.10.20.3:3006/api` | local esplora/mempool node |
| `BINANCE_API` | `https://api.binance.com` | historical USD rates |
| `GAP_LIMIT` | `5000` | consecutive unused addresses before abandoning a chain |
| `ACCOUNT_DEPTH` | `10` | accounts 0..9 × external+internal × address type |

## Data layout

- Raw tx dumps are >100 MB each and gitignored; their `.gz` archives ARE
  committed — keep them in sync after any dump change (`gzip -kf`).
- `found_addresses_btc_bip44_external.json` (4,083) / `found_addresses_ltc.json`
  (4,857): every funded legacy address discovered. Only BIP44 account 0
  external was ever used; all other types/accounts verified empty.
- Ledger rows: `coin, address, n_in, in, in_usd, n_out, out, out_usd, first, last`.

## Known analytical facts (verified, do not re-derive)

- BTC: received = spent = **5.74946810**, current on-chain balance **0**;
  47 of 137 withdrawals are shared custodial sweeps; fees (our share)
  0.08126502; net to destinations 5.66820308 (≈ $470k at tx-day rates).
- LTC: received = spent = **6,853.86737339**; fees 0.72448138; net to
  destinations 6,853.14289201 (≈ $619k). No sweep txs; no self-withdrawals.
- Top LTC destinations (standing deposit addresses): `LYaQTr1…9h7G` (49 txs,
  $74.8k), `LeVXYda…MQM4Cq` (31 txs, $45.6k), `LgZGiNo…S6jis` (39 txs, $43.8k).
- BTC destinations are 1-tx-each bc1q addresses — a forwarding/processor
  pattern, not a single vendor wallet.

## Security

- The analyzed wallet is **compromised by design of this repo's history**
  (the mnemonic was committed in early versions). Never send funds to any
  address of this wallet again; treat the seed as public.
- `.env` is gitignored. Never print the mnemonic to logs or chat output.
