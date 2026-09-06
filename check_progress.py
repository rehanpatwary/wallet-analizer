import json

s = json.load(open('tx_fetch_state.json'))
completed = s.get('completed', [])
failed = s.get('failed', [])
total_txids = s.get('total_txids', [])

print(f"Completed addresses: {len(completed)}")
print(f"Failed addresses: {len(failed)}")
print(f"Total unique txids tracked: {len(total_txids)}")

# Load addresses list
addrs = json.load(open('found_addresses_btc_bip44_external.json'))
print(f"Total addresses to process: {len(addrs)}")
print(f"Remaining (not completed): {len(addrs) - len(completed)}")

# Load transactions
txs = json.load(open('all_transactions.json'))
print(f"Unique transactions cached: {len(txs)}")
