import json, urllib.request, ssl

addr = '1NADUH7EdGaLnkRCijxb3VTm53bUmy41Xy'
req = urllib.request.Request(f'http://10.10.20.3:3006/api/address/{addr}/txs', headers={'User-Agent': 'WalletAnalyzer/1.0', 'Accept': 'application/json'})
ctx = ssl.create_default_context()
with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
    d = json.loads(resp.read())

print(f'txs returned: {len(d)}')
if d:
    tx = d[0]
    print(f'txid: {tx.get("txid")}')
    print(f'vin count: {len(tx.get("vin", []))}')
    print(f'vout count: {len(tx.get("vout", []))}')
    # Check if this address is in outputs
    our_outputs = [vo for vo in tx.get('vout', []) if vo.get('scriptpubkey_address') == addr]
    print(f'outputs to our address: {len(our_outputs)}')
    for vo in our_outputs:
        print(f'  value: {vo.get("value")}')
    # Check inputs
    our_inputs = [vi for vi in tx.get('vin', []) if vi.get('prevout', {}).get('scriptpubkey_address') == addr]
    print(f'inputs from our address: {len(our_inputs)}')
    for vi in our_inputs:
        print(f'  value: {vi.get("prevout", {}).get("value")}')
