#!/usr/bin/env python3
"""Detailed transaction ledgers for a list of account xpubs.

For each entry in xpubs.json: derive external+internal chain addresses
(watch-only, public CKD), gap-scan for funded addresses, download full
transaction history from esplora sources (BTC: local Umbrel mempool node,
fallback mempool.space; LTC: litecoinspace.org), filter to the entry's
date range, value every transaction in USD at the live rate on its day,
and emit:

  results/xpubs/<slug>-txs.csv        one row per transaction (detail ledger)
  results/xpubs/<slug>-addresses.csv  per-address in/out summary
  results/xpubs/<slug>-summary.json   totals + reconciliation
  results/xpubs/index.html            combined report

Run from the project root. Checkpointed: re-running resumes the current
xpub's address scan / tx fetch.
"""

import csv
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict

from wallet_config import (BINANCE_API, BTC_PAIR, FALLBACK_APIS, LTC_PAIR,
                           P, PROJ, WALLET_DIR)
from wallet_derive import (addresses_from_xpub, scan_chain_funded,
                           xpub_to_watchkey)

OUT_DIR = os.path.join(PROJ, "results", "xpubs")
DAY = 86_400_000
USD = "__USD__"

XPUBS_FILE = os.path.join(PROJ, "xpubs.json")


def _get_json(url, timeout=25, retries=4):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "wallet-analizer/2.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            # 4xx (e.g. 404 on electrs without /txs/chain) will never succeed
            # on retry — fail fast so we can fail over to the next source.
            if 400 <= e.code < 500:
                raise
            last = e
            time.sleep(1.2 * (attempt + 1))
        except Exception as e:
            last = e
            time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"GET failed {url}: {last}")


def fetch_address_txs(coin, address):
    """All confirmed + mempool txs for an address, esplora/electrs format.

    CRITICAL: never use single-shot /txs for history — electrs truncates to
    the 10 most recent txs and esplora to 25. We page backwards with a
    cursor (after_txid works on electrs AND esplora-family APIs) until a
    short or empty page. Note: esplora's last_seen pagination can skip txs
    that share a block with the cursor (dense consolidation blocks), so the
    local electrs node is preferred; its pages are small (10) and complete.
    """
    for base in FALLBACK_APIS[coin]:
        try:
            txs, seen, cursor = {}, set(), None
            while True:
                url = f"{base}/address/{address}/txs"
                if cursor:
                    url += f"?after_txid={cursor}"
                page = _get_json(url, timeout=60)
                new = 0
                for tx in page:
                    if tx["txid"] not in seen:
                        seen.add(tx["txid"])
                        txs[tx["txid"]] = tx
                        new += 1
                if len(page) < 10 or new == 0:
                    break
                cursor = page[-1]["txid"]
                time.sleep(0.03)
            if txs:
                return list(txs.values())
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                continue  # endpoint/address not served by this source — next
            continue
        except Exception:
            continue  # fail over to next source
    raise RuntimeError(f"no source returned txs for {address}")


def fetch_daily_prices(symbol, start_ts, end_ts):
    prices = {}
    start_ms, end_ms = int(start_ts) * 1000, int(end_ts) * 1000
    while start_ms <= end_ms:
        url = (f"{BINANCE_API}/api/v3/klines?symbol={symbol}&interval=1d&limit=1000"
               f"&startTime={start_ms}&endTime={end_ms}")
        rows = _get_json(url)
        if not rows:
            break
        for row in rows:
            d = datetime.datetime.fromtimestamp(row[0] / 1000, datetime.UTC).strftime("%Y-%m-%d")
            prices[d] = float(row[1])
        start_ms = rows[-1][0] + DAY
        if len(rows) < 1000:
            break
        time.sleep(0.4)
    return prices


def day_str(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime("%Y-%m-%d") if ts else ""


def ts(date_str, end_of_day=False):
    dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    if end_of_day:
        dt += datetime.timedelta(days=1)
    return int(dt.replace(tzinfo=datetime.UTC).timestamp())


def analyze_xpub(entry, prices):
    coin = entry["coin"]
    network = "ltc" if coin == "ltc" else "btc"
    node, net, script = xpub_to_watchkey(entry["xpub"])
    script = entry.get("script", script)
    t_from, t_to = ts(entry["from"]), ts(entry["to"], end_of_day=True)

    slug = entry["slug"]
    ckpt_path = os.path.join(OUT_DIR, f".ckpt-{slug}.json")
    ckpt = {"funded": [], "scanned": [], "txs": {}}
    if os.path.exists(ckpt_path):
        ckpt = json.load(open(ckpt_path))

    # ---- gap scan both chains ----
    if not ckpt["funded"]:
        for chain in (0, 1):
            def gen(start, count, c=chain):
                return addresses_from_xpub(node, script, network, c, count, start)
            funded, scanned, hit_gap = scan_chain_funded(coin, gen, gap=500)
            for f in funded:
                f["chain"] = chain
            ckpt["funded"].extend(funded)
            ckpt["scanned"].append({"chain": chain, "n": scanned})
            print(f"  [{slug}] chain {chain}: scanned={scanned} funded={len(funded)}",
                  flush=True)
            json.dump(ckpt, open(ckpt_path, "w"))

    own = {f["address"] for f in ckpt["funded"]}
    print(f"  [{slug}] total funded addresses: {len(own)}", flush=True)

    # ---- fetch txs (parallel; local node handles concurrency, public
    #      LTC source stays at 3 workers to be polite) ----
    from concurrent.futures import ThreadPoolExecutor, as_completed
    done = set(ckpt.get("fetched", []))
    pending = [f["address"] for f in ckpt["funded"] if f["address"] not in done]
    workers = 24 if coin == "btc" else 3
    completed = 0
    t_fetch = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_address_txs, coin, a): a for a in pending}
        for fut in as_completed(futs):
            addr = futs[fut]
            try:
                for tx in fut.result():
                    ckpt["txs"][tx["txid"]] = tx
                ckpt.setdefault("fetched", []).append(addr)
            except Exception as e:
                print(f"  [{slug}] WARN fetch failed {addr}: {e}", flush=True)
            completed += 1
            if completed % 200 == 0:
                json.dump(ckpt, open(ckpt_path, "w"))
                rate = completed / max(1, time.time() - t_fetch)
                eta = (len(pending) - completed) / max(rate, 0.01) / 60
                print(f"  [{slug}] fetched txs for {completed}/{len(pending)} "
                      f"addresses ({len(ckpt['txs'])} txs, {rate:.1f}/s, "
                      f"eta {eta:.0f}m)", flush=True)
    ckpt["fetched"] = sorted(set(ckpt.get("fetched", [])))
    json.dump(ckpt, open(ckpt_path, "w"))
    txs = list(ckpt["txs"].values())
    print(f"  [{slug}] total txs fetched: {len(txs)}", flush=True)

    # ---- analyze within date range ----
    rows, addr_stats = [], {}
    totals = defaultdict(float)
    for tx in txs:
        bt = (tx.get("status") or {}).get("block_time")
        if not bt or not (t_from <= bt < t_to):
            continue
        price = prices.get(day_str(bt))
        vin_addrs, our_in, total_in = [], 0, 0
        for vin in tx.get("vin", []):
            prev = vin.get("prevout") or {}
            a, v = prev.get("scriptpubkey_address"), prev.get("value") or 0
            total_in += v
            if a in own:
                our_in += v
                vin_addrs.append(a)
        ext_out, change_out = [], 0
        for v in tx.get("vout", []):
            a, val = v.get("scriptpubkey_address"), v.get("value") or 0
            if a in own:
                change_out += val
            elif a:
                ext_out.append((a, val))
        is_ours = our_in > 0
        direction = "out" if is_ours else "in"
        if is_ours:
            fee = max(0, total_in - sum(v for _, v in ext_out) - change_out)
            # shared sweep guard: attribute only up to our inputs
            gross = sum(v for _, v in ext_out)
            amt_sats = min(our_in, gross) if gross else 0
            counter = ";".join(sorted({a for a, _ in ext_out})) or "(none)"
        else:
            fee = 0
            amt_sats = sum(v for _, v in ext_out if False) or 0
            # incoming: sum vouts to us
            amt_sats = 0
            for v in tx.get("vout", []):
                if v.get("scriptpubkey_address") in own:
                    amt_sats += v.get("value") or 0
            counter = ";".join(sorted({(vin.get("prevout") or {}).get("scriptpubkey_address")
                                       for vin in tx.get("vin", [])}
                                      - {None})) or "(coinbase)"
        amt = amt_sats / 1e8
        usd = amt * price if price else None
        fee_c = fee / 1e8
        fee_u = fee_c * price if price else None
        rows.append({
            "date": day_str(bt), "txid": tx["txid"], "direction": direction,
            "counterparty": counter[:500], "amount": round(amt, 8),
            "usd": round(usd, 2) if usd is not None else "",
            "fee": round(fee_c, 8), "fee_usd": round(fee_u, 2) if fee_u is not None else "",
        })
        totals[f"{direction}_coin"] += amt
        totals[f"{direction}_usd"] += usd or 0
        totals["fee_coin"] += fee_c
        totals["fee_usd"] += fee_u or 0
        for a in vin_addrs:
            st = addr_stats.setdefault(a, defaultdict(float))
            st["out"] += 0  # spent recorded below per prevout value
        if is_ours:
            for vin in tx.get("vin", []):
                prev = vin.get("prevout") or {}
                a = prev.get("scriptpubkey_address")
                if a in own:
                    st = addr_stats.setdefault(a, defaultdict(float))
                    v = (prev.get("value") or 0) / 1e8
                    st["out"] += v
                    st["out_usd"] += v * price if price else 0
        else:
            for v in tx.get("vout", []):
                a = v.get("scriptpubkey_address")
                if a in own:
                    st = addr_stats.setdefault(a, defaultdict(float))
                    val = (v.get("value") or 0) / 1e8
                    st["in"] += val
                    st["in_usd"] += val * price if price else 0

    rows.sort(key=lambda r: (r["date"], r["txid"]))
    return slug, rows, addr_stats, dict(totals), len(own), len(txs)


def write_outputs(slug, coin, entry, rows, addr_stats, totals, n_addr, n_txs):
    with open(os.path.join(OUT_DIR, f"{slug}-txs.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "txid", "direction", "counterparty", f"amount_{coin}",
                    "amount_usd", f"fee_{coin}", "fee_usd"])
        for r in rows:
            w.writerow([r["date"], r["txid"], r["direction"], r["counterparty"],
                        r["amount"], r["usd"], r["fee"], r["fee_usd"]])
    with open(os.path.join(OUT_DIR, f"{slug}-addresses.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["address", "total_in", "total_in_usd", "total_out", "total_out_usd"])
        for a, st in sorted(addr_stats.items(), key=lambda kv: -kv[1].get("out_usd", 0)):
            w.writerow([a, round(st.get("in", 0), 8), round(st.get("in_usd", 0), 2),
                        round(st.get("out", 0), 8), round(st.get("out_usd", 0), 2)])
    summary = {
        "slug": slug, "coin": coin, "range": [entry["from"], entry["to"]],
        "funded_addresses": n_addr, "txs_in_range": len(rows),
        "total_in": round(totals.get("in_coin", 0), 8),
        "total_in_usd": round(totals.get("in_usd", 0), 2),
        "total_out": round(totals.get("out_coin", 0), 8),
        "total_out_usd": round(totals.get("out_usd", 0), 2),
        "fees": round(totals.get("fee_coin", 0), 8),
        "fees_usd": round(totals.get("fee_usd", 0), 2),
    }
    json.dump(summary, open(os.path.join(OUT_DIR, f"{slug}-summary.json"), "w"), indent=1)
    print(f"  [{slug}] IN {summary['total_in']} (${summary['total_in_usd']:,.2f})  "
          f"OUT {summary['total_out']} (${summary['total_out_usd']:,.2f})  "
          f"fees ${summary['fees_usd']:,.2f}  txs={len(rows)}", flush=True)


def write_index(summaries):
    rows = "".join(
        f"<tr><td>{s['slug']}</td><td>{s['coin'].upper()}</td><td>{s['range'][0]} → {s['range'][1]}</td>"
        f"<td>{s['funded_addresses']:,}</td><td>{s['txs_in_range']:,}</td>"
        f"<td>{s['total_in']:,.8f}</td><td>${s['total_in_usd']:,.2f}</td>"
        f"<td>{s['total_out']:,.8f}</td><td>${s['total_out_usd']:,.2f}</td>"
        f"<td>${s['fees_usd']:,.2f}</td>"
        f"<td><a href='{s['slug']}-txs.csv'>txs</a> · <a href='{s['slug']}-addresses.csv'>addresses</a></td></tr>"
        for s in summaries)
    page = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>xpub ledgers</title>
<style>body{{background:#0d1117;color:#e6edf3;font:13px/1.5 -apple-system,sans-serif;padding:28px;max-width:1300px;margin:auto}}
h1{{font-size:19px}} p{{color:#8b949e;font-size:12px}}
table{{border-collapse:collapse;width:100%;margin-top:16px;background:#161b22;border:1px solid #30363d}}
th,td{{padding:6px 10px;text-align:right;border-bottom:1px solid #30363d;white-space:nowrap}}
th:first-child,td:first-child{{text-align:left}} th{{background:#1c2128;color:#8b949e;font-size:11px}}
a{{color:#58a6ff}}</style></head><body>
<h1>XPUB Account Ledgers</h1>
<p>{len(summaries)} account xpubs · USD valued at Binance daily open on each transaction day · generated {datetime.datetime.now():%Y-%m-%d %H:%M}</p>
<table><tr><th>Account</th><th>Coin</th><th>Date range</th><th>Funded addr</th><th>Txs</th><th>In</th><th>In USD</th><th>Out</th><th>Out USD</th><th>Fees USD</th><th>Files</th></tr>
{rows}</table></body></html>"""
    with open(os.path.join(OUT_DIR, "index.html"), "w") as f:
        f.write(page)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    entries = json.load(open(XPUBS_FILE))["xpubs"]
    lo = min(ts(e["from"]) for e in entries) - 86400
    hi = max(ts(e["to"], end_of_day=True) for e in entries) + 86400
    print(f"Fetching USD rates {day_str(lo)} .. {day_str(hi)}...", flush=True)
    prices_btc = fetch_daily_prices(BTC_PAIR, lo, hi)
    prices_ltc = fetch_daily_prices(LTC_PAIR, lo, hi)
    print(f"  BTC {len(prices_btc)} days, LTC {len(prices_ltc)} days", flush=True)

    summaries = []
    for entry in entries:
        print(f"[{entry['slug']}] {entry['coin']} {entry['from']}..{entry['to']}", flush=True)
        prices = prices_btc if entry["coin"] == "btc" else prices_ltc
        slug, rows, addr_stats, totals, n_addr, n_txs = analyze_xpub(entry, prices)
        write_outputs(slug, entry["coin"], entry, rows, addr_stats, totals, n_addr, n_txs)
        summaries.append(json.load(open(os.path.join(OUT_DIR, f"{slug}-summary.json"))))
        json.dump(summaries, open(os.path.join(OUT_DIR, "summaries.json"), "w"), indent=1)
    write_index(summaries)
    print("results/xpubs/index.html written", flush=True)


if __name__ == "__main__":
    sys.exit(main())
