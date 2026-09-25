#!/usr/bin/env python3
"""Rank external (vendor) destination addresses by amount received from our wallet.

For each outgoing transaction (any vin spends from our address set), every
output that does NOT belong to our address set is an external destination and
is credited with its value. Outputs with no address (OP_RETURN / nulldata) are
skipped but counted.

Sources:
  - BTC: all_transactions.json        (dict keyed by txid)
  - LTC: all_ltc_transactions.json    (list of txs)
  - our addresses: found_addresses_btc_bip44_external.json, found_addresses_ltc.json
Values in the raw files are satoshi ints; we convert once with /1e8.
"""

import json
import sys
from collections import defaultdict

from wallet_config import PROJ


def load_own_addresses(path):
    with open(path) as f:
        data = json.load(f)
    addrs = set()
    if isinstance(data, dict):
        # tolerate a dict of address -> info
        for k, v in data.items():
            if isinstance(v, dict) and "address" in v:
                addrs.add(v["address"])
            else:
                addrs.add(k)
    else:
        for entry in data:
            if isinstance(entry, str):
                addrs.add(entry)
            elif isinstance(entry, dict):
                a = entry.get("address")
                if a:
                    addrs.add(a)
    return addrs


def iter_vin_addresses(tx):
    for vin in tx.get("vin", []):
        prev = vin.get("prevout")
        if prev:
            yield prev.get("scriptpubkey_address")


def analyze(txs_iter, own, coin):
    dest_amount = defaultdict(int)   # sats
    dest_txs = defaultdict(int)
    dest_first = {}
    dest_last = {}
    outgoing_txs = 0
    external_vout_total = 0          # sats across all outgoing txs
    own_vout_total = 0               # sats (change back to us)
    fees_total = 0
    op_return_outputs = 0
    for tx in txs_iter:
        vin_addrs = [a for a in iter_vin_addresses(tx)]
        if not any(a in own for a in vin_addrs):
            continue  # not our spend
        outgoing_txs += 1
        block_time = (tx.get("status") or {}).get("block_time")
        in_total = 0
        for vin in tx.get("vin", []):
            prev = vin.get("prevout")
            if prev and prev.get("value") is not None:
                in_total += prev["value"]
        out_total = 0
        for vout in tx.get("vout", []):
            val = vout.get("value")
            if val is None:
                continue
            out_total += val
            addr = vout.get("scriptpubkey_address")
            if not addr:
                op_return_outputs += 1
                continue
            if addr in own:
                own_vout_total += val
                continue
            dest_amount[addr] += val
            dest_txs[addr] += 1
            external_vout_total += val
            if block_time:
                if addr not in dest_first or block_time < dest_first[addr]:
                    dest_first[addr] = block_time
                if addr not in dest_last or block_time > dest_last[addr]:
                    dest_last[addr] = block_time
        fees_total += max(0, in_total - out_total)

    ranked = sorted(dest_amount.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "coin": coin,
        "outgoing_txs": outgoing_txs,
        "distinct_destinations": len(dest_amount),
        "external_sent_total": external_vout_total / 1e8,
        "change_back_to_self": own_vout_total / 1e8,
        "fees_total": fees_total / 1e8,
        "op_return_outputs": op_return_outputs,
        "destinations": [
            {
                "address": a,
                "amount": amt / 1e8,
                "tx_count": dest_txs[a],
                "first_seen": dest_first.get(a),
                "last_seen": dest_last.get(a),
            }
            for a, amt in ranked
        ],
    }


def main():
    print("Loading own address sets...", flush=True)
    own_btc = load_own_addresses(f"{PROJ}/found_addresses_btc_bip44_external.json")
    own_ltc = load_own_addresses(f"{PROJ}/found_addresses_ltc.json")
    print(f"  BTC own addresses: {len(own_btc)}")
    print(f"  LTC own addresses: {len(own_ltc)}")

    print("Loading BTC transactions...", flush=True)
    with open(f"{PROJ}/all_transactions.json") as f:
        btc_raw = json.load(f)
    btc_txs = btc_raw.values() if isinstance(btc_raw, dict) else btc_raw
    btc = analyze(btc_txs, own_btc, "BTC")
    print(f"  BTC: {btc['outgoing_txs']} outgoing txs, "
          f"{btc['distinct_destinations']} destinations, "
          f"{btc['external_sent_total']:.8f} sent external")

    print("Loading LTC transactions...", flush=True)
    with open(f"{PROJ}/all_ltc_transactions.json") as f:
        ltc_raw = json.load(f)
    ltc_txs = ltc_raw.values() if isinstance(ltc_raw, dict) else ltc_raw
    ltc = analyze(ltc_txs, own_ltc, "LTC")
    print(f"  LTC: {ltc['outgoing_txs']} outgoing txs, "
          f"{ltc['distinct_destinations']} destinations, "
          f"{ltc['external_sent_total']:.8f} sent external")

    result = {"btc": btc, "ltc": ltc}
    with open(f"{PROJ}/vendor_destinations_report.json", "w") as f:
        json.dump(result, f, indent=1)

    with open(f"{PROJ}/vendor_destinations_report.txt", "w") as f:
        for coin_key in ("btc", "ltc"):
            r = result[coin_key]
            f.write(f"=== {r['coin']} vendor destinations ===\n")
            f.write(f"outgoing txs: {r['outgoing_txs']}, distinct destinations: "
                    f"{r['distinct_destinations']}\n")
            f.write(f"total sent to external destinations: {r['external_sent_total']:.8f} {r['coin']}\n")
            f.write(f"change back to self: {r['change_back_to_self']:.8f}, "
                    f"fees: {r['fees_total']:.8f}, "
                    f"OP_RETURN outputs skipped: {r['op_return_outputs']}\n\n")
            f.write(f"{'rank':>4} {'address':<40} {'amount':>18} {'txs':>6} first_seen   last_seen\n")
            for i, d in enumerate(r["destinations"], 1):
                import datetime
                fmt = lambda ts: datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") if ts else "-"
                f.write(f"{i:>4} {d['address']:<40} {d['amount']:>18.8f} "
                        f"{d['tx_count']:>6} {fmt(d['first_seen'])}  {fmt(d['last_seen'])}\n")
            f.write("\n")

    print("Wrote vendor_destinations_report.json / .txt")


if __name__ == "__main__":
    sys.exit(main())
