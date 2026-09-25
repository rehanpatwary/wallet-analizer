#!/usr/bin/env python3
"""Preseed xpub_ledger checkpoints for OUR seed's accounts (btc-2024-25,
ltc-2024-25) from the local full-history dumps (all_transactions.json /
all_ltc_transactions.json), so the ledger run skips re-fetching them.

BTC: external funded addresses come from results/main/
found_addresses_btc_bip44_external.json; the change chain is derived
watch-only from the account xpub (chain 1, indices 0..6001) and intersected
with recipient addresses seen in the dump (a change address's first funding
always originates from a tx spending an external address, hence is present
in the dump). Histories for those change addresses are then fetched from
the local Umbrel node to catch change-only spends that the dump cannot
contain (txs spending change without touching any external address).

LTC: both chains come from results/main/found_addresses_ltc.json paths
(m/44'/2'/0'/<chain>/<i>); histories are taken from the LTC dump as-is.

Txs are slimmed to the fields xpub_ledger.analyze_xpub reads.

Writes results/xpubs/.ckpt-<slug>.json with funded/scanned/txs/fetched so
the running xpub_ledger.py skips scan+fetch entirely for these two entries.
Must finish BEFORE the background run reaches those entries.
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wallet_config import P, PROJ
from wallet_derive import addresses_from_xpub, xpub_to_watchkey
from xpub_ledger import fetch_address_txs

OUT_DIR = os.path.join(PROJ, "results", "xpubs")

BTC_XPUB = "xpub6DLFCJ5Dg9DRF1vUgEATheBGxAqqJ99CHR6DzKCyzoCgXw1v55STMkDWns5ybgM2uGbaMei7kpnvMRKLqysmq3paxChwAvTvuG5LcotZ7UD"
LTC_CHANGE_DEPTH = 6002  # ext max index 5564 + margin


def slim_tx(tx):
    return {
        "txid": tx["txid"],
        "status": {k: (tx.get("status") or {}).get(k)
                   for k in ("confirmed", "block_height", "block_time")},
        "vin": [{"is_coinbase": v.get("is_coinbase", False),
                 "prevout": ({k: (v.get("prevout") or {}).get(k)
                              for k in ("value", "scriptpubkey_address")}
                             if v.get("prevout") else None)}
                for v in tx.get("vin", [])],
        "vout": [{k: v.get(k) for k in ("value", "scriptpubkey_address")}
                 for v in tx.get("vout", [])],
    }


def load_slim_dump(path):
    print(f"loading {os.path.basename(path)}...", flush=True)
    raw = json.load(open(path))
    items = raw.items() if isinstance(raw, dict) else ((t["txid"], t) for t in raw)
    txs, recipients = {}, set()
    for txid, tx in items:
        txs[txid] = slim_tx(tx)
        for v in tx.get("vout", []):
            a = v.get("scriptpubkey_address")
            if a:
                recipients.add(a)
    print(f"  {len(txs)} txs, {len(recipients)} recipient addresses", flush=True)
    return txs, recipients


def preseed_btc():
    slug = "btc-2024-25"
    txs, recipients = load_slim_dump(os.path.join(PROJ, "all_transactions.json"))

    ext = json.load(open(P("found_addresses_btc_bip44_external.json")))
    funded = [{"address": f["address"], "index": f["index"], "chain": 0}
              for f in ext]
    print(f"  external funded from file: {len(funded)}", flush=True)

    node, net, script = xpub_to_watchkey(BTC_XPUB)
    change = addresses_from_xpub(node, script, "btc", 1, LTC_CHANGE_DEPTH, 0)
    change_funded_set = {c["address"] for c in change if c["address"] in recipients}
    change_funded = [c["address"] for c in change
                     if c["address"] in change_funded_set]
    print(f"  change derived: {len(change)}, funded (seen in dump): "
          f"{len(change_funded)}", flush=True)

    own = {f["address"] for f in funded}
    to_fetch = [a for a in change_funded if a not in own]
    t0, done_n = time.time(), 0
    with ThreadPoolExecutor(max_workers=12) as ex:  # keep some node headroom
        futs = {ex.submit(fetch_address_txs, "btc", a): a for a in to_fetch}
        for fut in as_completed(futs):
            addr = futs[fut]
            try:
                for tx in fut.result():
                    txs[tx["txid"]] = slim_tx(tx)
            except Exception as e:
                print(f"  WARN change fetch failed {addr}: {e}", flush=True)
            done_n += 1
            if done_n % 200 == 0:
                rate = done_n / (time.time() - t0)
                print(f"  change histories {done_n}/{len(to_fetch)} "
                      f"({rate:.1f}/s)", flush=True)

    funded += [{"address": c["address"], "index": c["index"], "chain": 1}
               for c in change if c["address"] in change_funded_set]
    ckpt = {
        "funded": funded,
        "scanned": [{"chain": 0, "n": 5565 + 501}, {"chain": 1, "n": LTC_CHANGE_DEPTH}],
        "txs": txs,
        "fetched": sorted({f["address"] for f in funded}),
    }
    path = os.path.join(OUT_DIR, f".ckpt-{slug}.json")
    json.dump(ckpt, open(path, "w"))
    print(f"  wrote {path}: {len(funded)} funded, {len(txs)} txs", flush=True)


def preseed_ltc():
    slug = "ltc-2024-25"
    txs, _ = load_slim_dump(os.path.join(PROJ, "all_ltc_transactions.json"))

    found = json.load(open(P("found_addresses_ltc.json")))
    funded, max_idx = [], {0: 0, 1: 0}
    for f in found:
        parts = f["path"].split("/")
        if parts[3] != "0'":  # only account 0 (this xpub)
            continue
        chain = int(parts[4])
        funded.append({"address": f["address"], "index": f["index"], "chain": chain})
        max_idx[chain] = max(max_idx[chain], f["index"])
    print(f"  ltc funded: {len(funded)} (ext max {max_idx[0]}, int max {max_idx[1]})",
          flush=True)

    ckpt = {
        "funded": funded,
        "scanned": [{"chain": 0, "n": max_idx[0] + 1}, {"chain": 1, "n": max_idx[1] + 1}],
        "txs": txs,
        "fetched": sorted({f["address"] for f in funded}),
    }
    path = os.path.join(OUT_DIR, f".ckpt-{slug}.json")
    json.dump(ckpt, open(path, "w"))
    print(f"  wrote {path}: {len(funded)} funded, {len(txs)} txs", flush=True)


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    preseed_btc()
    preseed_ltc()
    print("preseed done", flush=True)
