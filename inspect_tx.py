import json

# Inspect first few transactions to understand structure
txs = json.load(open('all_transactions.json'))
print(f"Total txs: {len(txs)}")

# Get first tx
first_txid = list(txs.keys())[0]
tx = txs[first_txid]
print(f"\nSample tx: {first_txid}")
print(f"Keys: {list(tx.keys())}")
print(f"\nvin count: {len(tx.get('vin', []))}")
if tx.get('vin'):
    print(f"First vin keys: {list(tx['vin'][0].keys())}")
    if 'prevout' in tx['vin'][0]:
        print(f"First vin prevout: {tx['vin'][0]['prevout']}")
    if 'scriptsig' in tx['vin'][0]:
        print(f"First vin scriptsig_address: {tx['vin'][0].get('scriptsig_address')}")

print(f"\nvout count: {len(tx.get('vout', []))}")
if tx.get('vout'):
    print(f"First vout: {tx['vout'][0]}")

# Check for any tx with no vin (coinbase)
no_vin = [txid for txid, tx in txs.items() if not tx.get('vin')]
print(f"\nTxs with no vin (coinbase): {len(no_vin)}")

# Check vin structure more carefully
sample = list(txs.values())[5]
print(f"\nSample vin[0] full: {json.dumps(sample.get('vin',[{}])[0], indent=2)[:800]}")
