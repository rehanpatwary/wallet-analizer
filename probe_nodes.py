#!/usr/bin/env python3
"""Deep probe discovered nodes for blockchain APIs."""
import urllib.request, ssl, json

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def probe(url, desc):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'WalletAnalyzer/1.0'})
        with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
            data = resp.read(2000)
            text = data.decode('utf-8', errors='replace')
            print(f"\n{desc}: {url}")
            print(f"  Status: {resp.status}")
            print(f"  Content-Type: {resp.headers.get('Content-Type', 'unknown')}")
            print(f"  Preview: {text[:300]}")
            return True
    except Exception as e:
        print(f"\n{desc}: {url} -> {e}")
        return False

# Check if 10.10.30.3:3006 is LTC or BTC
probe("http://10.10.30.3:3006/api/v1/difficulty", "30.3:3006 difficulty")
probe("http://10.10.30.3:3006/api/blocks/tip/hash", "30.3:3006 tip hash")

# Check 10.10.20.10 and .11 for blockbook
probe("http://10.10.20.10:3000/api", "20.10:3000 api")
probe("http://10.10.20.10:9090/api", "20.10:9090 api")
probe("http://10.10.20.11:3000/api", "20.11:3000 api")
probe("http://10.10.20.11:9090/api", "20.11:9090 api")

# Try blockbook-specific endpoints
for ip in ["10.10.20.10", "10.10.20.11", "10.10.20.7"]:
    for port in [9130, 9139, 9030, 9090, 3000, 80, 8080]:
        probe(f"http://{ip}:{port}/api/v2", f"{ip}:{port} blockbook v2")
        probe(f"http://{ip}:{port}/api", f"{ip}:{port} blockbook api")
