#!/usr/bin/env python3
"""Test BlockCypher LTC with actual address query."""
import urllib.request, ssl, json

ctx = ssl.create_default_context()

def test_url(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'WalletAnalyzer/1.0'})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = json.loads(resp.read())
            return True, data
    except Exception as e:
        return False, str(e)

# Test with the first LTC address derived from the mnemonic
test_addr = "LVgWwJq4YmC59vHUm6VoMzxDa6Dx5UonrM"

# BlockCypher LTC
ok, data = test_url(f"https://api.blockcypher.com/v1/ltc/main/addrs/{test_addr}?limit=50")
print(f"BlockCypher LTC address query: {'OK' if ok else 'FAIL'}")
if ok:
    print(json.dumps(data, indent=2)[:800])
else:
    print(data)

# Try with a known active LTC address
print("\n--- Testing with known active address ---")
active = "LTC_ADDRESS_PLACEHOLDER"
# Let's just test the endpoint structure
ok2, data2 = test_url("https://api.blockcypher.com/v1/ltc/main")
print(f"BlockCypher LTC chain info: {'OK' if ok2 else 'FAIL'}")
if ok2:
    print(f"  Height: {data2.get('height')}")
    print(f"  Name: {data2.get('name')}")
