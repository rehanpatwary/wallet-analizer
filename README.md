# wallet-analizer

Forensic analysis toolkit for a **BIP39 mnemonic-derived Bitcoin + Litecoin
wallet** (legacy BIP44). Recovers every address, downloads the full
transaction history, builds a per-address ledger with USD valuation at each
transaction's actual rate, and ranks where the money went — correctly
handling shared custodial sweep transactions.

Built to be operated by humans **and AI coding agents**: see
[AGENTS.md](AGENTS.md) for the agent runbook (golden rules, pipeline,
verified analytical facts).

## Results at a glance

| | BTC | LTC |
|---|---|---|
| Addresses found | 4,083 | 4,857 |
| Total received | 5.74946810 (**$475,371**) | 6,853.86737339 (**$618,718**) |
| Network fees (our share) | 0.08126502 | 0.72448138 |
| Net sent to destinations | 5.66820308 (**$470,134**) | 6,853.14289201 (**$619,110**) |
| Current on-chain balance | **0** | **0** |

USD values are computed per-transaction at the live rate on the day it
confirmed (Binance daily open, UTC).

## Quick start

```bash
make setup        # install dependencies
make env          # creates .env — put your WALLET_MNEMONIC in it
make restore-data # decompress committed tx archives
make fix-funding  # repair any missing funding transactions
make ledger       # rebuild ledger + interactive HTML report
```

Full pipeline from a fresh clone: `make all`. Every target is also runnable
without make — see the `Makefile` or `AGENTS.md` for the raw commands.

Docker:

```bash
docker build -t wallet-analizer .
docker run -e WALLET_MNEMONIC="word1 ... word12" wallet-analizer make ledger
```

## How it works

1. **Address sweep** (`scan_all_types_accounts.py`) — derives BIP44/BIP49/
   BIP84 × accounts 0–9 × external/internal from the mnemonic; legacy
   account-0 external was the only active path (everything else verified
   empty). Gap limit 5000.
2. **History download** (`fetch_all_txs_fast.py`) — pulls every transaction
   touching any found address from a self-hosted mempool/esplora node
   (`MEMPOOL_API`, default `http://10.10.20.3:3006/api`), failing over to
   mempool.space / litecoinspace.org.
3. **Gap repair** (`fetch_missing_funding.py`) — esplora downloads can miss
   funding transactions; this reconciles spent-vs-received and backfills any
   missing funding txs automatically (checkpointed, retry-safe).
4. **Ledger + report** (`build_ledger.py`) — per-address in/out with USD at
   tx-day rates; sweep-aware vendor attribution; emits `ledger.csv`,
   `ledger.json`, `wallet_summary.json`, `vendor_destinations_usd_report.*`,
   `vendor_tx_usd.csv`, and a self-contained interactive
   `wallet_report.html` (searchable/sortable ledger, cumulative USD flow
   chart, top destinations).

## Configuration

Everything is env-driven via `wallet_config.py` / `.env` (see
`.env.example`): `WALLET_MNEMONIC`, `MEMPOOL_API`, `BINANCE_API`, `GAP_LIMIT`,
`ACCOUNT_DEPTH`. No machine-specific paths are hardcoded anywhere.

## Security notice

⚠️ The mnemonic analyzed by this project was exposed in this repository's
early commit history. **Treat this wallet as compromised**: never deposit
funds to any of its addresses again. Never commit your `.env`.

## Requirements

Python 3.10+ (no other system deps). `requirements.txt`: ecdsa, base58,
mnemonic, bech32.
