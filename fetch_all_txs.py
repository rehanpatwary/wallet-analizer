#!/usr/bin/env python3
"""
Transaction fetcher for wallet analysis.
Fetches all transactions for discovered addresses from local mempool.
Saves progress incrementally to survive timeouts.
"""

import json, urllib.request, ssl, time, os, sys
from datetime import datetime

BASE = "http://10.10.20.3:3006"
ADDRESSES_FILE = '/Users/agenticos/Documents/kimi/workspace/wallet-analizer/found_addresses_btc_bip44_external.json'
STATE_FILE = '/Users/agenticos/Documents/kimi/workspace/wallet-analizer/tx_fetch_state.json'
OUTPUT_FILE = '/Users/agenticos/Documents/kimi/workspace/wallet-analizer/all_transactions.json'
BATCH_SAVE_EVERY = 50  # Save every N addresses processed

def load_addresses():
    with open(ADDRESSES_FILE, 'r') as f:
        return json.load(f)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {'completed': [], 'failed': [], 'total_txids': []}

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

def fetch_address_txs(address, timeout=15):
    """Fetch all transactions for an address. Returns list of tx objects."""
    all_txs = []
    last_seen_txid = None
    
    while True:
        url = f"{BASE}/api/address/{address}/txs"
        if last_seen_txid:
            url += f"?after_txid={last_seen_txid}"
        
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'WalletAnalyzer/1.0',
                'Accept': 'application/json'
            })
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                txs = json.loads(resp.read())
                
                if not txs:
                    break
                
                all_txs.extend(txs)
                last_seen_txid = txs[-1]['txid']
                
                # Mempool.space typically returns 25 txs per page
                if len(txs) < 25:
                    break
                    
        except Exception as e:
            return {'error': str(e), 'partial': all_txs}
    
    return {'txs': all_txs}

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
    
    processed_since_save = 0
    total_new_txs = 0
    
    for i, addr_info in enumerate(remaining):
        addr = addr_info['address']
        
        result = fetch_address_txs(addr)
        
        if 'error' in result:
            print(f"  [FAIL] {addr} (idx {addr_info['index']}): {result['error']}")
            failed.add(addr)
            state['failed'].append(addr)
        else:
            txs = result['txs']
            print(f"  [OK] {addr} (idx {addr_info['index']}): {len(txs)} tx(s)")
            
            new_count = 0
            for tx in txs:
                txid = tx['txid']
                if txid not in all_transactions:
                    all_transactions[txid] = tx
                    new_count += 1
            total_new_txs += new_count
            
            completed.add(addr)
            state['completed'].append(addr)
        
        processed_since_save += 1
        
        if processed_since_save >= BATCH_SAVE_EVERY:
            save_state(state)
            save_transactions(all_transactions)
            print(f"  [SAVED] {len(completed)} done, {len(all_transactions)} unique txs")
            processed_since_save = 0
        
        # Small delay to not overwhelm the node
        time.sleep(0.1)
        
        # Progress report every 100
        if (i + 1) % 100 == 0:
            print(f"\n  Progress: {i+1}/{len(remaining)} | Total unique txs: {len(all_transactions)} | Time: {datetime.now().isoformat()}\n")
    
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
