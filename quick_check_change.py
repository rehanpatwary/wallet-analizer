#!/usr/bin/env python3
"""Quick check change chain for first 1000 addresses."""
import sys, json, urllib.request, ssl, time, hashlib, hmac, struct
sys.path.insert(0, '/Users/agenticos/Library/Python/3.9/lib/python/site-packages')
import ecdsa, base58, bech32
from mnemonic import Mnemonic

BASE = "http://10.10.20.3:3006"
MNEMONIC = 'resemble praise oxygen rhythm rate rose mutual upon beach april behave cliff'
HARDENED = 0x80000000

def hash160(d): return __import__('hashlib').new('ripemd160', __import__('hashlib').sha256(d).digest()).digest()
def p2pkh(pub): h = hash160(pub); v = b'\x00'; return base58.b58encode(v + h + __import__('hashlib').sha256(__import__('hashlib').sha256(v + h).digest()).digest()[:4]).decode()

def derive_addresses(mnemonic, start, count, change=0):
    seed = Mnemonic("english").to_seed(mnemonic)
    h = hmac.new(b"Bitcoin seed", seed, hashlib.sha512).digest()
    class K:
        def __init__(self, key, chain, d=0, i=0, fp=b'\x00\x00\x00\x00'):
            self.key, self.chain, self.d, self.i, self.fp = key, chain, d, i, fp
        def pub(self):
            sk = ecdsa.SigningKey.from_string(self.key, curve=ecdsa.SECP256k1)
            x = sk.get_verifying_key().pubkey.point.x()
            y = sk.get_verifying_key().pubkey.point.y()
            return (b'\x02' if y % 2 == 0 else b'\x03') + x.to_bytes(32, 'big')
        def child(self, idx, hard=False):
            if hard: idx |= HARDENED; data = b'\x00' + self.key + struct.pack('>I', idx)
            else: data = self.pub() + struct.pack('>I', idx)
            h = hmac.new(self.chain, data, hashlib.sha512).digest()
            left, right = h[:32], h[32:]
            ck = ((int.from_bytes(self.key, 'big') + int.from_bytes(left, 'big')) % ecdsa.SECP256k1.order).to_bytes(32, 'big')
            fp = __import__('hashlib').new('ripemd160', __import__('hashlib').sha256(self.pub()).digest()).digest()[:4]
            return K(ck, right, self.d+1, idx, fp)
        def path(self, indices):
            k = self
            for idx in indices:
                k = k.child(idx & ~HARDENED, idx >= HARDENED)
            return k
    master = K(h[:32], h[32:])
    acc = master.path([HARDENED+44, HARDENED+0, HARDENED+0, change])
    results = []
    for i in range(start, start+count):
        pub = acc.child(i).pub()
        results.append({'index': i, 'address': p2pkh(pub)})
    return results

def check(addr):
    try:
        req = urllib.request.Request(f"{BASE}/api/address/{addr}", headers={'User-Agent': 'WalletAnalyzer/1.0', 'Accept': 'application/json'})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            data = json.loads(resp.read())
            return data.get('chain_stats', {}).get('tx_count', 0)
    except Exception:
        return None

print("Quick check BTC BIP44 change chain (first 1000 addresses)...")
found = []
for info in derive_addresses(MNEMONIC, 0, 1000, change=1):
    n = check(info['address'])
    if n:
        print(f"  [FOUND] idx {info['index']}: {info['address']} — {n} tx(s)")
        info['n_tx'] = n
        found.append(info)
    if info['index'] % 100 == 0:
        print(f"  ...checked up to {info['index']}")
    time.sleep(0.03)

print(f"\nFound {len(found)} addresses in change chain (first 1000)")
if found:
    print(f"Index range: {min(a['index'] for a in found)} - {max(a['index'] for a in found)}")
