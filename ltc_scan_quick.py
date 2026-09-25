#!/usr/bin/env python3
from wallet_config import PROJ
"""
LTC Wallet Scanner via litecoinspace.org (mempool.space fork)
Deep scan + transaction fetch + classification using public API.
"""

import json, os, sys, time, urllib.request
from datetime import datetime

sys.path.insert(0, '/Users/agenticos/Library/Python/3.9/lib/python/site-packages')
sys.path.insert(0, PROJ)

from wallet_analyzer import derive_addresses

from wallet_config import MNEMONIC
GAP_LIMIT = 100
MAX_SCAN = 6000
BASE = "https://litecoinspace.org/api"
OUT_DIR = PROJ


def req(path, timeout=12):
    try:
        r = urllib.request.Request(f"{BASE}{path}", headers={'User-Agent': 'WalletAnalyzer/1.0'})
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"  [API ERROR] {path}: {e}")
        return None


def addr_info(address):
    return req(f"/address/{address}", timeout=8)


def addr_txs(address):
    """Fetch all txs for an address with pagination if needed."""
    all_txs = []
    info = addr_info(address)
    if not info or info.get('chain_stats', {}).get('tx_count', 0) == 0:
        return all_txs
    total = info['chain_stats']['tx_count']
    # mempool.space returns max 25 per page
    last_seen = None
    while len(all_txs) < total:
        if last_seen is None:
            data = req(f"/address/{address}/txs", timeout=15)
        else:
            data = req(f"/address/{address}/txs/chain/{last_seen}", timeout=15)
        if not data or not isinstance(data, list):
            break
        if not data:
            break
        all_txs.extend(data)
        last_seen = data[-1]['txid']
        if len(data) < 25:
            break
        time.sleep(0.1)
    return all_txs


def scan_addresses():
    print("[LTC SCAN] Starting deep address scan via litecoinspace.org...")
    all_found = []
    for path_type in ['bip44_legacy', 'bip49_segwit', 'bip84_native_segwit']:
        for chain in (0, 1):
            label = 'external' if chain == 0 else 'change'
            found = []
            consecutive_empty = 0
            idx = 0
            print(f"\n  Path: {path_type} ({label})")
            while idx < MAX_SCAN and consecutive_empty < GAP_LIMIT:
                batch = derive_addresses(MNEMONIC, 'ltc', path_type, idx, 10, change=chain)
                for info in batch:
                    address = info['address']
                    data = addr_info(address)
                    if data and data.get('chain_stats', {}).get('tx_count', 0) > 0:
                        tx_count = data['chain_stats']['tx_count']
                        balance = data['chain_stats']['funded_txo_sum'] - data['chain_stats']['spent_txo_sum']
                        info['tx_count'] = tx_count
                        info['balance'] = balance
                        found.append(info)
                        all_found.append(info)
                        consecutive_empty = 0
                        print(f"    [FOUND] {address} ({path_type}, {label}, idx {info['index']}) - {tx_count} txs, bal {balance/1e8:.8f} LTC")
                    else:
                        consecutive_empty += 1
                    time.sleep(0.05)
                idx += 10
                if idx % 500 == 0:
                    print(f"  progress: {path_type}/{label} idx={idx}, found={len(found)}")
            print(f"  Found {len(found)} addresses on {path_type} {label}")
    return all_found


def fetch_all_txs(addresses):
    print(f"\n[TX FETCH] Fetching transactions for {len(addresses)} addresses...")
    txs = {}
    for i, info in enumerate(addresses):
        address = info['address']
        data = addr_txs(address)
        for tx in data:
            txid = tx.get('txid')
            if txid and txid not in txs:
                txs[txid] = tx
        print(f"  [{i+1}/{len(addresses)}] {address}: {len(data)} txs fetched, {len(txs)} unique total")
        time.sleep(0.1)
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
            rec = {'txid': txid, 'block_time': tx.get('status', {}).get('block_time'),
                   'fee': tx.get('fee', 0)}
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
    print(f"\n[SAVE] {addr_file}")
    print(f"[SAVE] {tx_file}")
    print(f"[SAVE] {report_file}")
    print(f"[SAVE] {txt_file}")


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
