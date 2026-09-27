#!/usr/bin/env python3
"""Stage 4: classify every outgoing transaction SELF vs 3rd-party + fiat P&L.

Inputs:
  results/classify/cache/outgoing_raw.json   (1859 outgoing txs, ext outputs)
  results/classify/cache/dest/<addr>.json    (1262 dest histories + info)
  results/classify/cache/own_addresses.json  (global own-address set)

Per-destination signals:
  fan_in      = our_total_to_dest / chain_stats.funded_txo_sum
  n_ours      = how many of our txs paid this dest
  amount_cv   = coefficient of variation of our payment amounts
  interval_cv = CV of gaps between our payments (sorted by time)
  spends      = does the dest later spend (vin contains dest / spent_refs)?
  hop1        = first-level forwarding targets where derivable

Class rules (priority order):
  fan_in >= 0.98            -> self-candidate:
      never spends          -> SELF_SWEEP (cold storage / receive-only)
      spends, hop1 all own  -> SELF_FORWARD depth-2 (chained self wallets)
      spends, else          -> SELF_FORWARD (own wallet hop)
  fan_in 0.50..0.98         -> MIXED_CUSTODIAL (likely exchange deposit shared
                               with other users' funds) -> cash-out side
  count>=3 & amount_cv<=0.15 & interval_cv<=0.5 -> FIXED_MERCHANT
  count >= 2                -> VENDOR_REPEAT
  else                      -> VENDOR_ONCE
Missing funded_txo_sum      -> UNKNOWN (never silently dropped)

Hop-2 (bounded): for SELF_FORWARD dests, hop1 targets not in cache and not
own are looked up (BTC: local electrs; LTC: litecoinspace -> blockcypher)
to verify whether the chain ends in another self wallet or a custodial
address. Results cached to cache/hop/<addr>.json.

Outputs:
  results/classify/destinations.csv
  results/classify/outgoing.csv          (per-tx class + USD at exit)
  results/classify/hops.csv              (forwarding chains)
  results/classify/classes.json          (totals per class, fiat P&L)
"""

import csv
import datetime
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wallet_config import FALLBACK_APIS, PROJ
from xpub_ledger import fetch_daily_prices, ts as xts, day_str

CACHE = os.path.join(PROJ, "results", "classify", "cache")
DEST = os.path.join(CACHE, "dest")
HOP = os.path.join(CACHE, "hop")
OUT = os.path.join(PROJ, "results", "classify")

SELF_THRESHOLD = 0.98
MIXED_THRESHOLD = 0.50


def cv(xs):
    if len(xs) < 2:
        return 0.0
    m = statistics.fmean(xs)
    return (statistics.pstdev(xs) / m) if m else 0.0


def load_inputs():
    rows = json.load(open(os.path.join(CACHE, "outgoing_raw.json")))
    own = set(json.load(open(os.path.join(CACHE, "own_addresses.json"))))
    dests = {}
    for fn in os.listdir(DEST):
        a = fn[:-5]
        try:
            dests[a] = json.load(open(os.path.join(DEST, fn)))
        except Exception:
            dests[a] = None
    return rows, own, dests


def dest_signals(addr, cache, our_pays):
    """our_pays: [(time, value_sats), ...] for this dest."""
    n = len(our_pays)
    vals = [v for _, v in our_pays]
    times = sorted(t for t, _ in our_pays if t)
    gaps = [b - a for a, b in zip(times, times[1:])]
    our_total = sum(vals)
    sig = {
        "n_ours": n, "our_total_sats": our_total,
        "amount_cv": cv(vals), "interval_cv": cv(gaps),
        "first": day_str(times[0]) if times else "",
        "last": day_str(times[-1]) if times else "",
    }
    funded = balance = n_tx = None
    spends = False
    hop1 = set()
    hist_recv = hist_spent = 0
    if cache:
        info = cache.get("info") or {}
        cs = info.get("chain_stats") or {}
        funded = cs.get("funded_txo_sum")
        n_tx = (cs.get("tx_count") or 0) + ((info.get("mempool_stats") or {}).get("tx_count") or 0)
        for tx in cache.get("txs", []):
            for v in tx.get("vout", []):
                if v.get("a") == addr:
                    hist_recv += v.get("v") or 0
            mine = False
            for v in tx.get("vin", []):
                if v.get("a") == addr:
                    hist_spent += v.get("v") or 0
                    mine = True
            if mine:
                spends = True
                for v in tx.get("vout", []):
                    if v.get("a") and v["a"] != addr:
                        hop1.add(v["a"])
        if cache.get("spent_refs"):
            spends = spends or bool(cache["spent_refs"])
        # electrs lies: some long-history addresses show funded_txo_sum=0.
        # Denominator = best available: max(chain_stats, history-derived).
        cands = [x for x in (funded, hist_recv) if x]
        funded = max(cands) if cands else None
        if info.get("final_balance") is not None:
            balance = info.get("final_balance")
        elif funded is not None:
            spent = cs.get("spent_txo_sum")
            if not spent and hist_spent:
                spent = hist_spent
            balance = funded - (spent or 0)
    sig.update({"funded_sats": funded, "balance_sats": balance, "n_tx": n_tx,
                "spends": spends, "hop1": sorted(hop1),
                "history_partial": bool(cache.get("history_partial")) if cache else None})
    sig["fan_in"] = (our_total / funded) if funded else None
    return sig


def classify_dest(addr, coin, sig):
    n = sig["n_ours"]
    if sig["fan_in"] is None:
        return "UNKNOWN", "no funded sum (lookup failed)"
    # FIXED_MERCHANT first: an exclusive merchant (fan_in=1.0, only we pay
    # them) must not be swallowed by the fan_in>=0.98 self rules.
    if (n >= 3 and sig["amount_cv"] <= 0.15 and sig["interval_cv"] <= 0.5
            and sig["interval_cv"] > 0):
        return "FIXED_MERCHANT", (f"n={n}, amount_cv={sig['amount_cv']:.2f}, "
                                  f"interval_cv={sig['interval_cv']:.2f}")
    if sig["fan_in"] >= SELF_THRESHOLD:
        if not sig["spends"]:
            return "SELF_SWEEP", f"fan_in={sig['fan_in']:.2f}, never spends"
        note = f"fan_in={sig['fan_in']:.2f}, spends"
        return "SELF_FORWARD", note
    if sig["fan_in"] >= MIXED_THRESHOLD:
        return "MIXED_CUSTODIAL", f"fan_in={sig['fan_in']:.2f} (custodial-scale shared address)"
    if n >= 2:
        return "VENDOR_REPEAT", f"n={n}, no fixed-amount pattern"
    return "VENDOR_ONCE", "single payment"


def hop2_lookup(coin, addr):
    """Fetch a hop-1 target's address-info only (lightweight, one request).

    Hop labelling only needs chain_stats (funded/spent sums, tx count) —
    never the full paginated history, which is what made this stage crawl.
    """
    p = os.path.join(HOP, f"{addr}.json")
    if os.path.exists(p):
        return json.load(open(p))
    import classify_stage23_lookup as m23
    try:
        if coin == "ltc" and not m23.LTC_PRIMARY_OK:
            data = m23.fetch_addr_history_blockcypher(addr)
            out = {"funded_sats": data.get("funded_sats"),
                   "tx_count": data.get("n_tx", 0),
                   "spends": bool(data.get("spent_refs"))}
        else:
            bases = list(FALLBACK_APIS[coin])
            if coin == "ltc" and not m23.LTC_PRIMARY_OK:
                bases = bases[1:]
            info = None
            last = None
            for base in bases:
                try:
                    info = m23._get_json(f"{base}/address/{addr}", timeout=20,
                                         retries=1)
                    break
                except Exception as e:
                    last = e
            if info is None:
                raise last or RuntimeError("no source")
            cs = info.get("chain_stats") or {}
            ms = info.get("mempool_stats") or {}
            funded = cs.get("funded_txo_sum") or 0
            spent = cs.get("spent_txo_sum") or 0
            # electrs funded=0 lie guard: only trust non-zero sums
            out = {"funded_sats": funded or None,
                   "tx_count": (cs.get("tx_count") or 0) + (ms.get("tx_count") or 0),
                   "spends": spent > 0}
        json.dump(out, open(p, "w"))
        return out
    except Exception as e:
        out = {"error": str(e)}
        json.dump(out, open(p, "w"))
        return out


def main():
    os.makedirs(HOP, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    rows, own, dests = load_inputs()

    # group our payments per destination
    pays = defaultdict(list)
    coin_of = {}
    for r in rows:
        for a, v in r["ext_outs"]:
            pays[a].append((r["time"], v))
            coin_of[a] = r["coin"]

    # ---- destination table + classification ----
    dest_rows = {}
    for a, plist in pays.items():
        sig = dest_signals(a, dests.get(a), plist)
        cls, why = classify_dest(a, coin_of[a], sig)
        dest_rows[a] = {"address": a, "coin": coin_of[a], "class": cls,
                        "why": why, **sig}

    # ---- hop-2 for SELF_FORWARD chains ----
    hop_rows = []
    need = set()
    for a, dr in dest_rows.items():
        if dr["class"] == "SELF_FORWARD":
            for h in dr["hop1"]:
                if h not in own and h not in dest_rows:
                    need.add((dr["coin"], h))
    print(f"hop-2 lookups needed: {len(need)}", flush=True)
    from classify_stage23_lookup import _probe_ltc_primary
    _probe_ltc_primary()
    from concurrent.futures import ThreadPoolExecutor, as_completed
    def _one(pair):
        coin, h = pair
        return {"hop_addr": h, "coin": coin, **hop2_lookup(coin, h)}
    done = 0
    with ThreadPoolExecutor(max_workers=24) as ex:
        futs = [ex.submit(_one, p2) for p2 in sorted(need)]
        for fut in as_completed(futs):
            hop_rows.append(fut.result())
            done += 1
            if done % 50 == 0:
                print(f"  hop-2 {done}/{len(need)}", flush=True)

    # annotate depth-2: hop1 target is own address -> chained self
    for a, dr in dest_rows.items():
        if dr["class"] != "SELF_FORWARD":
            continue
        hop_summary = []
        for h in dr["hop1"]:
            if h in own:
                hop_summary.append(f"{h[:16]}…(own)")
            elif h in dest_rows:
                hop_summary.append(f"{h[:16]}…({dest_rows[h]['class']})")
            else:
                hr = next((x for x in hop_rows if x["hop_addr"] == h), None)
                if hr and hr.get("funded_sats"):
                    hop_summary.append(f"{h[:16]}…(recv {hr['funded_sats']/1e8:.4f})")
                else:
                    hop_summary.append(f"{h[:16]}…(?)")
        dr["hop_detail"] = "; ".join(hop_summary)

    # ---- USD rates ----
    lo = min(r["time"] for r in rows if r["time"]) - 86400
    hi = max(r["time"] for r in rows if r["time"]) + 86400
    print(f"fetching USD rates {day_str(lo)}..{day_str(hi)}", flush=True)
    prices = {"btc": fetch_daily_prices("BTCUSDT", lo, hi),
              "ltc": fetch_daily_prices("LTCUSDT", lo, hi)}

    # ---- per-tx classification ----
    out_txs = []
    for r in rows:
        t = r["time"]
        price = prices[r["coin"]].get(day_str(t)) if t else None
        for a, v in r["ext_outs"]:
            dr = dest_rows[a]
            usd = (v / 1e8) * price if price else None
            out_txs.append({
                "date": day_str(t) if t else "", "txid": r["txid"],
                "slug": r["slug"], "coin": r["coin"],
                "dest": a, "amount": v / 1e8,
                "usd_exit": round(usd, 2) if usd is not None else "",
                "dest_class": dr["class"], "n_payments_to_dest": dr["n_ours"],
                "fan_in": round(dr["fan_in"], 3) if dr["fan_in"] is not None else "",
            })

    # ---- totals per class (fiat P&L framing) ----
    classes = defaultdict(lambda: {"txs": 0, "coin": 0.0, "usd_exit": 0.0, "by_coin": defaultdict(float)})
    for r in rows:
        for a, v in r["ext_outs"]:
            cls = dest_rows[a]["class"]
            t = r["time"]
            price = prices[r["coin"]].get(day_str(t)) if t else None
            c = classes[cls]
            c["txs"] += 1
            c["coin"] += v / 1e8
            c["by_coin"][r["coin"]] += v / 1e8
            if price:
                c["usd_exit"] += (v / 1e8) * price

    # ---- write outputs ----
    with open(os.path.join(OUT, "destinations.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["address", "coin", "class", "why", "n_ours", "our_total",
                    "our_total_usd_equiv", "fan_in", "funded_total", "balance",
                    "n_tx_dest", "spends", "amount_cv", "interval_cv",
                    "first", "last", "history_partial", "hop_detail"])
        for a, dr in sorted(dest_rows.items(), key=lambda kv: -kv[1]["our_total_sats"]):
            w.writerow([a, dr["coin"], dr["class"], dr["why"], dr["n_ours"],
                        dr["our_total_sats"] / 1e8,
                        round(dr["our_total_sats"] / 1e8 *
                              (prices[dr["coin"]].get(dr["last"]) or 0), 2),
                        dr["fan_in"] if dr["fan_in"] is not None else "",
                        (dr["funded_sats"] or 0) / 1e8 if dr["funded_sats"] else "",
                        (dr["balance_sats"] or 0) / 1e8 if dr["balance_sats"] is not None else "",
                        dr["n_tx"] if dr["n_tx"] is not None else "",
                        dr["spends"], round(dr["amount_cv"], 3),
                        round(dr["interval_cv"], 3), dr["first"], dr["last"],
                        dr["history_partial"], dr.get("hop_detail", "")])

    with open(os.path.join(OUT, "outgoing.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_txs[0].keys()))
        w.writeheader()
        w.writerows(out_txs)

    with open(os.path.join(OUT, "hops.csv"), "w", newline="") as f:
        if hop_rows:
            keys = list(hop_rows[0].keys()) + ["error"]
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(hop_rows)

    classes_out = {k: {"txs": v["txs"], "coin_total": round(v["coin"], 8),
                       "usd_at_exit": round(v["usd_exit"], 2),
                       "by_coin": dict(v["by_coin"])}
                   for k, v in sorted(classes.items(), key=lambda kv: -kv[1]["usd_exit"])}
    json.dump(classes_out, open(os.path.join(OUT, "classes.json"), "w"), indent=1)
    print(json.dumps(classes_out, indent=1), flush=True)
    print(f"dests={len(dest_rows)} out_txs={len(out_txs)} hops={len(hop_rows)}", flush=True)


if __name__ == "__main__":
    main()
