#!/usr/bin/env python3
"""Fetch funding transactions missing from our tx datasets.

For every vin prevout that spends one of our addresses, the funding tx must
exist on-chain. This script finds spend references whose funding tx is absent
from all_transactions.json (BTC) / all_ltc_transactions.json (LTC), fetches
each missing funding tx from the local mempool API (10.10.20.3:3006, falling
back to public mempool.space / litecoinspace.org), and merges them into the
datasets. Checkpointed every 25 txs; API failures are retried with backoff
and recorded, never treated as empty.

Usage: python3 fetch_missing_funding.py [btc|ltc|both]
"""

import json
import sys
import time
import urllib.request

PROJ = "/Users/agenticos/Documents/kimi/workspace/wallet-analizer"
APIS = {
    "btc": ["http://10.10.20.3:3006/api", "https://mempool.space/api"],
    "ltc": ["http://10.10.20.3:3006/api", "https://litecoinspace.org/api"],
}
TX_FILES = {"btc": "all_transactions.json", "ltc": "all_ltc_transactions.json"}
OWN_FILES = {"btc": "found_addresses_btc_bip44_external.json",
             "ltc": "found_addresses_ltc.json"}


def load_own(coin):
    with open(f"{PROJ}/{OWN_FILES[coin]}") as f:
        data = json.load(f)
    out = set()
    for e in (data.values() if isinstance(data, dict) else data):
        if isinstance(e, str):
            out.add(e)
        elif isinstance(e, dict) and e.get("address"):
            out.add(e["address"])
    return out


def load_txs(coin):
    with open(f"{PROJ}/{TX_FILES[coin]}") as f:
        raw = json.load(f)
    return raw


def fetch_tx(coin, txid):
    last_err = None
    for base in APIS[coin]:
        url = f"{base}/tx/{txid}"
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "wallet-analyzer/1.0"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    tx = json.load(r)
                if not isinstance(tx, dict) or tx.get("txid") != txid:
                    raise ValueError(f"bad payload from {base}")
                return tx
            except Exception as e:
                last_err = f"{base}: {e}"
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"all sources failed for {txid}: {last_err}")


def find_missing_funding(coin, raw, own):
    txs = raw.values() if isinstance(raw, dict) else raw
    funded = set()
    for tx in txs:
        for n, v in enumerate(tx.get("vout", [])):
            if v.get("scriptpubkey_address") in own:
                funded.add((tx["txid"], n))
    missing = {}  # funding txid -> value to us (sats), for stats
    for tx in txs:
        vins = tx.get("vin", [])
        if not any((v.get("prevout") or {}).get("scriptpubkey_address") in own for v in vins):
            continue
        for v in vins:
            prev = v.get("prevout") or {}
            if prev.get("scriptpubkey_address") in own:
                key = (v.get("txid"), v.get("vout"))
                if key not in funded:
                    missing.setdefault(v["txid"], 0)
                    missing[v["txid"]] += prev.get("value") or 0
    return missing


def run(coin):
    print(f"[{coin}] loading data...", flush=True)
    own = load_own(coin)
    raw = load_txs(coin)
    # index existing txids
    existing = set(raw.keys()) if isinstance(raw, dict) else {t["txid"] for t in raw}
    missing = find_missing_funding(coin, raw, own)
    todo = {txid: val for txid, val in missing.items() if txid not in existing}
    print(f"[{coin}] spend refs missing funding txs: {len(missing)} "
          f"({sum(missing.values()) / 1e8:.8f} {coin}), not yet in file: {len(todo)}", flush=True)
    if not todo:
        print(f"[{coin}] nothing to fetch", flush=True)
        return

    ckpt_path = f"{PROJ}/missing_funding_{coin}.json"
    try:
        with open(ckpt_path) as f:
            fetched = json.load(f)
    except FileNotFoundError:
        fetched = {}
    failed = {}
    todo = {t: v for t, v in todo.items() if t not in fetched}
    print(f"[{coin}] resume: {len(fetched)} already fetched, {len(todo)} to go", flush=True)

    t0 = time.time()
    for i, txid in enumerate(sorted(todo), 1):
        try:
            fetched[txid] = fetch_tx(coin, txid)
        except Exception as e:
            failed[txid] = str(e)
            print(f"[{coin}] FAIL {txid}: {e}", flush=True)
        if i % 25 == 0 or i == len(todo):
            with open(ckpt_path, "w") as f:
                json.dump(fetched, f)
            rate = i / max(1, time.time() - t0)
            print(f"[{coin}] {i}/{len(todo)} fetched ({rate:.1f}/s), failed {len(failed)}", flush=True)
        time.sleep(0.15)

    # merge into dataset
    print(f"[{coin}] merging {len(fetched)} txs into {TX_FILES[coin]}...", flush=True)
    if isinstance(raw, dict):
        raw.update(fetched)
        merged = raw
    else:
        seen = {t["txid"] for t in raw}
        merged = raw + [t for tid, t in fetched.items() if tid not in seen]
    with open(f"{PROJ}/{TX_FILES[coin]}", "w") as f:
        json.dump(merged, f)
    print(f"[{coin}] done. dataset now {len(merged)} txs; "
          f"merge-failures: {len(failed)}", flush=True)
    if failed:
        with open(f"{PROJ}/missing_funding_{coin}_failed.json", "w") as f:
            json.dump(failed, f, indent=1)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    coins = ["btc", "ltc"] if which == "both" else [which]
    for c in coins:
        run(c)
