#!/usr/bin/env python3
from wallet_config import MEMPOOL_API, P, PROJ
"""
Concurrent transaction fetcher for wallet analysis.
Uses threading to speed up API calls.
"""

import json, urllib.request, ssl, time, os, sys, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

BASE = MEMPOOL_API.removesuffix("/api")
ADDRESSES_FILE = P('found_addresses_btc_bip44_external.json')
STATE_FILE = P('tx_fetch_state.json')
OUTPUT_FILE = P('all_transactions.json')
MAX_WORKERS = 16

def load_addresses():
    with open(ADDRESSES_FILE, 'r') as f:
        return json.load(f)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {'completed': [], 'failed': []}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

def load_existing_transactions():
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_transactions(txs):
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(txs, f, indent=2)

def fetch_address_txs(addr_info):
    """Fetch all transactions for an address. Returns (address, result_dict)."""
    address = addr_info['address']
    all_txs = []
    last_seen_txid = None
    
    try:
        while True:
            url = f"{BASE}/api/address/{address}/txs"
            if last_seen_txid:
                url += f"?after_txid={last_seen_txid}"
            
            req = urllib.request.Request(url, headers={
                'User-Agent': 'WalletAnalyzer/1.0',
                'Accept': 'application/json'
            })
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                txs = json.loads(resp.read())
                
                if not txs:
                    break
                
                all_txs.extend(txs)
                last_seen_txid = txs[-1]['txid']
                
                if len(txs) < 25:
                    break
        
        return (address, {'success': True, 'txs': all_txs, 'count': len(all_txs), 'index': addr_info['index']})
    except Exception as e:
        return (address, {'success': False, 'error': str(e), 'index': addr_info['index']})

def main():
    addresses = load_addresses()
    state = load_state()
    all_transactions = load_existing_transactions()
    
    completed = set(state['completed'])
    failed = set(state['failed'])
    
    print(f"Loaded {len(addresses)} addresses")
    print(f"Already processed: {len(completed)}")
    print(f"Previously failed: {len(failed)}")
    print(f"Unique transactions cached: {len(all_transactions)}")
    print(f"Started at: {datetime.now().isoformat()}")
    print("-" * 60)
    
    remaining = [a for a in addresses if a['address'] not in completed and a['address'] not in failed]
    print(f"Remaining to process: {len(remaining)}")
    
    total_new_txs = 0
    batch_processed = 0
    lock = threading.Lock()
    
    def process_result(address, result):
        nonlocal total_new_txs, batch_processed
        with lock:
            if result['success']:
                new_count = 0
                for tx in result['txs']:
                    txid = tx['txid']
                    if txid not in all_transactions:
                        all_transactions[txid] = tx
                        new_count += 1
                total_new_txs += new_count
                completed.add(address)
                state['completed'].append(address)
                print(f"  [OK] {address} (idx {result['index']}): {result['count']} tx(s), {new_count} new")
            else:
                failed.add(address)
                state['failed'].append(address)
                print(f"  [FAIL] {address} (idx {result['index']}): {result['error']}")
            
            batch_processed += 1
            
            if batch_processed % 100 == 0:
                save_state(state)
                save_transactions(all_transactions)
                print(f"\n  [SAVED] {len(completed)} done, {len(all_transactions)} unique txs\n")
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_addr = {executor.submit(fetch_address_txs, a): a for a in remaining}
        
        for future in as_completed(future_to_addr):
            addr_info = future_to_addr[future]
            try:
                address, result = future.result()
                process_result(address, result)
            except Exception as e:
                with lock:
                    failed.add(addr_info['address'])
                    state['failed'].append(addr_info['address'])
                    print(f"  [FAIL] {addr_info['address']} (idx {addr_info['index']}): {e}")
                    batch_processed += 1
    
    # Final save
    save_state(state)
    save_transactions(all_transactions)
    
    print("-" * 60)
    print(f"DONE at: {datetime.now().isoformat()}")
    print(f"Total addresses processed: {len(completed)}")
    print(f"Failed addresses: {len(failed)}")
    print(f"Total unique transactions: {len(all_transactions)}")
    print(f"New transactions this run: {total_new_txs}")

if __name__ == '__main__':
    main()
