#!/usr/bin/env python3
"""Stage 1: extract every outgoing transaction from the 8 xpub checkpoints.

Outgoing = tx spends at least one funded (our) address. For each such tx we
record: slug, txid, block_time, coin, our input value (sats), external
outputs (dest addresses + values), fee. Global dedupe by txid (D2).

Output: results/classify/cache/outgoing_raw.json
  [{slug, txid, time, coin, our_in, ext_outs: [[addr, sats]...], fee}]
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wallet_config import PROJ

OUT_DIR = os.path.join(PROJ, "results", "classify", "cache")
SLUGS = ["btc-2022", "btc-2023", "btc-2024-25", "btc-2025",
         "ltc-2023", "ltc-2023-24", "ltc-2024-25", "ltc-2025"]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    seen = set()
    out_rows = []
    for slug in SLUGS:
        path = os.path.join(PROJ, "results", "xpubs", f".ckpt-{slug}.json")
        print(f"[{slug}] loading ckpt...", flush=True)
        ck = json.load(open(path))
        own = {f["address"] for f in ck["funded"]}
        coin = "ltc" if slug.startswith("ltc") else "btc"
        n_out = 0
        for txid, tx in ck["txs"].items():
            if txid in seen:
                continue
            our_in = 0
            total_in = 0
            for vin in tx.get("vin", []):
                prev = vin.get("prevout") or {}
                v = prev.get("value") or 0
                total_in += v
                if prev.get("scriptpubkey_address") in own:
                    our_in += v
            if our_in == 0:
                continue  # incoming-only tx
            seen.add(txid)
            ext = []
            change_like = 0
            for v in tx.get("vout", []):
                a, val = v.get("scriptpubkey_address"), v.get("value") or 0
                if not a:
                    continue
                if a in own:
                    change_like += val
                else:
                    ext.append([a, val])
            fee = max(0, total_in - sum(x[1] for x in ext) - change_like)
            out_rows.append({
                "slug": slug, "txid": txid, "coin": coin,
                "time": (tx.get("status") or {}).get("block_time"),
                "our_in": our_in, "ext_outs": ext, "fee": fee,
                "to_self_change": change_like,
            })
            n_out += 1
        print(f"[{slug}] outgoing txs: {n_out} (cumulative {len(out_rows)})",
              flush=True)
    dst = os.path.join(OUT_DIR, "outgoing_raw.json")
    json.dump(out_rows, open(dst, "w"))
    print(f"wrote {dst}: {len(out_rows)} unique outgoing txs", flush=True)


if __name__ == "__main__":
    main()
