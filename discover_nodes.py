#!/usr/bin/env python3
"""Discover blockchain nodes on both subnets."""
import urllib.request, socket, ssl, json, concurrent.futures

SUBNETS = ["10.10.20", "10.10.30"]
PORTS_TO_TRY = [80, 443, 8080, 3000, 3006, 8000, 9000, 9130, 9139, 19130, 19139, 9030, 9090]

def check_host(ip, port, timeout=3):
    try:
        req = urllib.request.Request(f"http://{ip}:{port}/api", method='GET')
        req.add_header('User-Agent', 'WalletAnalyzer/1.0')
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = resp.read(800)
            return ip, port, resp.status, data
    except Exception:
        return None

print("Scanning local network for blockchain nodes...")
found = []
with concurrent.futures.ThreadPoolExecutor(max_workers=80) as executor:
    futures = []
    for subnet in SUBNETS:
        for i in range(1, 30):
            ip = f"{subnet}.{i}"
            for port in PORTS_TO_TRY:
                futures.append(executor.submit(check_host, ip, port))
    
    for future in concurrent.futures.as_completed(futures):
        result = future.result()
        if result:
            ip, port, status, data = result
            print(f"  FOUND: {ip}:{port} -> HTTP {status}")
            try:
                text = data.decode('utf-8', errors='replace').lower()
                if any(k in text for k in ['mempool', 'blockbook', 'blockchain', 'btc', 'ltc', 'litecoin', 'bitcoin']):
                    print(f"    DATA: {text[:200]}")
            except:
                pass
            found.append(result)

print(f"\nTotal found: {len(found)}")
