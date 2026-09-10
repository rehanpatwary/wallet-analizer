#!/usr/bin/env python3
"""
Continuation scanner for the LTC deep scan.

The main scan (ltc_scan_litecoinspace.py) caps each derivation path at
MAX_SCAN=6000 indices. The bip44_legacy external chain alone showed funded
addresses still dense near idx 5546, so the cap can truncate real history
before the user's required 5,000-empty gap is satisfied.

This script:
  1. Loads found_addresses_ltc.json + all_ltc_transactions.json from the main run
  2. For each (path_type, chain) combo, scans from MAX_SCAN onward until
     GAP_LIMIT (5000) consecutive empty addresses are seen
  3. Fetches transactions only for newly found addresses
  4. Merges everything and re-classifies + rewrites all reports
"""
import json
import os
import sys
import time
import concurrent.futures

sys.path.insert(0, '/Users/agenticos/Library/Python/3.9/lib/python/site-packages')
sys.path.insert(0, '/Users/agenticos/Documents/kimi/workspace/wallet-analizer')

from ltc_scan_litecoinspace import (
    MNEMONIC, GAP_LIMIT, OUT_DIR,
    check_address, derive_addresses, addr_txs, classify, save_and_report,
)

MAX_SCAN_MAIN = 6000      # where the main scan stopped
HARD_CAP = 50000          # absolute safety bound for continuation
COMBOS = [(p, c) for p in ['bip44_legacy', 'bip49_segwit', 'bip84_native_segwit'] for c in (0, 1)]


def load_json(name, default):
    path = os.path.join(OUT_DIR, name)
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def scan_from(path_type, chain, start_idx):
    label = 'external' if chain == 0 else 'change'
    found = []
    consecutive_empty = 0
    idx = start_idx
    print(f"  [CONT] {path_type} ({label}) from idx {idx}")
    while idx < HARD_CAP and consecutive_empty < GAP_LIMIT:
        batch = derive_addresses(MNEMONIC, 'ltc', path_type, idx, 10, change=chain)
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            futures = {ex.submit(check_address, a['address']): a for a in batch}
            for fut in concurrent.futures.as_completed(futures):
                info = futures[fut]
                result = fut.result()
                if result:
                    info['tx_count'] = result['tx_count']
                    info['balance'] = result['balance']
                    found.append(info)
                    consecutive_empty = 0
                    print(f"    [FOUND] {info['address']} ({path_type}, {label}, idx {info['index']}) - {result['tx_count']} txs")
                else:
                    consecutive_empty += 1
        idx += 10
        if idx % 500 == 0:
            print(f"  [PROGRESS] {path_type}/{label} idx={idx}, new={len(found)}")
    print(f"  [DONE] {path_type} ({label}): {len(found)} new addresses, ended at idx {idx}")
    return found


def main():
    addresses = load_json('found_addresses_ltc.json', [])
    txs_list = load_json('all_ltc_transactions.json', [])
    if not addresses:
        print("[CONT] No main-run results found. Run ltc_scan_litecoinspace.py first.")
        return

    print(f"[CONT] Loaded {len(addresses)} addresses, {len(txs_list)} txs from main run")
    known = {a['address'] for a in addresses}
    new_addrs = []

    for path_type, chain in COMBOS:
        # highest funded index the main run found for this combo
        idxs = [a['index'] for a in addresses
                if a.get('path_type') == path_type and a.get('chain', a.get('change')) == chain]
        # main scan covered [0, MAX_SCAN) for every combo regardless, so continue from the cap
        found = scan_from(path_type, chain, MAX_SCAN_MAIN)
        for f in found:
            if f['address'] not in known:
                known.add(f['address'])
                new_addrs.append(f)
        addresses.extend([f for f in found if f['address'] not in {x['address'] for x in addresses}])

    print(f"\n[CONT] {len(new_addrs)} genuinely new addresses beyond the main-run cap")

    txs = {t['txid']: t for t in txs_list if t.get('txid')}
    for i, info in enumerate(new_addrs):
        data = addr_txs(info['address'])
        for tx in data:
            txid = tx.get('txid')
            if txid and txid not in txs:
                txs[txid] = tx
        print(f"  [{i+1}/{len(new_addrs)}] {info['address']}: {len(data)} txs, {len(txs)} unique total")
        time.sleep(0.05)

    incoming, out_self, out_vendor = classify(txs, addresses)
    save_and_report(addresses, txs, incoming, out_self, out_vendor)
    print("\n[CONT] Complete. Reports rewritten with continuation results merged.")


if __name__ == '__main__':
    main()
