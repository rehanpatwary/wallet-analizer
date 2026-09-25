"""Shared configuration for the wallet-analizer pipeline.

Import this from every script instead of hardcoding paths, mnemonics, or
endpoints. All values can be overridden via environment variables or a
project-root .env file (see .env.example). Never commit your real .env.
"""

import os
from pathlib import Path

PROJ = str(Path(__file__).resolve().parent)

# --- secrets ---------------------------------------------------------------
# The BIP39 mnemonic of the wallet under analysis. NEVER hardcode it in
# scripts and NEVER commit it. Provide via WALLET_MNEMONIC env var or .env.
MNEMONIC = os.environ.get("WALLET_MNEMONIC", "")

# --- data sources ----------------------------------------------------------
# Local mempool/esplora instance (self-hosted). Public fallbacks are tried
# automatically by the fetch scripts when this is unreachable.
MEMPOOL_API = os.environ.get("MEMPOOL_API", "http://10.10.20.3:3006/api")
FALLBACK_APIS = {
    "btc": [MEMPOOL_API, "https://mempool.space/api"],
    "ltc": [MEMPOOL_API, "https://litecoinspace.org/api"],
}

# Historical USD rates (daily klines). Override if you prefer another venue.
BINANCE_API = os.environ.get("BINANCE_API", "https://api.binance.com")
BTC_PAIR = os.environ.get("BTC_PAIR", "BTCUSDT")
LTC_PAIR = os.environ.get("LTC_PAIR", "LTCUSDT")

# --- derivation defaults ---------------------------------------------------
GAP_LIMIT = int(os.environ.get("GAP_LIMIT", "5000"))   # user rule: 5000
ACCOUNT_DEPTH = int(os.environ.get("ACCOUNT_DEPTH", "10"))  # accounts 0..9


# --- per-wallet working directory ------------------------------------------
# All pipeline scripts read/write their data files inside WALLET_DIR.
# run_wallets.py sets this to results/<wallet-name> per wallet; running any
# script directly defaults WALLET_DIR to the project root (legacy layout).
WALLET_DIR = os.environ.get("WALLET_DIR", PROJ)


def P(filename: str) -> str:
    """Resolve a data filename inside the active WALLET_DIR."""
    return os.path.join(WALLET_DIR, filename)


def require_mnemonic():
    """Fail fast with a clear message instead of deriving from an empty seed."""
    if not MNEMONIC.strip():
        raise SystemExit(
            "WALLET_MNEMONIC is not set. Copy .env.example to .env and fill it in, "
            "or export WALLET_MNEMONIC='word1 word2 ... word12'. "
            "Never hardcode the mnemonic in source files."
        )
    return MNEMONIC.strip()
