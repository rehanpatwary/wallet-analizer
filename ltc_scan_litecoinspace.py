#!/usr/bin/env python3
"""
LTC Wallet Scanner via litecoinspace.org (mempool.space fork)
Parallel deep scan + transaction fetch + classification.
"""

import json, os, sys, time, socket, urllib.request, concurrent.futures
from datetime import datetime

socket.setdefaulttimeout(15)

sys.path.insert(0, '/Users/agenticos/Library/Python/3.9/lib/python/site-packages')
sys.path.insert(0, '/Users/agenticos/Documents/kimi/workspace/wallet-analizer')

from wallet_analyzer import derive_addresses

MNEMONIC = "resemble praise oxygen rhythm rate rose mutual upon beach april behave cliff"
GAP_LIMIT = 5000
MAX_SCAN = 25000
BASE = "https://litecoinspace.org/api"
OUT_DIR = "/Users/agenticos/Documents/kimi/workspace/wallet-analizer"
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}


def req(path, timeout=12, retries=4):
    delay = 1.5
    for attempt in range(retries):
        try:
            r = urllib.request.Request(f"{BASE}{path}", headers=HEADERS)
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as e:
            if attempt == retries - 1:
                return None
            time.sleep(delay)
            delay *= 2
    return None


def check_address(address):
    data = req(f"/address/{address}", timeout=8)
    if data and data.get('chain_stats', {}).get('tx_count', 0) > 0:
        return {
            'address': address,
            'tx_count': data['chain_stats']['tx_count'],
            'balance': data['chain_stats']['funded_txo_sum'] - data['chain_stats']['spent_txo_sum'],
            'data': data,
        }
    return None


def scan_path(mnemonic, coin, path_type, chain, gap_limit, max_scan):
    label = 'external' if chain == 0 else 'change'
    found = []
    consecutive_empty = 0
    idx = 0
    print(f"  [START] {path_type} ({label})")
    while idx < max_scan and consecutive_empty < gap_limit:
        batch = derive_addresses(mnemonic, coin, path_type, idx, 10, change=chain)
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
            print(f"  [PROGRESS] {path_type}/{label} idx={idx}, found={len(found)}")
    print(f"  [DONE] {path_type} ({label}): {len(found)} addresses")
    return found


def scan_addresses():
    print("[LTC SCAN] Starting parallel deep scan via litecoinspace.org...")
    all_found = []
    checkpoint = os.path.join(OUT_DIR, 'found_addresses_ltc.checkpoint.json')
    for path_type in ['bip44_legacy', 'bip49_segwit', 'bip84_native_segwit']:
        for chain in (0, 1):
            found = scan_path(MNEMONIC, 'ltc', path_type, chain, GAP_LIMIT, MAX_SCAN)
            all_found.extend(found)
            with open(checkpoint, 'w') as f:
                json.dump(all_found, f)
            print(f"  [CHECKPOINT] saved {len(all_found)} addresses")
    return all_found


def addr_txs(address):
    all_txs = []
    info = check_address(address)
    if not info:
        return all_txs
    total = info['tx_count']
    last_seen = None
    while len(all_txs) < total:
        if last_seen is None:
            data = req(f"/address/{address}/txs", timeout=15)
        else:
            data = req(f"/address/{address}/txs/chain/{last_seen}", timeout=15)
        if not data or not isinstance(data, list) or not data:
            break
        all_txs.extend(data)
        last_seen = data[-1]['txid']
        if len(data) < 25:
            break
        time.sleep(0.05)
    return all_txs


def fetch_all_txs(addresses):
    print(f"\n[TX FETCH] Fetching transactions for {len(addresses)} addresses...")
    txs = {}
    for i, info in enumerate(addresses):
        data = addr_txs(info['address'])
        for tx in data:
            txid = tx.get('txid')
            if txid and txid not in txs:
                txs[txid] = tx
        print(f"  [{i+1}/{len(addresses)}] {info['address']}: {len(data)} txs, {len(txs)} unique total")
    print(f"[TX FETCH] Total unique transactions: {len(txs)}")
    return txs


def classify(txs, addresses):
    ours = {a['address'] for a in addresses}
    incoming, outgoing_self, outgoing_vendor = [], [], []
    for txid, tx in txs.items():
        vin = tx.get('vin', [])
        vout = tx.get('vout', [])
        our_inputs = set()
        for inp in vin:
            prev = inp.get('prevout', {})
            addr = prev.get('scriptpubkey_address')
            if addr in ours:
                our_inputs.add(addr)
        if our_inputs:
            external, self_out = [], []
            for o in vout:
                addr = o.get('scriptpubkey_address')
                val = o.get('value', 0)
                if addr in ours:
                    self_out.append({'address': addr, 'value': val})
                elif addr:
                    external.append({'address': addr, 'value': val})
            rec = {'txid': txid, 'block_time': tx.get('status', {}).get('block_time'), 'fee': tx.get('fee', 0)}
            if external:
                rec['external_outputs'] = external
                rec['external_total'] = sum(e['value'] for e in external)
                outgoing_vendor.append(rec)
            else:
                rec['self_outputs'] = self_out
                rec['self_total'] = sum(s['value'] for s in self_out)
                outgoing_self.append(rec)
        else:
            received = sum(o.get('value', 0) for o in vout if o.get('scriptpubkey_address') in ours)
            incoming.append({'txid': txid, 'block_time': tx.get('status', {}).get('block_time'),
                             'amount_received': received})
    return incoming, outgoing_self, outgoing_vendor


def save_and_report(addresses, txs, incoming, out_self, out_vendor):
    addr_file = os.path.join(OUT_DIR, 'found_addresses_ltc.json')
    tx_file = os.path.join(OUT_DIR, 'all_ltc_transactions.json')
    report_file = os.path.join(OUT_DIR, 'ltc_report_litecoinspace.json')
    txt_file = os.path.join(OUT_DIR, 'ltc_report.txt')
    with open(addr_file, 'w') as f:
        json.dump(addresses, f, indent=2)
    with open(tx_file, 'w') as f:
        json.dump(list(txs.values()), f, indent=2)
    total_in = sum(i['amount_received'] for i in incoming)
    vendor_out = sum(o['external_total'] for o in out_vendor)
    self_out = sum(o['self_total'] for o in out_self)
    fees = sum(o['fee'] for o in out_vendor)
    report = {
        'generated_at': datetime.now().isoformat(),
        'source': 'litecoinspace.org',
        'mnemonic_fingerprint': 'resemble...cliff',
        'address_count': len(addresses),
        'transaction_count': len(txs),
        'incoming_count': len(incoming),
        'outgoing_self_count': len(out_self),
        'outgoing_vendor_count': len(out_vendor),
        'total_incoming_ltc': total_in / 1e8,
        'vendor_total_ltc': vendor_out / 1e8,
        'self_total_ltc': self_out / 1e8,
        'fees_ltc': fees / 1e8,
        'net_flow_ltc': (total_in - vendor_out - self_out - fees) / 1e8,
    }
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2)
    lines = [
        "=" * 70,
        "         LTC WALLET ANALYSIS REPORT (litecoinspace.org)",
        "=" * 70,
        "",
        f"Generated:      {report['generated_at']}",
        f"Source:         {report['source']}",
        "",
        f"Total addresses:        {report['address_count']}",
        f"Total transactions:     {report['transaction_count']}",
        f"Incoming txs:           {report['incoming_count']}",
        f"Self withdrawals:       {report['outgoing_self_count']}",
        f"Vendor withdrawals:     {report['outgoing_vendor_count']}",
        "",
        f"Total incoming LTC:     {report['total_incoming_ltc']:.8f} LTC",
        f"Vendor outgoing LTC:    {report['vendor_total_ltc']:.8f} LTC",
        f"Self outgoing LTC:      {report['self_total_ltc']:.8f} LTC",
        f"Fees LTC:               {report['fees_ltc']:.8f} LTC",
        f"Net flow LTC:           {report['net_flow_ltc']:.8f} LTC",
        "",
        "=" * 70,
    ]
    with open(txt_file, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    print('\n'.join(lines))
    print(f"\n[SAVE] {addr_file}\n[SAVE] {tx_file}\n[SAVE] {report_file}\n[SAVE] {txt_file}")


def main():
    addresses = scan_addresses()
    if not addresses:
        print("[LTC SCAN] No LTC addresses with transactions found.")
        return
    txs = fetch_all_txs(addresses)
    incoming, out_self, out_vendor = classify(txs, addresses)
    save_and_report(addresses, txs, incoming, out_self, out_vendor)
    print("\n[LTC SCAN] Complete.")


if __name__ == '__main__':
    main()
