# AGENT LOG — Outgoing Classification & P&L (multi-agent handoff)

Goal: classify every outgoing transaction across the 8 xpub accounts as
**self-use** (self-transfer / cash-out to own exchange account / cold storage)
vs **3rd-party** (fixed merchant payments, one-off vendors), and compute fiat
value at arrival vs at exit for profit/loss. Forwarding lookups 1–2 hops.

## Decisions (dated)

- 2026-09-27 **D1 — Source of truth**: classify from the 8 checkpoints
  `results/xpubs/.ckpt-<slug>.json` (full-history txs, already validated),
  NOT the date-filtered CSVs. Date-range filters apply only to final totals.
- 2026-09-27 **D2 — No duplication**: global dedupe by txid. If one tx touches
  two accounts it is counted once (primary = account whose inputs funded it).
- 2026-09-27 **D3 — Class taxonomy** (per outgoing tx, one of):
  - `SELF_SWEEP` — bulk consolidation from many our-addresses to one dest
  - `SELF_FORWARD` — dest receives only from us, later forwards onward
    (1–2 hop evidence); cold-storage / wallet-hop
  - `CASH_OUT` — dest forwards to high fan-in custodial/exchange pattern
  - `FIXED_MERCHANT` — same dest, similar amounts, regular intervals
  - `VENDOR_ONCE` — one-off 3rd-party payment, no recurrence
  - `VENDOR_REPEAT` — same dest 2+ times but not fixed-amount/interval
  - `UNKNOWN` — insufficient evidence (kept, never silently dropped)
- 2026-09-27 **D4 — Merchant heuristic (user rule)**: dest on legacy BTC/LTC
  address style, ≥3 payments, amount CV ≤ 0.15, interval CV ≤ 0.5 ⇒
  FIXED_MERCHANT even if dest is external.
- 2026-09-27 **D5 — Self signal**: dest whose received value comes ≥98% from
  our wallet addresses ⇒ self-candidate; then forwarding lookup decides
  SELF_FORWARD vs CASH_OUT vs SELF_SWEEP (never-spent + single incoming).
- 2026-09-27 **D6 — Fiat P&L**: USD at arrival (in-tx day rate) and USD at
  exit (out-tx day rate) both already in ckpt-derived data (Binance daily
  open). P&L per account = IN_USD − OUT_USD over full history; per class
  totals reported separately. No FIFO matching (not requested; note as
  limitation L1).
- 2026-09-27 **D7 — Hop lookup scope**: hop-1 = full tx history of each unique
  destination (BTC via local electrs 24 threads; LTC via litecoinspace 3
  workers). hop-2 only for destinations classified SELF_FORWARD or CASH_OUT
  (limit API load). Cache all raw lookups under results/classify/cache/.
- 2026-09-27 **D8 — Artifacts**:
  `results/classify/{outgoing.csv, destinations.csv, classes.json, report.html, agent_log.md}`
  plus this root log. Commit after every stage.

## Execution state

- [x] Stage 0: log + plan (this file)
- [x] Stage 1: extract outgoing txs from 8 ckpts → cache (1859 unique txs)
- [ ] Stage 2: destination profiling (count/amount/interval stats)
- [ ] Stage 3: hop lookups (fan-in + forwarding)
- [ ] Stage 4: classification + fiat P&L
- [ ] Stage 5: report.html + commit/push

## Handoff notes for the next agent

- Checkpoints are huge (btc-2023 ~966MB). Use streaming JSON parse or load
  once and cache extracted subsets; never re-fetch on failure — fall back to
  `results/classify/cache/`.
- tx JSONs in ckpts are SLIMMED: vin[].prevout = {value, scriptpubkey_address},
  vout = {value, scriptpubkey_address}, status has block_time. No scriptsig.
- The 2nd-wallet seeds' address sets per slug = ckpt `funded` lists.
- Local BTC node: http://10.10.20.3:3006/api (electrs; paginate /txs with
  after_txid cursor, pages of 10, break on <10 or no-new; timeout 60s).
- LTC: https://litecoinspace.org/api (3 workers max; it 502s in waves — retry
  with backoff).
- Known caveat carried from ledger stage: btc-2024-25 has 9 txs where segwit
  self-change (~0.339 BTC) is counted as external out (segwit xpub not
  provided). Flag these in classification (they're SELF_FORWARD by shape).
