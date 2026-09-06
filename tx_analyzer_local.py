#!/usr/bin/env python3
"""
Fetch and classify BTC transactions via LOCAL mempool.space instance.
"""

import sys, json, urllib.request, ssl, time
from datetime import datetime
from collections import defaultdict

BASE = "http://10.10.20.3:3006"

def fetch_json(path, timeout=30):
    try:
        req = urllib.request.Request(f"{BASE}{path}", headers={'User-Agent': 'WalletAnalyzer/1.0', 'Accept': 'application/json'})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"    [ERROR] {BASE}{path}: {e}")
        return None

OUR_ADDRS = {
    '1NZggBvNt7mn2GdD28EYgYKKj86CCw7XAc',
    '14mRCJcUymZYgL3GuxNxjsskGMpi2FX2BJ',
}

def fetch_local_txs(address):
    """Fetch transactions from local mempool"""
    txs = fetch_json(f'/api/address/{address}/txs', timeout=60)
    if txs is None:
        return []
    print(f"  Fetched {len(txs)} tx(s) for {address}")
    return txs

def satoshi_to_btc(sat):
    return sat / 1e8 if sat else 0.0

def parse_mempool_tx(raw):
    txid = raw.get('txid', 'unknown')
    time_val = raw.get('status', {}).get('block_time')
    block_h = raw.get('status', {}).get('block_height')
    fee = satoshi_to_btc(raw.get('fee', 0))
    vsize = raw.get('vsize', raw.get('size', 0))
    
    inputs = []
    for inp in raw.get('vin', []):
        prev = inp.get('prevout', {})
        addr = prev.get('scriptpubkey_address', '')
        val = satoshi_to_btc(prev.get('value', 0))
        inputs.append({'address': addr, 'value': val})
    
    outputs = []
    for out in raw.get('vout', []):
        addr = out.get('scriptpubkey_address', '')
        val = satoshi_to_btc(out.get('value', 0))
        n = out.get('n', 0)
        outputs.append({'address': addr, 'value': val, 'index': n})
    
    return {
        'txid': txid,
        'time': time_val,
        'block_height': block_h,
        'fee': fee,
        'vsize': vsize,
        'inputs': inputs,
        'outputs': outputs,
    }

def classify_tx(tx):
    inputs_from_us = [inp for inp in tx['inputs'] if inp['address'] in OUR_ADDRS]
    outputs_to_us = [out for out in tx['outputs'] if out['address'] in OUR_ADDRS]
    outputs_to_external = [out for out in tx['outputs'] if out['address'] and out['address'] not in OUR_ADDRS]
    
    amount_from_us = sum(inp['value'] for inp in inputs_from_us)
    amount_to_us = sum(out['value'] for out in outputs_to_us)
    amount_to_external = sum(out['value'] for out in outputs_to_external)
    
    if amount_from_us > 0 and amount_to_external > 0:
        return 'outgoing', amount_from_us, amount_to_us, amount_to_external, outputs_to_external
    elif amount_from_us > 0 and amount_to_external == 0:
        return 'self', amount_from_us, amount_to_us, 0, []
    else:
        return 'incoming', 0, amount_to_us, amount_to_external, outputs_to_external

def main():
    print("=" * 70)
    print("  BTC TRANSACTION ANALYSIS (Local Mempool 10.10.20.3:3006)")
    print("=" * 70)
    
    all_raw_txs = {}
    
    for addr in OUR_ADDRS:
        print(f"\n  Fetching transactions for {addr}...")
        txs = fetch_local_txs(addr)
        for tx in txs:
            txid = tx.get('txid')
            if txid and txid not in all_raw_txs:
                all_raw_txs[txid] = tx
        time.sleep(0.5)
    
    print(f"\n  Total unique transactions: {len(all_raw_txs)}")
    
    parsed_txs = []
    for txid, raw in all_raw_txs.items():
        parsed = parse_mempool_tx(raw)
        direction, amt_in, amt_out, amt_ext, ext_dests = classify_tx(parsed)
        parsed['direction'] = direction
        parsed['amount_from_us'] = amt_in
        parsed['amount_to_us'] = amt_out
        parsed['amount_to_external'] = amt_ext
        parsed['external_destinations'] = ext_dests
        parsed_txs.append(parsed)
    
    parsed_txs.sort(key=lambda x: x.get('time') or 0)
    
    incoming = [t for t in parsed_txs if t['direction'] == 'incoming']
    outgoing = [t for t in parsed_txs if t['direction'] == 'outgoing']
    self_txs = [t for t in parsed_txs if t['direction'] == 'self']
    
    print("\n" + "=" * 70)
    print("  CLASSIFICATION SUMMARY")
    print("=" * 70)
    print(f"  Incoming (deposits):        {len(incoming)}")
    print(f"  Outgoing (vendor withdraw): {len(outgoing)}")
    print(f"  Self-transfers:             {len(self_txs)}")
    print(f"  Total:                      {len(parsed_txs)}")
    
    total_received = sum(t['amount_to_us'] for t in incoming)
    total_sent = sum(t['amount_from_us'] for t in outgoing)
    total_self = sum(t['amount_from_us'] for t in self_txs)
    total_fees = sum(t['fee'] for t in parsed_txs)
    
    print(f"\n  Total Received:  +{total_received:.8f} BTC")
    print(f"  Total Sent:      -{total_sent:.8f} BTC")
    print(f"  Self-Moved:      {total_self:.8f} BTC")
    print(f"  Total Fees:      -{total_fees:.8f} BTC")
    print(f"  Net Balance:     {total_received - total_sent:.8f} BTC")
    
    vendor_map = defaultdict(float)
    for t in outgoing:
        for dest in t['external_destinations']:
            vendor_map[dest['address']] += dest['value']
    
    print("\n" + "=" * 70)
    print("  VENDOR WITHDRAWAL DESTINATIONS")
    print("=" * 70)
    if vendor_map:
        for addr, amount in sorted(vendor_map.items(), key=lambda x: x[1], reverse=True):
            print(f"  → {addr}: {amount:.8f} BTC")
    else:
        print("  (none)")
    
    print("\n" + "=" * 70)
    print("  OUTGOING TX BREAKDOWN (Vendor Withdrawals)")
    print("=" * 70)
    for t in outgoing:
        dt = datetime.fromtimestamp(t['time']).strftime('%Y-%m-%d %H:%M') if t['time'] else 'unknown'
        print(f"\n  [{dt}] TXID: {t['txid']}")
        print(f"    Fee: {t['fee']:.8f} BTC | Size: {t['vsize']} vbytes")
        print(f"    Amount sent from wallet: {t['amount_from_us']:.8f} BTC")
        for dest in t['external_destinations']:
            print(f"    → Destination: {dest['address']} | {dest['value']:.8f} BTC")
    
    if self_txs:
        print("\n" + "=" * 70)
        print("  SELF-TRANSFERS")
        print("=" * 70)
        for t in self_txs:
            dt = datetime.fromtimestamp(t['time']).strftime('%Y-%m-%d %H:%M') if t['time'] else 'unknown'
            print(f"\n  [{dt}] TXID: {t['txid']}")
            print(f"    Amount moved internally: {t['amount_from_us']:.8f} BTC")
    
    print("\n" + "=" * 70)
    print("  INCOMING TX (Deposits)")
    print("=" * 70)
    for t in incoming:
        dt = datetime.fromtimestamp(t['time']).strftime('%Y-%m-%d %H:%M') if t['time'] else 'unknown'
        print(f"\n  [{dt}] TXID: {t['txid']}")
        print(f"    Amount received: {t['amount_to_us']:.8f} BTC")
    
    # Save JSON report
    report = {
        'source': 'local_mempool_10.10.20.3:3006',
        'addresses': list(OUR_ADDRS),
        'summary': {
            'total_tx': len(parsed_txs),
            'incoming': len(incoming),
            'outgoing': len(outgoing),
            'self': len(self_txs),
            'total_received': total_received,
            'total_sent': total_sent,
            'total_fees': total_fees,
            'net_balance': total_received - total_sent,
        },
        'vendor_destinations': dict(vendor_map),
        'transactions': parsed_txs,
    }
    
    with open('tx_report_local.json', 'w') as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  [SAVED] Full report: tx_report_local.json")

if __name__ == '__main__':
    main()
