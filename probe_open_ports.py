#!/usr/bin/env python3
"""Probe open ports for blockchain APIs."""
import urllib.request, ssl, json

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def probe(url, timeout=5):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'WalletAnalyzer/1.0'})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = resp.read(1000)
            ct = resp.headers.get('Content-Type', '')
            return resp.status, ct, data.decode('utf-8', errors='replace')
    except Exception as e:
        return None, None, str(e)[:100]

# Check Proxmox on 8443
print("=== 10.10.20.7:8443 ===")
status, ct, body = probe("https://10.10.20.7:8443", timeout=3)
print(f"Status: {status}, CT: {ct}")
print(body[:300])

# Check 10.10.20.10:3000 for blockbook paths
print("\n=== 10.10.20.10:3000 blockbook paths ===")
for path in ['/api/v2', '/api', '/api/v1', '/api/v2/block-index/0', '/status', '/']:
    status, ct, body = probe(f"http://10.10.20.10:3000{path}")
    print(f"  {path}: status={status}, ct={ct}")
    if status == 200:
        print(f"    {body[:200]}")

# Check 10.10.20.11:3000 for blockbook paths
print("\n=== 10.10.20.11:3000 blockbook paths ===")
for path in ['/api/v2', '/api', '/api/v1', '/api/v2/block-index/0', '/status', '/']:
    status, ct, body = probe(f"http://10.10.20.11:3000{path}")
    print(f"  {path}: status={status}, ct={ct}")
    if status == 200:
        print(f"    {body[:200]}")

# Check 10.10.20.10:9090 for blockbook paths
print("\n=== 10.10.20.10:9090 blockbook paths ===")
for path in ['/api/v2', '/api', '/api/v1']:
    status, ct, body = probe(f"http://10.10.20.10:9090{path}")
    print(f"  {path}: status={status}, ct={ct}")
    if status == 200:
        print(f"    {body[:200]}")

# Check if 8443 might be blockbook directly
print("\n=== 10.10.20.7:8443 blockbook paths ===")
for path in ['/api/v2', '/api', '/api/v1']:
    status, ct, body = probe(f"https://10.10.20.7:8443{path}")
    print(f"  {path}: status={status}, ct={ct}")
    if status == 200:
        print(f"    {body[:200]}")
