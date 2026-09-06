#!/usr/bin/env python3
"""Try alternative LTC APIs."""
import urllib.request, ssl, json, concurrent.futures

ctx = ssl.create_default_context()

def test_url(name, url, timeout=8):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 WalletAnalyzer/1.0'})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = resp.read()
            ct = resp.headers.get('Content-Type', '')
            try:
                parsed = json.loads(data)
                return name, True, f"HTTP {resp.status} JSON", parsed
            except:
                return name, True, f"HTTP {resp.status} {ct}", data[:150].decode('utf-8', errors='replace')
    except Exception as e:
        return name, False, str(e)[:120], None

test_addr = "LVgWwJq4YmC59vHUm6VoMzxDa6Dx5UonrM"

urls = [
    ("blockchair LTC addr", f"https://api.blockchair.com/litecoin/dashboards/address/{test_addr}"),
    ("blockchair LTC addr limit", f"https://api.blockchair.com/litecoin/dashboards/address/{test_addr}?limit=50"),
    ("ltc mempool space api", f"https://mempool.space/litecoin/api/address/{test_addr}"),
    ("ltc mempool space txs", f"https://mempool.space/litecoin/api/address/{test_addr}/txs"),
    ("litecoinspace org", f"https://litecoinspace.org/api/address/{test_addr}"),
    ("litecoinspace txs", f"https://litecoinspace.org/api/address/{test_addr}/txs"),
    ("crypto ID balance", f"https://chainz.cryptoid.info/ltc/api.dws?q=getbalance&a={test_addr}"),
    ("crypto ID txs", f"https://chainz.cryptoid.info/ltc/api.dws?q=listtransactions&a={test_addr}"),
    ("insight LTC", f"https://insight.litecore.io/api/addr/{test_addr}"),
    ("insight LTC txs", f"https://insight.litecore.io/api/txs/?address={test_addr}"),
    ("blockcypher wait", f"https://api.blockcypher.com/v1/ltc/main/addrs/{test_addr}?limit=1"),
]

with concurrent.futures.ThreadPoolExecutor(max_workers=len(urls)) as executor:
    futures = {executor.submit(test_url, name, url): name for name, url in urls}
    for future in concurrent.futures.as_completed(futures):
        name, ok, info, data = future.result()
        if ok:
            print(f"✅ {name}: {info}")
            if isinstance(data, dict):
                print(f"   {json.dumps(data, indent=2)[:350]}")
            else:
                print(f"   {data}")
        else:
            print(f"❌ {name}: {info}")
        print()
