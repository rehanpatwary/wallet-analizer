#!/usr/bin/env python3
"""Stage 2+3: per-destination lookup + forwarding evidence.

For every unique destination address in outgoing_raw.json:
  - /address/:dest  -> tx_count, funded/spent sums, current balance
  - /txs (paginated, cursor) -> full history, cached to
    results/classify/cache/dest/<addr>.json

From each cached history we later compute (stage 4):
  - fan_in_share: fraction of received value whose tx spends one of OUR
    wallet addresses (global own-set = union of all 8 slugs' funded addrs)
  - forwarding: dest's own spends -> hop-1 target addresses, and whether
    those targets are (a) also exclusive-ours, (b) high fan-in custodial.

Resume-safe: cached dests are skipped.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wallet_config import PROJ, FALLBACK_APIS

CACHE = os.path.join(PROJ, "results", "classify", "cache", "dest")
OWN_FILE = os.path.join(PROJ, "results", "classify", "cache", "own_addresses.json")

SLUGS = ["btc-2022", "btc-2023", "btc-2024-25", "btc-2025",
         "ltc-2023", "ltc-2023-24", "ltc-2024-25", "ltc-2025"]


def _get_json(url, timeout=90, retries=2):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "wallet-analizer/3.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                raise
            last = e
            time.sleep(1.5 * (attempt + 1))
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET failed {url}: {last}")


def fetch_addr_history(coin, address):
    """Return {info, txs} — txs is full paginated history (slim)."""
    for base in FALLBACK_APIS[coin]:
        try:
            info = _get_json(f"{base}/address/{address}")
            txs, seen, cursor = {}, set(), None
            while True:
                url = f"{base}/address/{address}/txs"
                if cursor:
                    url += f"?after_txid={cursor}"
                page = _get_json(url)
                new = 0
                for tx in page:
                    if tx["txid"] not in seen:
                        seen.add(tx["txid"])
                        txs[tx["txid"]] = tx
                        new += 1
                if len(page) < 10 or new == 0:
                    break
                cursor = page[-1]["txid"]
                time.sleep(0.02)
            return {"info": info, "txs": list(txs.values())}
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                continue
            continue
        except Exception:
            continue
    raise RuntimeError(f"no source returned history for {address}")


def build_own_set():
    if os.path.exists(OWN_FILE):
        return set(json.load(open(OWN_FILE)))
    own = set()
    for slug in SLUGS:
        ck = json.load(open(os.path.join(PROJ, "results", "xpubs", f".ckpt-{slug}.json")))
        own |= {f["address"] for f in ck["funded"]}
        print(f"  own-set += {slug}: {len(own)}", flush=True)
    json.dump(sorted(own), open(OWN_FILE, "w"))
    return own


def main():
    os.makedirs(CACHE, exist_ok=True)
    rows = json.load(open(os.path.join(PROJ, "results", "classify", "cache", "outgoing_raw.json")))
    dests = sorted({a for r in rows for a, _ in r["ext_outs"]})
    print(f"{len(dests)} unique destinations", flush=True)
    build_own_set()

    todo = {"btc": [], "ltc": []}
    coin_of = {}
    for r in rows:
        for a, _ in r["ext_outs"]:
            coin_of[a] = r["coin"]  # source tx's coin is authoritative
    for a in dests:
        p = os.path.join(CACHE, f"{a}.json")
        if not os.path.exists(p):
            todo[coin_of[a]].append(a)
    print(f"to fetch: btc={len(todo['btc'])} ltc={len(todo['ltc'])}", flush=True)

    stats = {"ok": 0, "fail": 0}
    def work(coin, addr):
        try:
            data = fetch_addr_history(coin, addr)
            slim = []
            for tx in data["txs"]:
                slim.append({
                    "txid": tx["txid"],
                    "time": (tx.get("status") or {}).get("block_time"),
                    "vin": [{"a": (v.get("prevout") or {}).get("scriptpubkey_address"),
                             "v": (v.get("prevout") or {}).get("value") or 0}
                            for v in tx.get("vin", [])],
                    "vout": [{"a": v.get("scriptpubkey_address"),
                              "v": v.get("value") or 0}
                             for v in tx.get("vout", [])],
                })
            json.dump({"info": data["info"], "txs": slim},
                      open(os.path.join(CACHE, f"{addr}.json"), "w"))
            return True
        except Exception as e:
            print(f"  FAIL {addr}: {e}", flush=True)
            return False

    for coin, workers in (("btc", 24), ("ltc", 3)):
        if not todo[coin]:
            continue
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(work, coin, a) for a in todo[coin]]
            for i, fut in enumerate(as_completed(futs), 1):
                stats["ok" if fut.result() else "fail"] += 1
                if i % 100 == 0:
                    rate = i / (time.time() - t0)
                    print(f"  [{coin}] {i}/{len(futs)} ({rate:.1f}/s)", flush=True)
    print(f"done: ok={stats['ok']} fail={stats['fail']}", flush=True)


if __name__ == "__main__":
    main()
