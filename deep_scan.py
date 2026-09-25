#!/usr/bin/env python3
"""
Deep address scan with gap limit 5000 using local mempool node.
Checks BIP44/49/84 for both external (0) and change (1) chains.
"""

import sys, json, urllib.request, ssl, time, hashlib, hmac, struct
from typing import List, Dict

sys.path.insert(0, '/Users/agenticos/Library/Python/3.9/lib/python/site-packages')
import ecdsa, base58, bech32
from mnemonic import Mnemonic

BASE = "http://10.10.20.3:3006"
from wallet_config import MNEMONIC
HARDENED = 0x80000000
BTC_COIN_TYPE = 0
LTC_COIN_TYPE = 2

derivation = {
    'btc': {
        'bip44': (44, BTC_COIN_TYPE),
        'bip49': (49, BTC_COIN_TYPE),
        'bip84': (84, BTC_COIN_TYPE),
    },
    'ltc': {
        'bip44': (44, LTC_COIN_TYPE),
        'bip49': (49, LTC_COIN_TYPE),
        'bip84': (84, LTC_COIN_TYPE),
    }
}
netver = {
    'btc': {'p2pkh': b'\x00', 'p2sh': b'\x05'},
    'ltc': {'p2pkh': b'\x30', 'p2sh': b'\x32'},
}

class BIP32Key:
    def __init__(self, key: bytes, chain_code: bytes, depth=0, index=0, fingerprint=b'\x00\x00\x00\x00'):
        self.key, self.chain_code, self.depth, self.index, self.fingerprint = key, chain_code, depth, index, fingerprint
    def get_public_key(self) -> bytes:
        sk = ecdsa.SigningKey.from_string(self.key, curve=ecdsa.SECP256k1)
        vk = sk.get_verifying_key()
        x, y = vk.pubkey.point.x(), vk.pubkey.point.y()
        return (b'\x02' if y % 2 == 0 else b'\x03') + x.to_bytes(32, 'big')
    def derive_child(self, index: int, hardened=False):
        if hardened: index |= HARDENED; data = b'\x00' + self.key + struct.pack('>I', index)
        else: data = self.get_public_key() + struct.pack('>I', index)
        h = hmac.new(self.chain_code, data, hashlib.sha512).digest()
        left, right = h[:32], h[32:]
        child_key = ((int.from_bytes(self.key, 'big') + int.from_bytes(left, 'big')) % ecdsa.SECP256k1.order).to_bytes(32, 'big')
        fp = hashlib.new('ripemd160', hashlib.sha256(self.get_public_key()).digest()).digest()[:4]
        return BIP32Key(child_key, right, self.depth + 1, index, fp)
    def derive_path(self, indices):
        k = self
        for idx in indices:
            k = k.derive_child(idx & ~HARDENED, idx >= HARDENED)
        return k

def hash160(d): return hashlib.new('ripemd160', hashlib.sha256(d).digest()).digest()
def p2pkh(pub, net):
    h = hash160(pub); v = netver[net]['p2pkh']
    return base58.b58encode(v + h + hashlib.sha256(hashlib.sha256(v + h).digest()).digest()[:4]).decode()
def p2sh_p2wpkh(pub, net):
    wp = b'\x00\x14' + hash160(pub)
    sh = hash160(wp); v = netver[net]['p2sh']
    payload = v + sh
    return base58.b58encode(payload + hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]).decode()
def p2wpkh(pub, net):
    h = hash160(pub)
    prefix = 'bc' if net == 'btc' else 'ltc'
    return bech32.bech32_encode(prefix, [0] + _convert_bits(h, 8, 5))
def _convert_bits(data, from_bits, to_bits, pad=True):
    acc, bits, ret, maxv = 0, 0, [], (1 << to_bits) - 1
    max_acc = (1 << (from_bits + to_bits - 1)) - 1
    for v in data:
        acc = ((acc << from_bits) | v) & max_acc; bits += from_bits
        while bits >= to_bits: bits -= to_bits; ret.append((acc >> bits) & maxv)
    if pad and bits: ret.append((acc << (to_bits - bits)) & maxv)
    return ret

def mnemonic_to_seed(mnemonic, passphrase=""):
    return Mnemonic("english").to_seed(mnemonic, passphrase)
def seed_to_master(seed):
    h = hmac.new(b"Bitcoin seed", seed, hashlib.sha512).digest()
    return BIP32Key(h[:32], h[32:])

def derive_addresses(mnemonic, coin, path_type, start=0, count=20, change=0):
    seed = mnemonic_to_seed(mnemonic)
    master = seed_to_master(seed)
    p = derivation[coin][path_type]
    path = [HARDENED + p[0], HARDENED + p[1], HARDENED + 0, change]
    acc = master.derive_path(path)
    results = []
    for i in range(start, start + count):
        child = acc.derive_child(i)
        pub = child.get_public_key()
        if path_type == 'bip44': addr = p2pkh(pub, coin)
        elif path_type == 'bip49': addr = p2sh_p2wpkh(pub, coin)
        elif path_type == 'bip84': addr = p2wpkh(pub, coin)
        else: continue
        results.append({'index': i, 'address': addr, 'path': f"m/{p[0]}'/{p[1]}'/0'/{change}/{i}", 'type': path_type})
    return results

def check_address_local(address, timeout=10):
    try:
        req = urllib.request.Request(f"{BASE}/api/address/{address}", headers={'User-Agent': 'WalletAnalyzer/1.0', 'Accept': 'application/json'})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = json.loads(resp.read())
            return data.get('chain_stats', {}).get('tx_count', 0)
    except Exception:
        return None

def scan_path(mnemonic, coin, path_type, change, gap_limit):
    chain_label = 'external' if change == 0 else 'change'
    print(f"\n  Scanning {coin.upper()} {path_type} ({chain_label} chain) — gap limit: {gap_limit}")
    found = []
    consecutive_empty = 0
    idx = 0
    batch_size = 5
    
    while consecutive_empty < gap_limit:
        batch = derive_addresses(mnemonic, coin, path_type, idx, batch_size, change)
        for info in batch:
            n_tx = check_address_local(info['address'])
            if n_tx is None:
                print(f"    [SKIP] API error for {info['address']}")
                consecutive_empty += 1
            elif n_tx > 0:
                print(f"    [FOUND] {info['address']} (idx {info['index']}) — {n_tx} tx(s)")
                info['n_tx'] = n_tx
                found.append(info)
                consecutive_empty = 0
            else:
                consecutive_empty += 1
            
            if consecutive_empty >= gap_limit:
                break
        
        idx += batch_size
        if idx % 100 == 0:
            print(f"    ...checked up to index {idx}, gap: {consecutive_empty}/{gap_limit}")
        time.sleep(0.05)
    
    print(f"  [DONE] {len(found)} address(es) found, stopped at index {idx}")
    return found

def main():
    print("=" * 70)
    print("  DEEP ADDRESS SCAN — Gap Limit 5000")
    print("  Local Node: 10.10.20.3:3006")
    print("=" * 70)
    
    all_found = []
    
    for coin in ['btc', 'ltc']:
        for path_type in derivation[coin]:
            for change in [0, 1]:
                found = scan_path(MNEMONIC, coin, path_type, change, gap_limit=5000)
                all_found.extend(found)
    
    print("\n" + "=" * 70)
    print("  SCAN COMPLETE")
    print("=" * 70)
    print(f"  Total addresses with history: {len(all_found)}")
    
    if all_found:
        for info in all_found:
            print(f"  → {info['address']} | {info['path']} | {info['n_tx']} tx(s)")
    else:
        print("  No addresses with transaction history found.")

if __name__ == '__main__':
    main()
