#!/usr/bin/env python3
"""Retry the per-address fetch failures logged as WARN in xpub_ledger.log,
merge recovered txs into the owning slug's checkpoint, so a re-run of
xpub_ledger.py picks them up (analysis-only, fetches are all cached).
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from xpub_ledger import fetch_address_txs
from wallet_config import PROJ

OUT_DIR = os.path.join(PROJ, "results", "xpubs")

LTC_SLUGS = ["ltc-2023", "ltc-2023-24", "ltc-2024-25", "ltc-2025"]


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


def main():
    warns = sorted(set(re.findall(r"WARN fetch failed (\S+):",
                                  open(os.path.join(PROJ, "xpub_ledger.log")).read())))
    print(f"{len(warns)} failed addresses")
    for slug in LTC_SLUGS:
        path = os.path.join(OUT_DIR, f".ckpt-{slug}.json")
        ck = json.load(open(path))
        own = {f["address"] for f in ck["funded"]}
        missing = [a for a in warns if a in own]
        if not missing:
            print(f"[{slug}] none of the failures belong here", flush=True)
            continue
        got = 0
        for a in missing:
            try:
                txs = fetch_address_txs("ltc", a)
            except Exception as e:
                print(f"[{slug}] STILL FAILING {a}: {e}", flush=True)
                continue
            for tx in txs:
                ck["txs"][tx["txid"]] = slim_tx(tx)
            ck.setdefault("fetched", []).append(a)
            got += 1
            print(f"[{slug}] recovered {a} ({len(txs)} txs)", flush=True)
        json.dump(ck, open(path, "w"))
        print(f"[{slug}] recovered {got}/{len(missing)}; ckpt txs={len(ck['txs'])}",
              flush=True)


if __name__ == "__main__":
    main()
