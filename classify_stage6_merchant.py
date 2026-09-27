#!/usr/bin/env python3
"""Stage 6: merchant payment wallet-depth analysis.

Cross-references the merchant's own payment list (data/merchant/merchant_txs.csv)
against BOTH wallet layers we control:
  - main mnemonic wallet (all_transactions*.json dumps, found_addresses_* paths)
  - the 8 xpub accounts (ckpt funded lists + ckpt slimmed txs with vin prevouts)

For every merchant payment, determines which of OUR addresses funded it
(vin prevouts) -> wallet depth: accounts / chains / address-index ranges used.

Pass 1: locate merchant txs in main dumps (top-level keys) and in ckpt `txs`
        dicts (windowed raw_decode, no full-file parse).
Pass 2: any merchant tx not found in either layer is fetched from the public
        API (electrs / litecoinspace) and its funding addresses checked
        against both address indexes -> finds unknown wallet layers.

Outputs:
  results/merchant/merchant_depth.json
  results/merchant/merchant_depth.csv
"""

import csv
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wallet_config import PROJ, FALLBACK_APIS

MERCH = os.path.join(PROJ, "data", "merchant", "merchant_txs.csv")
OUT = os.path.join(PROJ, "results", "merchant")
DUMPS = {"btc": os.path.join(PROJ, "all_transactions.json"),
         "ltc": os.path.join(PROJ, "all_ltc_transactions.json")}
FOUND = {"btc": os.path.join(PROJ, "found_addresses_btc_bip44_external.json"),
         "ltc": os.path.join(PROJ, "found_addresses_ltc.json")}
CKPTS = ["btc-2022", "btc-2023", "btc-2024-25", "btc-2025",
         "ltc-2023", "ltc-2023-24", "ltc-2024-25", "ltc-2025"]

DEC = json.JSONDecoder()
CACHE_FUND = os.path.join(PROJ, "results", "merchant", "cache_funded.json")
CACHE_TX = os.path.join(PROJ, "results", "merchant", "cache_tx")


def extract_top_level_txs(path, txids):
    """txids found as top-level keys -> {txid: tx} via windowed raw_decode."""
    data = open(path, "rb").read()
    out = {}
    for h in txids:
        pos = data.find(b'"' + h.encode() + b'":')
        if pos < 0:
            continue
        i = pos
        while data[i:i+1] != b"{":
            i += 1
        try:
            tx, _ = DEC.raw_decode(data[i:i + 4 * 1024 * 1024].decode(
                "utf-8", "replace"))
            out[h] = tx
        except Exception as e:
            print(f"  WARN decode {h[:12]}: {e}", flush=True)
    del data
    return out


def extract_ckpt_txs(slug, txids):
    """txids found in ckpt['txs'] dict -> {txid: slimtx} (windowed parse)."""
    p = os.path.join(PROJ, "results", "xpubs", f".ckpt-{slug}.json")
    data = open(p, "rb").read()
    out = {}
    for h in txids:
        pos = data.find(b'"' + h.encode() + b'":')
        if pos < 0:
            continue
        i = pos
        while data[i:i+1] != b"{":
            i += 1
        try:
            tx, _ = DEC.raw_decode(data[i:i + 4 * 1024 * 1024].decode(
                "utf-8", "replace"))
            out[h] = tx
        except Exception as e:
            print(f"  WARN decode {slug}/{h[:12]}: {e}", flush=True)
    del data
    return out


def load_main_paths():
    m = {}
    for coin, p in FOUND.items():
        for r in json.load(open(p)):
            m[r["address"]] = r.get("path", "")
    return m


def load_xpub_index():
    """address -> (slug, chain, index) from ckpt funded lists."""
    if os.path.exists(CACHE_FUND):
        return {k: tuple(v) for k, v in json.load(open(CACHE_FUND)).items()}
    idx = {}
    for slug in CKPTS:
        p = os.path.join(PROJ, "results", "xpubs", f".ckpt-{slug}.json")
        data = open(p, "rb").read(40 * 1024 * 1024)
        pos = data.find(b'"funded":')
        chunk = data[pos:pos + 40 * 1024 * 1024].decode("utf-8", "replace")
        lst, _ = DEC.raw_decode(chunk[chunk.find("["):])
        for e in lst:
            idx[e["address"]] = [slug, e.get("chain"), e.get("index")]
        print(f"  {slug}: {len(lst)} funded addrs", flush=True)
    json.dump(idx, open(CACHE_FUND, "w"))
    return idx


def funding_addresses(tx):
    out = []
    for v in tx.get("vin", []):
        po = v.get("prevout") or {}
        a = po.get("scriptpubkey_address")
        if a:
            out.append((a, po.get("value") or 0))
    return out


def fetch_tx_api(coin, txid):
    """Fetch a raw tx from public sources (for txs outside both layers)."""
    p = os.path.join(CACHE_TX, f"{coin}_{txid}.json")
    if os.path.exists(p):
        return json.load(open(p))
    import classify_stage23_lookup as m23
    bases = list(FALLBACK_APIS[coin])
    if coin == "ltc":
        m23._probe_ltc_primary()
        if not m23.LTC_PRIMARY_OK:
            bases = bases[1:]
    tx = None
    for base in bases:
        try:
            tx = m23._get_json(f"{base}/tx/{txid}", timeout=30, retries=2)
            break
        except Exception:
            continue
    if tx is None:
        raise RuntimeError(f"fetch failed {coin} {txid}")
    json.dump(tx, open(p, "w"))
    return tx


def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(CACHE_TX, exist_ok=True)
    merch = list(csv.DictReader(open(MERCH)))
    print(f"merchant payments: {len(merch)}", flush=True)

    main_paths = load_main_paths()
    xpub_idx = load_xpub_index()
    print(f"main labeled={len(main_paths)}  xpub indexed={len(xpub_idx)}",
          flush=True)

    by_coin = defaultdict(set)
    for r in merch:
        by_coin[r["currency"].lower()].add(r["transaction_hash"].strip())

    # ---- pass 1a: main dumps ----
    found = {}
    for coin, hashes in by_coin.items():
        got = extract_top_level_txs(DUMPS[coin], hashes)
        found.update(got)
        print(f"{coin}: {len(got)}/{len(hashes)} in main dump", flush=True)

    # ---- pass 1b: ckpts ----
    remain = {h for hs in by_coin.values() for h in hs} - set(found)
    coin_of = {}
    for r in merch:
        coin_of[r["transaction_hash"].strip()] = r["currency"].lower()
    ckpt_txs = {}
    for slug in CKPTS:
        coin = "ltc" if slug.startswith("ltc") else "btc"
        want = {h for h in remain if coin_of.get(h) == coin}
        if not want:
            continue
        got = extract_ckpt_txs(slug, want)
        ckpt_txs.update(got)
        if got:
            print(f"  ckpt {slug}: {len(got)} merchant txs", flush=True)
    print(f"ckpt total: {len(ckpt_txs)}", flush=True)

    # ---- pass 2: network fetch for the rest ----
    remain2 = remain - set(ckpt_txs)
    print(f"pass-2 api fetches needed: {len(remain2)}", flush=True)
    api_txs = {}
    from concurrent.futures import ThreadPoolExecutor
    def _one(h):
        c = coin_of[h]
        try:
            return h, fetch_tx_api(c, h)
        except Exception as e:
            print(f"  fetch FAIL {h[:16]}: {e}", flush=True)
            return h, None
    with ThreadPoolExecutor(max_workers=16) as ex:
        for h, tx in ex.map(_one, sorted(remain2)):
            if tx:
                api_txs[h] = tx
    print(f"pass-2 fetched: {len(api_txs)}", flush=True)

    # ---- label funding addresses for every merchant tx ----
    def label(a, h):
        if a in xpub_idx:
            slug, chain, idx = xpub_idx[a]
            return f"xpub:{slug}", chain, idx
        if a in main_paths:
            return "main:bip44", None, None
        return "FOREIGN", None, None

    rows, unmatched_foreign, unmatched_err = [], [], []
    depth = defaultdict(set)
    index_range = defaultdict(list)
    coin_sum = defaultdict(lambda: defaultdict(float))

    for r in merch:
        coin = r["currency"].lower()
        h = r["transaction_hash"].strip()
        if h in found:
            tx, where = found[h], "main-dump"
        elif h in ckpt_txs:
            tx, where = ckpt_txs[h], "xpub-ckpt"
        elif h in api_txs:
            tx, where = api_txs[h], "api"
        else:
            unmatched_err.append(r)
            continue
        funds = []
        for a, v in funding_addresses(tx):
            src, chain, idx = label(a, h)
            funds.append({"addr": a, "sats": v, "src": src,
                          "chain": chain, "index": idx})
            if src != "FOREIGN":
                depth[src].add(a)
                if idx is not None:
                    index_range[src].append((chain, idx))
        n_foreign = sum(1 for f in funds if f["src"] == "FOREIGN")
        n_our = len(funds) - n_foreign
        if n_our == 0:
            unmatched_foreign.append(r)
            continue
        amt = float(r["amount_btc"]) if r["amount_btc"] else 0.0
        coin_sum[coin]["txs"] += 1
        coin_sum[coin]["amount"] += amt
        coin_sum[coin]["usd"] += float(r["amount"] or 0)
        rows.append({
            "currency": coin, "txid": h, "where": where,
            "merchant_addr": r["input_address"],
            "usd": r["amount"], "amount_coin": r["amount_btc"],
            "created_at": r["created_at"],
            "n_our_inputs": n_our, "n_foreign_inputs": n_foreign,
            "funding": funds,
        })

    summary = {
        "merchant_payments_total": len(merch),
        "ours": len(rows),
        "foreign_funded": len(unmatched_foreign),
        "fetch_errors": len(unmatched_err),
        "by_coin": {c: dict(v) for c, v in coin_sum.items()},
        "distinct_funding_addresses": {k: len(v) for k, v in depth.items()},
        "index_stats": {},
        "merchant_deposit_addrs": sorted({r["input_address"] for r in merch}),
    }
    for src, lst in index_range.items():
        ext = sorted(i for c, i in lst if c == 0)
        chg = sorted(i for c, i in lst if c == 1)
        summary["index_stats"][src] = {
            "external": ({"min": ext[0], "max": ext[-1], "touches": len(ext)}
                         if ext else None),
            "change": ({"min": chg[0], "max": chg[-1], "touches": len(chg)}
                       if chg else None),
        }

    json.dump({"summary": summary, "rows": rows},
              open(os.path.join(OUT, "merchant_depth.json"), "w"), indent=1)
    with open(os.path.join(OUT, "merchant_depth.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["currency", "txid", "where", "merchant_addr",
                    "amount_coin", "usd", "created_at", "n_our_inputs",
                    "n_foreign_inputs", "funding"])
        for r in rows:
            w.writerow([r["currency"], r["txid"], r["where"],
                        r["merchant_addr"], r["amount_coin"], r["usd"],
                        r["created_at"], r["n_our_inputs"],
                        r["n_foreign_inputs"],
                        ";".join(f"{x['addr']}:{x['src']}" for x in r["funding"])])

    print(json.dumps(summary, indent=1))
    print(f"foreign-funded: {len(unmatched_foreign)}, errors: {len(unmatched_err)}")


if __name__ == "__main__":
    main()
