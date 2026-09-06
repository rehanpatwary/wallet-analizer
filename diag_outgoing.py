import json

txs = json.load(open('all_transactions.json'))
our_addrs = set(a['address'] for a in json.load(open('found_addresses_btc_bip44_external.json')))

# Find outgoing txs (our address in input)
outgoing = []
for txid, tx in txs.items():
    vin = tx.get('vin', [])
    vout = tx.get('vout', [])
    
    our_inputs = []
    for inp in vin:
        addr = inp.get('prevout', {}).get('scriptpubkey_address')
        if addr in our_addrs:
            our_inputs.append(addr)
    
    if our_inputs:
        external_outputs = []
        our_outputs = []
        for out in vout:
            addr = out.get('scriptpubkey_address')
            val = out.get('value', 0)
            if addr in our_addrs:
                our_outputs.append({'addr': addr, 'val': val})
            elif addr:
                external_outputs.append({'addr': addr, 'val': val})
        
        outgoing.append({
            'txid': txid,
            'our_input_count': len(our_inputs),
            'total_input_count': len(vin),
            'external_outputs': external_outputs,
            'our_outputs': our_outputs,
            'fee': tx.get('fee', 0),
            'status': tx.get('status', {})
        })

print(f"Total outgoing txs: {len(outgoing)}")
print("\nFirst 5 outgoing transactions:")
for o in outgoing[:5]:
    print(f"\n  TXID: {o['txid']}")
    print(f"  Our inputs: {o['our_input_count']} / {o['total_input_count']} total")
    print(f"  External outputs ({len(o['external_outputs'])}):")
    for eo in o['external_outputs']:
        print(f"    {eo['addr']}: {eo['val']/1e8:.8f} BTC")
    print(f"  Our outputs ({len(o['our_outputs'])}):")
    for oo in o['our_outputs']:
        print(f"    {oo['addr']}: {oo['val']/1e8:.8f} BTC")
    print(f"  Fee: {o['fee']/1e8:.8f} BTC")
    print(f"  Status: {o['status']}")

# Distribution of input counts
from collections import Counter
input_counts = Counter(o['our_input_count'] for o in outgoing)
print(f"\nDistribution of our-input counts:")
for k in sorted(input_counts.keys()):
    print(f"  {k} inputs: {input_counts[k]} txs")

# Check for consolidation patterns (many inputs, 1-2 outputs)
consolidations = [o for o in outgoing if o['our_input_count'] >= 10]
print(f"\nTxs with 10+ our inputs (consolidations): {len(consolidations)}")
