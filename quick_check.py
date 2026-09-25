#!/usr/bin/env python3
from wallet_config import MNEMONIC as _MNEMONIC
"""
Streamlined BTC/LTC address checker using blockchain.info (BTC) and chain.so (LTC).
"""

import sys, hashlib, hmac, struct, json, time, urllib.request, ssl
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set

sys.path.insert(0, '/Users/agenticos/Library/Python/3.9/lib/python/site-packages')
import ecdsa, base58, bech32
from mnemonic import Mnemonic

HARDENED = 0x80000000
BTC_COIN_TYPE = 0
LTC_COIN_TYPE = 2

derivation = {
    'btc': {'bip44': (44, BTC_COIN_TYPE), 'bip49': (49, BTC_COIN_TYPE), 'bip84': (84, BTC_COIN_TYPE)},
    'ltc': {'bip44': (44, LTC_COIN_TYPE), 'bip49': (49, LTC_COIN_TYPE), 'bip84': (84, LTC_COIN_TYPE)},
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

def fetch_json(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'WalletAnalyzer/1.0', 'Accept': 'application/json'})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return None

def check_address_btc(address):
    """Returns n_tx count from blockchain.info, or None on error."""
    data = fetch_json(f'https://blockchain.info/rawaddr/{address}?limit=1', timeout=15)
    if data and 'n_tx' in data:
        return int(data['n_tx'])
    return None

def check_address_ltc(address):
    """Returns n_tx count from chain.so API v3, or None on error."""
    data = fetch_json(f'https://chain.so/api/v3/address_summary/LTC/{address}', timeout=15)
    if data and data.get('status') == 'success' and 'data' in data:
        return int(data['data'].get('total_txs', 0))
    return None

def scan_mnemonic(mnemonic, coin='btc', depth=100, gap=20):
    print(f"\n[{'='*60}")
    print(f"  SCANNING {coin.upper()} WALLET")
    print(f"  Mnemonic: {' '.join(mnemonic.split()[:3])}... (12 words)")
    print(f"  Depth: {depth} | Gap: {gap}")
    print(f"{'='*60}\n")

    found_addrs = []
    check_fn = check_address_btc if coin == 'btc' else check_address_ltc

    for path_name in derivation[coin]:
        for change in [0, 1]:
            chain_label = 'external' if change == 0 else 'change'
            print(f"  Path: {path_name} ({chain_label} chain)")
            consecutive_empty = 0
            idx = 0
            while idx < depth and consecutive_empty < gap:
                batch = derive_addresses(mnemonic, coin, path_name, idx, 5, change)
                for info in batch:
                    n_tx = check_fn(info['address'])
                    if n_tx is not None and n_tx > 0:
                        print(f"    [FOUND] {info['address']} ({path_name}, {chain_label}, idx {info['index']}) — {n_tx} tx(s)")
                        info['n_tx'] = n_tx
                        found_addrs.append(info)
                        consecutive_empty = 0
                    else:
                        consecutive_empty += 1
                    time.sleep(0.15)
                idx += 5

    print(f"\n  [RESULT] {len(found_addrs)} address(es) with transaction history found.")
    return found_addrs

if __name__ == '__main__':
    MNEMONIC = _MNEMONIC
    btc_addrs = scan_mnemonic(MNEMONIC, coin='btc', depth=50, gap=10)
    ltc_addrs = scan_mnemonic(MNEMONIC, coin='ltc', depth=50, gap=10)

    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    print(f"  BTC addresses with history: {len(btc_addrs)}")
    print(f"  LTC addresses with history: {len(ltc_addrs)}")
