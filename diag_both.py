import json

addresses = json.load(open('found_addresses_btc_bip44_external.json'))
txs = json.load(open('all_transactions.json'))
our_addrs = set(a['address'] for a in addresses)

# Find transactions where our address is in BOTH input and output
both_count = 0
sample_both = []

# Find transactions with only our addresses as outputs (no external)
only_ours_count = 0

for txid, tx in txs.items():
    vin = tx.get('vin', [])
    vout = tx.get('vout', [])
    
    input_addrs = set()
    for inp in vin:
        addr = inp.get('prevout', {}).get('scriptpubkey_address')
        if addr:
            input_addrs.add(addr)
    
    output_addrs = set()
    for out in vout:
        addr = out.get('scriptpubkey_address')
        if addr:
            output_addrs.add(addr)
    
    we_input = bool(input_addrs & our_addrs)
    we_output = bool(output_addrs & our_addrs)
    
    if we_input and we_output:
        both_count += 1
        if len(sample_both) < 5:
            sample_both.append({
                'txid': txid,
                'input_ours': [a for a in input_addrs if a in our_addrs],
                'output_ours': [a for a in output_addrs if a in our_addrs],
                'output_external': [a for a in output_addrs if a not in our_addrs],
                'fee': tx.get('fee', 0)
            })
    
    if we_input and output_addrs and output_addrs.issubset(our_addrs):
        only_ours_count += 1

print(f"Txs where we are both sender and receiver: {both_count}")
print(f"Txs where all outputs are ours: {only_ours_count}")
print("\nSample txs with both input and output from us:")
for s in sample_both:
    print(f"\n  TXID: {s['txid']}")
    print(f"  Our inputs: {s['input_ours']}")
    print(f"  Our outputs: {s['output_ours']}")
    print(f"  External outputs: {s['output_external']}")
    print(f"  Fee: {s['fee']}")
