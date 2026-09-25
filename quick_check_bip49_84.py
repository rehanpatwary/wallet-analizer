#!/usr/bin/env python3
"""Quick check BIP49 (SegWit compat) and BIP84 (Native SegWit) for first 100 addresses."""
import sys, json, urllib.request, ssl, time, hashlib, hmac, struct
sys.path.insert(0, '/Users/agenticos/Library/Python/3.9/lib/python/site-packages')
import ecdsa, base58, bech32
from mnemonic import Mnemonic

BASE = "http://10.10.20.3:3006"
from wallet_config import MNEMONIC
HARDENED = 0x80000000

def hash160(d): return __import__('hashlib').new('ripemd160', __import__('hashlib').sha256(d).digest()).digest()
def p2sh_p2wpkh(pub):
    wp = b'\x00\x14' + hash160(pub)
    sh = hash160(wp)
    payload = b'\x05' + sh
    return base58.b58encode(payload + __import__('hashlib').sha256(__import__('hashlib').sha256(payload).digest()).digest()[:4]).decode()
def p2wpkh(pub):
    h = hash160(pub)
    return bech32.bech32_encode('bc', [0] + _convert_bits(h, 8, 5))
def _convert_bits(data, from_bits, to_bits, pad=True):
    acc, bits, ret, maxv = 0, 0, [], (1 << to_bits) - 1
    max_acc = (1 << (from_bits + to_bits - 1)) - 1
    for v in data:
        acc = ((acc << from_bits) | v) & max_acc; bits += from_bits
        while bits >= to_bits: bits -= to_bits; ret.append((acc >> bits) & maxv)
    if pad and bits: ret.append((acc << (to_bits - bits)) & maxv)
    return ret

def derive(mnemonic, path_type, start, count, change=0):
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
    if path_type == 'bip49':
        acc = master.path([HARDENED+49, HARDENED+0, HARDENED+0, change])
    else:
        acc = master.path([HARDENED+84, HARDENED+0, HARDENED+0, change])
    results = []
    for i in range(start, start+count):
        pub = acc.child(i).pub()
        if path_type == 'bip49': addr = p2sh_p2wpkh(pub)
        else: addr = p2wpkh(pub)
        results.append({'index': i, 'address': addr})
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

for pt in ['bip49', 'bip84']:
    for ch in [0, 1]:
        chname = 'external' if ch == 0 else 'change'
        print(f"\nChecking BTC {pt} {chname} (first 100)...")
        found = []
        for info in derive(MNEMONIC, pt, 0, 100, ch):
            n = check(info['address'])
            if n:
                print(f"  [FOUND] idx {info['index']}: {info['address']} — {n} tx(s)")
                info['n_tx'] = n
                found.append(info)
            time.sleep(0.03)
        print(f"  -> Found {len(found)} addresses")
