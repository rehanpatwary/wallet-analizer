#!/usr/bin/env python3
"""Add USD valuation to vendor-destination analysis.

Each outgoing tx's external outputs are valued at the LIVE rate at the time of
the transaction: the Binance daily kline OPEN price (UTC day) for BTCUSDT /
LTCUSDT, fetched once per coin for the whole tx date range. Txs whose UTC day
has no kline (outside fetched range) are reported as unpriced.

Outputs:
  vendor_destinations_usd_report.json  - per-destination USD totals + grand totals
  vendor_destinations_usd_report.txt   - human readable ranked tables
  vendor_tx_usd.csv                    - one row per tx->destination transfer
"""

import csv
import datetime
import json
import sys
import time
import urllib.request
from collections import defaultdict

PROJ = "/Users/agenticos/Documents/kimi/workspace/wallet-analizer"
BINANCE = "https://api.binance.com/api/v3/klines"


def load_own_addresses(path):
    with open(path) as f:
        data = json.load(f)
    addrs = set()
    items = data.values() if isinstance(data, dict) else data
    for entry in items:
        if isinstance(entry, str):
            addrs.add(entry)
        elif isinstance(entry, dict) and entry.get("address"):
            addrs.add(entry["address"])
    return addrs


def fetch_daily_prices(symbol, start_ts, end_ts):
    """Return {utc_date_str: open_price_float} from Binance daily klines."""
    start_ms = int(start_ts) * 1000
    end_ms = int(end_ts) * 1000
    prices = {}
    while start_ms <= end_ms:
        url = (f"{BINANCE}?symbol={symbol}&interval=1d&limit=1000"
               f"&startTime={start_ms}&endTime={end_ms}")
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    rows = json.load(r)
                break
            except Exception as e:
                if attempt == 3:
                    raise
                wait = 2 ** attempt
                print(f"  retry {symbol} klines in {wait}s ({e})", flush=True)
                time.sleep(wait)
        if not rows:
            break
        for row in rows:
            day = datetime.datetime.fromtimestamp(row[0] / 1000, datetime.UTC).strftime("%Y-%m-%d")
            prices[day] = float(row[1])  # open
        start_ms = rows[-1][0] + 86_400_000
        if len(rows) < 1000:
            break
        time.sleep(0.5)
    return prices


def collect_transfers(path, own):
    """Yield (txid, block_time, dest, sats) for every external output of our spends."""
    with open(path) as f:
        raw = json.load(f)
    txs = raw.values() if isinstance(raw, dict) else raw
    out = []
    for tx in txs:
        if not any((vin.get("prevout") or {}).get("scriptpubkey_address") in own
                   for vin in tx.get("vin", [])):
            continue
        txid = tx.get("txid")
        block_time = (tx.get("status") or {}).get("block_time")
        for vout in tx.get("vout", []):
            addr = vout.get("scriptpubkey_address")
            val = vout.get("value")
            if not addr or val is None or addr in own:
                continue
            out.append((txid, block_time, addr, val))
    return out


def main():
    print("Loading own address sets...", flush=True)
    own_btc = load_own_addresses(f"{PROJ}/found_addresses_btc_bip44_external.json")
    own_ltc = load_own_addresses(f"{PROJ}/found_addresses_ltc.json")

    print("Collecting transfers...", flush=True)
    btc_transfers = collect_transfers(f"{PROJ}/all_transactions.json", own_btc)
    ltc_transfers = collect_transfers(f"{PROJ}/all_ltc_transactions.json", own_ltc)
    print(f"  BTC transfers: {len(btc_transfers)}, LTC transfers: {len(ltc_transfers)}")

    result = {"btc": None, "ltc": None}
    csv_rows = []
    for coin, symbol, transfers in (("BTC", "BTCUSDT", btc_transfers),
                                    ("LTC", "LTCUSDT", ltc_transfers)):
        timed = [t for t in transfers if t[1]]
        if timed:
            lo = min(t[1] for t in timed)
            hi = max(t[1] for t in timed)
            print(f"Fetching {symbol} daily rates {datetime.datetime.fromtimestamp(lo, datetime.UTC).date()} "
                  f"-> {datetime.datetime.fromtimestamp(hi, datetime.UTC).date()}...", flush=True)
            prices = fetch_daily_prices(symbol, lo - 86400, hi + 86400)
        else:
            prices = {}
        print(f"  {coin}: {len(prices)} daily prices", flush=True)

        dest_coin = defaultdict(float)
        dest_usd = defaultdict(float)
        dest_txs = defaultdict(set)
        dest_first, dest_last = {}, {}
        total_coin = 0.0
        total_usd = 0.0
        unpriced = 0
        for txid, bt, addr, sats in transfers:
            coin_amt = sats / 1e8
            day = (datetime.datetime.fromtimestamp(bt, datetime.UTC).strftime("%Y-%m-%d")
                   if bt else None)
            price = prices.get(day) if day else None
            usd = coin_amt * price if price else None
            dest_coin[addr] += coin_amt
            dest_txs[addr].add(txid)
            if bt:
                dest_first[addr] = min(dest_first.get(addr, bt), bt)
                dest_last[addr] = max(dest_last.get(addr, bt), bt)
            total_coin += coin_amt
            if usd is not None:
                dest_usd[addr] += usd
                total_usd += usd
            else:
                unpriced += 1
            csv_rows.append([coin, txid, day or "", addr, f"{coin_amt:.8f}",
                             f"{price:.2f}" if price else "",
                             f"{usd:.2f}" if usd is not None else ""])

        ranked = sorted(dest_coin.items(), key=lambda kv: dest_usd[kv[0]], reverse=True)
        result[coin.lower()] = {
            "coin": coin,
            "destinations": [
                {
                    "address": a,
                    "amount": round(dest_coin[a], 8),
                    "usd": round(dest_usd[a], 2),
                    "tx_count": len(dest_txs[a]),
                    "first_seen": dest_first.get(a),
                    "last_seen": dest_last.get(a),
                }
                for a, _ in ranked
            ],
            "total_sent_coin": round(total_coin, 8),
            "total_sent_usd": round(total_usd, 2),
            "distinct_destinations": len(dest_coin),
            "unpriced_transfers": unpriced,
            "price_source": f"Binance {symbol} daily open (UTC), live rate at tx day",
        }
        print(f"  {coin}: {len(dest_coin)} destinations, total {total_coin:.8f} "
              f"= ${total_usd:,.2f}, unpriced {unpriced}", flush=True)

    result["combined_total_usd"] = round(result["btc"]["total_sent_usd"] + result["ltc"]["total_sent_usd"], 2)

    with open(f"{PROJ}/vendor_destinations_usd_report.json", "w") as f:
        json.dump(result, f, indent=1)

    with open(f"{PROJ}/vendor_destinations_usd_report.txt", "w") as f:
        for coin in ("btc", "ltc"):
            r = result[coin]
            f.write(f"=== {r['coin']} vendor destinations (valued at live rate on tx day) ===\n")
            f.write(f"destinations: {r['distinct_destinations']}, "
                    f"total sent: {r['total_sent_coin']:.8f} {r['coin']} = ${r['total_sent_usd']:,.2f}\n")
            f.write(f"price source: {r['price_source']}; unpriced transfers: {r['unpriced_transfers']}\n\n")
            f.write(f"{'rank':>4} {'address':<40} {'amount':>18} {'USD':>16} {'txs':>5}  period\n")
            fmt = lambda ts: datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime("%Y-%m-%d") if ts else "-"
            for i, d in enumerate(r["destinations"], 1):
                f.write(f"{i:>4} {d['address']:<40} {d['amount']:>18.8f} "
                        f"{d['usd']:>16,.2f} {d['tx_count']:>5}  {fmt(d['first_seen'])} .. {fmt(d['last_seen'])}\n")
            f.write("\n")
        f.write(f"COMBINED TOTAL USD VALUE OF ALL OUTGOING TRANSFERS: ${result['combined_total_usd']:,.2f}\n")

    with open(f"{PROJ}/vendor_tx_usd.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["coin", "txid", "date_utc", "destination", "amount", "usd_rate", "usd_value"])
        w.writerows(csv_rows)

    print(f"COMBINED TOTAL USD: ${result['combined_total_usd']:,.2f}")


if __name__ == "__main__":
    sys.exit(main())
