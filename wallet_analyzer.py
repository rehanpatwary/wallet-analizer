#!/usr/bin/env python3
"""
Crypto Wallet Recovery & Transaction Analysis System
Supports Bitcoin (BTC) and Litecoin (LTC) from BIP39 mnemonic phrases.

Features:
- Deep address discovery across BIP44/49/84 derivation paths
- Transaction history retrieval from public blockchain APIs
- Automatic classification: Incoming / Outgoing / Self-Withdrawal / Vendor-Withdrawal
- Fund flow mapping and visual HTML report generation

Usage:
    python3 wallet_analyzer.py --mnemonic "your twelve word seed phrase here" --coin btc --depth 20
"""

import sys
import os
import hashlib
import hmac
import struct
import base64
import json
import time
import urllib.request
import urllib.error
import urllib.parse
import ssl
import argparse
import csv
from datetime import datetime
from typing import List, Dict, Tuple, Optional, Set
from dataclasses import dataclass, field, asdict

# Add user site-packages path for installed crypto libs
sys.path.insert(0, '/Users/agenticos/Library/Python/3.9/lib/python/site-packages')

try:
    import ecdsa
    from mnemonic import Mnemonic
    import base58
    import bech32
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Please install: pip3 install ecdsa base58 mnemonic bech32 requests")
    sys.exit(1)

# ============================================================================
# CONSTANTS & CONFIGURATION
# ============================================================================

BTC_COIN_TYPE = 0
LTC_COIN_TYPE = 2
HARDENED = 0x80000000

DERIVATION_PATHS = {
    'btc': {
        'bip44_legacy':     (44, BTC_COIN_TYPE, 0, 0),
        'bip49_segwit':     (49, BTC_COIN_TYPE, 0, 0),
        'bip84_native_segwit': (84, BTC_COIN_TYPE, 0, 0),
    },
    'ltc': {
        'bip44_legacy':     (44, LTC_COIN_TYPE, 0, 0),
        'bip49_segwit':     (49, LTC_COIN_TYPE, 0, 0),
        'bip84_native_segwit': (84, LTC_COIN_TYPE, 0, 0),
    }
}

NETWORK_VERSIONS = {
    'btc': {'p2pkh': b'\x00', 'p2sh':  b'\x05'},
    'ltc': {'p2pkh': b'\x30', 'p2sh':  b'\x32'},
}

WIF_VERSIONS = {'btc': b'\x80', 'ltc': b'\xb0'}

# ============================================================================
# BIP32 / BIP39 KEY DERIVATION
# ============================================================================

class BIP32Key:
    def __init__(self, key: bytes, chain_code: bytes, depth: int = 0,
                 index: int = 0, fingerprint: bytes = b'\x00\x00\x00\x00'):
        if len(key) != 32:
            raise ValueError("Key must be 32 bytes")
        if len(chain_code) != 32:
            raise ValueError("Chain code must be 32 bytes")
        self.key = key
        self.chain_code = chain_code
        self.depth = depth
        self.index = index
        self.fingerprint = fingerprint

    def get_public_key(self) -> bytes:
        sk = ecdsa.SigningKey.from_string(self.key, curve=ecdsa.SECP256k1)
        vk = sk.get_verifying_key()
        x = vk.pubkey.point.x()
        y = vk.pubkey.point.y()
        prefix = b'\x02' if (y % 2 == 0) else b'\x03'
        return prefix + x.to_bytes(32, 'big')

    def derive_child(self, index: int, hardened: bool = False) -> 'BIP32Key':
        if hardened:
            index |= HARDENED
            data = b'\x00' + self.key + struct.pack('>I', index)
        else:
            data = self.get_public_key() + struct.pack('>I', index)
        hmac_result = hmac.new(self.chain_code, data, hashlib.sha512).digest()
        left = hmac_result[:32]
        right = hmac_result[32:]
        key_int = int.from_bytes(self.key, 'big')
        left_int = int.from_bytes(left, 'big')
        curve_order = ecdsa.SECP256k1.order
        child_key = ((key_int + left_int) % curve_order).to_bytes(32, 'big')
        pub_key = self.get_public_key()
        fingerprint = hashlib.new('ripemd160', hashlib.sha256(pub_key).digest()).digest()[:4]
        return BIP32Key(child_key, right, self.depth + 1, index, fingerprint)

    def derive_path(self, path_indices: List[int]) -> 'BIP32Key':
        key = self
        for idx in path_indices:
            hardened = idx >= HARDENED
            key = key.derive_child(idx & ~HARDENED, hardened)
        return key


def mnemonic_to_seed(mnemonic: str, passphrase: str = "") -> bytes:
    mnemo = Mnemonic("english")
    if not mnemo.check(mnemonic):
        raise ValueError("Invalid mnemonic checksum!")
    return mnemo.to_seed(mnemonic, passphrase)


def seed_to_master_key(seed: bytes) -> BIP32Key:
    hmac_result = hmac.new(b"Bitcoin seed", seed, hashlib.sha512).digest()
    return BIP32Key(hmac_result[:32], hmac_result[32:])


# ============================================================================
# ADDRESS GENERATION
# ============================================================================

def hash160(data: bytes) -> bytes:
    return hashlib.new('ripemd160', hashlib.sha256(data).digest()).digest()


def pubkey_to_p2pkh(pubkey: bytes, network: str = 'btc') -> str:
    h = hash160(pubkey)
    version = NETWORK_VERSIONS[network]['p2pkh']
    return base58.b58encode(version + h + hashlib.sha256(hashlib.sha256(version + h).digest()).digest()[:4]).decode()


def pubkey_to_p2sh_p2wpkh(pubkey: bytes, network: str = 'btc') -> str:
    witness_program = b'\x00\x14' + hash160(pubkey)
    script_hash = hash160(witness_program)
    version = NETWORK_VERSIONS[network]['p2sh']
    payload = version + script_hash
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return base58.b58encode(payload + checksum).decode()


def pubkey_to_p2wpkh(pubkey: bytes, network: str = 'btc') -> str:
    h = hash160(pubkey)
    prefix = 'bc' if network == 'btc' else 'ltc'
    return bech32.bech32_encode(prefix, [0] + _convert_bits(h, 8, 5))


def _convert_bits(data: bytes, from_bits: int, to_bits: int, pad: bool = True) -> List[int]:
    acc = 0
    bits = 0
    ret = []
    maxv = (1 << to_bits) - 1
    max_acc = (1 << (from_bits + to_bits - 1)) - 1
    for value in data:
        acc = ((acc << from_bits) | value) & max_acc
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (to_bits - bits)) & maxv)
    return ret


def derive_addresses(mnemonic: str, coin: str, path_type: str,
                     start: int = 0, count: int = 20, change: int = 0) -> List[Dict]:
    seed = mnemonic_to_seed(mnemonic)
    master = seed_to_master_key(seed)
    path = DERIVATION_PATHS[coin][path_type]
    path_indices = [
        HARDENED + path[0],
        HARDENED + path[1],
        HARDENED + 0,
        change,
    ]
    account_key = master.derive_path(path_indices)
    results = []
    for i in range(start, start + count):
        child = account_key.derive_child(i)
        pubkey = child.get_public_key()
        if path_type == 'bip44_legacy':
            address = pubkey_to_p2pkh(pubkey, coin)
        elif path_type == 'bip49_segwit':
            address = pubkey_to_p2sh_p2wpkh(pubkey, coin)
        elif path_type == 'bip84_native_segwit':
            address = pubkey_to_p2wpkh(pubkey, coin)
        else:
            continue
        chain_label = '1' if change == 1 else '0'
        results.append({
            'index': i,
            'address': address,
            'path': f"m/{path[0]}'/{path[1]}'/0'/{chain_label}/{i}",
            'type': path_type,
            'pubkey': pubkey.hex(),
        })
    return results


# ============================================================================
# BLOCKCHAIN API CLIENTS
# ============================================================================

class BlockchainAPI:
    def __init__(self):
        self.ctx = ssl.create_default_context()

    def _fetch(self, url: str, retries: int = 3) -> Optional[Dict]:
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'WalletAnalyzer/1.0',
                    'Accept': 'application/json'
                })
                with urllib.request.urlopen(req, timeout=15, context=self.ctx) as resp:
                    data = resp.read()
                    return json.loads(data)
            except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as e:
                if attempt < retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                else:
                    print(f"  API Error after {retries} retries: {e}")
                    return None
        return None


class MempoolSpaceAPI(BlockchainAPI):
    BASE = "https://mempool.space/api"
    def get_address_info(self, address: str) -> Optional[Dict]:
        return self._fetch(f"{self.BASE}/address/{address}")
    def get_address_txs(self, address: str) -> Optional[List[Dict]]:
        return self._fetch(f"{self.BASE}/address/{address}/txs")
    def get_tx(self, txid: str) -> Optional[Dict]:
        return self._fetch(f"{self.BASE}/tx/{txid}")


class BlockCypherAPI(BlockchainAPI):
    def get_address_info(self, coin: str, address: str) -> Optional[Dict]:
        token = "c0f9a7b5e6d34f8a9b2c1d3e4f5a6b7c"
        network = 'btc' if coin == 'btc' else 'ltc'
        base = f"https://api.blockcypher.com/v1/{network}/main"
        return self._fetch(f"{base}/addrs/{address}?limit=50&token={token}")


class ChainSoAPI(BlockchainAPI):
    def get_address_txs(self, coin: str, address: str) -> Optional[Dict]:
        network = 'BTC' if coin == 'btc' else 'LTC'
        return self._fetch(f"https://chain.so/api/v2/get_tx_received/{network}/{address}")
    def get_tx_spent(self, coin: str, address: str) -> Optional[Dict]:
        network = 'BTC' if coin == 'btc' else 'LTC'
        return self._fetch(f"https://chain.so/api/v2/get_tx_spent/{network}/{address}")


class BlockchainInfoAPI(BlockchainAPI):
    def get_address_info(self, address: str) -> Optional[Dict]:
        return self._fetch(f"https://blockchain.info/rawaddr/{address}?limit=50")


# ============================================================================
# TRANSACTION MODELS
# ============================================================================

@dataclass
class TxInput:
    txid: str
    vout: int
    value: float = 0.0
    address: str = ""
    script_sig: str = ""
    sequence: int = 0


@dataclass
class TxOutput:
    index: int
    value: float
    address: str
    script_pubkey: str
    is_ours: bool = False


@dataclass
class Transaction:
    txid: str
    block_height: Optional[int]
    block_time: Optional[int]
    fee: float
    size: int
    version: int
    locktime: int
    inputs: List[TxInput] = field(default_factory=list)
    outputs: List[TxOutput] = field(default_factory=list)
    direction: str = ""
    amount_in: float = 0.0
    amount_out: float = 0.0
    net_flow: float = 0.0
    is_self_withdrawal: bool = False
    is_vendor_withdrawal: bool = False
    external_destinations: List[Dict] = field(default_factory=list)


# ============================================================================
# WALLET SCANNER
# ============================================================================

class WalletScanner:
    def __init__(self, mnemonic: str, coin: str = 'btc', gap_limit: int = 20):
        self.mnemonic = mnemonic
        self.coin = coin.lower()
        self.gap_limit = gap_limit
        self.all_addresses: Dict[str, Dict] = {}
        self.our_addresses: Set[str] = set()
        self.transactions: Dict[str, Transaction] = {}
        self.mempool = MempoolSpaceAPI() if coin == 'btc' else None
        self.blockcypher = BlockCypherAPI()
        self.chainso = ChainSoAPI()
        self.blockchaininfo = BlockchainInfoAPI() if coin == 'btc' else None

    def _scan_path(self, path_name: str, max_scan: int, change: int = 0) -> List[Dict]:
        found = []
        consecutive_empty = 0
        idx = 0
        while idx < max_scan and consecutive_empty < self.gap_limit:
            batch = derive_addresses(self.mnemonic, self.coin, path_name, idx, 5, change=change)
            for addr_info in batch:
                address = addr_info['address']
                has_tx = False
                if self.coin == 'btc' and self.mempool:
                    info = self.mempool.get_address_info(address)
                    if info and info.get('chain_stats', {}).get('tx_count', 0) > 0:
                        has_tx = True
                if not has_tx:
                    info = self.blockcypher.get_address_info(self.coin, address)
                    if info and info.get('n_tx', 0) > 0:
                        has_tx = True
                if has_tx:
                    chain_label = 'change' if change == 1 else 'external'
                    print(f"    [FOUND] {address} ({path_name}, {chain_label}, index {addr_info['index']})")
                    self.all_addresses[address] = addr_info
                    self.our_addresses.add(address)
                    found.append(addr_info)
                    consecutive_empty = 0
                else:
                    consecutive_empty += 1
            idx += 5
            time.sleep(0.2)
        return found

    def discover_addresses(self, max_scan: int = 100) -> List[Dict]:
        print(f"\n[DISCOVERY] Scanning {self.coin.upper()} addresses...")
        found = []
        for path_name in DERIVATION_PATHS[self.coin]:
            print(f"\n  Path: {path_name} (external chain)")
            found += self._scan_path(path_name, max_scan, change=0)
            print(f"  Path: {path_name} (change chain)")
            found += self._scan_path(path_name, max_scan, change=1)
        print(f"\n[DISCOVERY] Total addresses with transactions: {len(found)}")
        return found

    def fetch_transactions(self) -> List[Transaction]:
        print(f"\n[TX FETCH] Retrieving transaction history...")
        tx_map: Dict[str, Dict] = {}
        for address in self.our_addresses:
            print(f"  Fetching txs for {address}...")
            if self.coin == 'btc' and self.mempool:
                txs = self.mempool.get_address_txs(address)
                if txs:
                    for tx in txs:
                        tx_map[tx['txid']] = tx
            if self.coin == 'btc' and self.blockchaininfo:
                info = self.blockchaininfo.get_address_info(address)
                if info and 'txs' in info:
                    for tx in info['txs']:
                        tx_map[tx['hash']] = tx
            received = self.chainso.get_tx_spent(self.coin, address)
            if received and received.get('status') == 'success':
                for tx in received.get('data', {}).get('txs', []):
                    tx_map[tx['txid']] = tx
            time.sleep(0.3)
        results = []
        for txid, raw in tx_map.items():
            tx = self._parse_transaction(txid, raw)
            if tx:
                self.transactions[txid] = tx
                results.append(tx)
        print(f"[TX FETCH] Found {len(results)} unique transactions")
        return results

    def _parse_transaction(self, txid: str, raw: Dict) -> Optional[Transaction]:
        try:
            if 'txid' in raw:
                txid = raw['txid']
            elif 'hash' in raw:
                txid = raw['hash']
            tx = Transaction(
                txid=txid,
                block_height=raw.get('block_height') or raw.get('block_height'),
                block_time=raw.get('block_time') or raw.get('time'),
                fee=raw.get('fee', 0) / 1e8 if isinstance(raw.get('fee'), int) else raw.get('fee', 0),
                size=raw.get('size', 0),
                version=raw.get('version', 1),
                locktime=raw.get('locktime', 0),
            )
            vin = raw.get('vin', [])
            for inp in vin:
                tx_in = TxInput(
                    txid=inp.get('txid', ''),
                    vout=inp.get('vout', 0),
                    value=inp.get('prevout', {}).get('value', 0) / 1e8 if isinstance(inp.get('prevout', {}).get('value'), int) else 0,
                    address=inp.get('prevout', {}).get('scriptpubkey_address', '') if isinstance(inp.get('prevout'), dict) else '',
                    script_sig=inp.get('scriptsig', ''),
                    sequence=inp.get('sequence', 0),
                )
                tx.inputs.append(tx_in)
            vout = raw.get('vout', [])
            for i, out in enumerate(vout):
                spk = out.get('scriptpubkey', out.get('scriptPubKey', {}))
                if isinstance(spk, dict):
                    addr = spk.get('address', '') or (spk.get('addresses', [''])[0] if isinstance(spk.get('addresses'), list) else '')
                else:
                    addr = out.get('addr', '') or out.get('address', '')
                value = out.get('value', 0)
                if isinstance(value, int):
                    value = value / 1e8
                elif isinstance(value, str):
                    value = float(value)
                tx_out = TxOutput(
                    index=i,
                    value=value,
                    address=addr,
                    script_pubkey=spk.get('hex', '') if isinstance(spk, dict) else str(spk),
                    is_ours=(addr in self.our_addresses)
                )
                tx.outputs.append(tx_out)
            return tx
        except Exception as e:
            print(f"  Error parsing tx {txid}: {e}")
            return None

    def classify_transactions(self):
        print(f"\n[CLASSIFY] Analyzing transaction directions...")
        for txid, tx in self.transactions.items():
            amount_from_us = sum(inp.value for inp in tx.inputs if inp.address in self.our_addresses)
            amount_to_us = sum(out.value for out in tx.outputs if out.is_ours)
            amount_to_external = sum(out.value for out in tx.outputs if not out.is_ours and out.address)
            tx.amount_in = amount_from_us
            tx.amount_out = amount_to_us
            tx.net_flow = amount_to_us - amount_from_us
            if amount_from_us > 0 and amount_to_us > 0 and amount_to_external == 0:
                tx.direction = 'self'
                tx.is_self_withdrawal = True
            elif amount_from_us > 0:
                tx.direction = 'outgoing'
                tx.is_vendor_withdrawal = True
                for out in tx.outputs:
                    if not out.is_ours and out.address:
                        tx.external_destinations.append({
                            'address': out.address,
                            'amount': out.value,
                            'type': 'unknown'
                        })
            else:
                tx.direction = 'incoming'
            if tx.direction == 'outgoing' and len(tx.external_destinations) == 0:
                tx.direction = 'self'
                tx.is_self_withdrawal = True
                tx.is_vendor_withdrawal = False

    def get_summary(self) -> Dict:
        incoming = [tx for tx in self.transactions.values() if tx.direction == 'incoming']
        outgoing = [tx for tx in self.transactions.values() if tx.direction == 'outgoing']
        self_txs = [tx for tx in self.transactions.values() if tx.direction == 'self']
        total_received = sum(tx.amount_out for tx in incoming)
        total_sent = sum(tx.amount_in for tx in outgoing)
        total_self_moved = sum(tx.amount_in for tx in self_txs)
        vendor_map: Dict[str, float] = {}
        for tx in outgoing:
            for dest in tx.external_destinations:
                addr = dest['address']
                vendor_map[addr] = vendor_map.get(addr, 0.0) + dest['amount']
        return {
            'coin': self.coin.upper(),
            'addresses_found': len(self.our_addresses),
            'total_transactions': len(self.transactions),
            'incoming_count': len(incoming),
            'outgoing_count': len(outgoing),
            'self_transfer_count': len(self_txs),
            'total_received': total_received,
            'total_sent': total_sent,
            'total_self_moved': total_self_moved,
            'net_balance': total_received - total_sent,
            'vendor_destinations': vendor_map,
        }


# ============================================================================
# REPORT GENERATOR
# ============================================================================

class ReportGenerator:
    def __init__(self, scanner: WalletScanner):
        self.scanner = scanner

    def generate(self, output_path: str):
        summary = self.scanner.get_summary()
        txs = list(self.scanner.transactions.values())
        txs_sorted = sorted(txs, key=lambda t: t.block_time or 0)

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Wallet Analysis Report - {summary['coin']}</title>
<style>
:root {{ --bg: #0f1419; --card: #161b22; --border: #30363d; --text: #c9d1d9; --text-dim: #8b949e; --accent: #58a6ff; --accent-green: #3fb950; --accent-red: #f85149; --accent-yellow: #d29922; }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; padding: 20px; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
h1 {{ color: var(--accent); margin-bottom: 8px; font-size: 28px; }}
h2 {{ color: var(--text); margin: 30px 0 15px; font-size: 20px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }}
.subtitle {{ color: var(--text-dim); margin-bottom: 25px; font-size: 14px; }}
.summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 30px; }}
.stat-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 18px; }}
.stat-label {{ font-size: 12px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; }}
.stat-value {{ font-size: 24px; font-weight: 600; margin-top: 5px; }}
.stat-value.positive {{ color: var(--accent-green); }}
.stat-value.negative {{ color: var(--accent-red); }}
.stat-value.neutral {{ color: var(--accent); }}
table {{ width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; font-size: 13px; }}
th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--border); }}
th {{ background: #1c2128; color: var(--text-dim); font-weight: 500; font-size: 11px; text-transform: uppercase; }}
tr:hover {{ background: #1c2128; }}
.tx-incoming {{ color: var(--accent-green); }}
.tx-outgoing {{ color: var(--accent-red); }}
.tx-self {{ color: var(--accent-yellow); }}
.mono {{ font-family: 'SF Mono', Monaco, monospace; font-size: 11px; }}
.address-tag {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 10px; background: #21262d; color: var(--text-dim); margin-left: 5px; }}
.destination-list {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 15px; }}
.dest-item {{ display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid var(--border); }}
.dest-item:last-child {{ border-bottom: none; }}
.dest-addr {{ font-family: monospace; font-size: 12px; color: var(--accent); }}
.dest-amount {{ font-weight: 600; color: var(--accent-red); }}
.flow-diagram {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-top: 20px; }}
.flow-step {{ display: flex; align-items: center; gap: 15px; margin: 10px 0; }}
.flow-node {{ padding: 8px 16px; border-radius: 6px; font-size: 12px; font-weight: 500; }}
.flow-node.source {{ background: #238636; color: white; }}
.flow-node.target {{ background: #da3633; color: white; }}
.flow-node.self {{ background: #d29922; color: black; }}
.flow-arrow {{ color: var(--text-dim); font-size: 18px; }}
.flow-amount {{ font-weight: 600; font-size: 14px; }}
</style>
</head>
<body>
<div class="container">
<h1>🔍 Wallet Analysis Report</h1>
<p class="subtitle">{summary['coin']} | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {summary['addresses_found']} addresses discovered</p>

<h2>📊 Summary</h2>
<div class="summary-grid">
<div class="stat-card"><div class="stat-label">Total Transactions</div><div class="stat-value neutral">{summary['total_transactions']}</div></div>
<div class="stat-card"><div class="stat-label">Incoming</div><div class="stat-value positive">{summary['incoming_count']}</div></div>
<div class="stat-card"><div class="stat-label">Outgoing</div><div class="stat-value negative">{summary['outgoing_count']}</div></div>
<div class="stat-card"><div class="stat-label">Self Transfers</div><div class="stat-value neutral">{summary['self_transfer_count']}</div></div>
<div class="stat-card"><div class="stat-label">Total Received</div><div class="stat-value positive">{summary['total_received']:.8f} {summary['coin']}</div></div>
<div class="stat-card"><div class="stat-label">Total Sent</div><div class="stat-value negative">{summary['total_sent']:.8f} {summary['coin']}</div></div>
<div class="stat-card"><div class="stat-label">Net Balance</div><div class="stat-value {'positive' if summary['net_balance'] >= 0 else 'negative'}">{summary['net_balance']:.8f} {summary['coin']}</div></div>
<div class="stat-card"><div class="stat-label">Self-Moved</div><div class="stat-value neutral">{summary['total_self_moved']:.8f} {summary['coin']}</div></div>
</div>

<h2>📋 Discovered Addresses</h2>
<table><tr><th>Path</th><th>Type</th><th>Address</th><th>Index</th></tr>
"""
        for addr, info in self.scanner.all_addresses.items():
            html += f"<tr><td class='mono'>{info['path']}</td><td><span class='address-tag'>{info['type']}</span></td><td class='mono'>{addr}</td><td>{info['index']}</td></tr>\n"
        html += "</table>\n"

        html += "<h2>💸 Transaction History</h2><table><tr><th>Date</th><th>TXID</th><th>Direction</th><th>Amount In</th><th>Amount Out</th><th>Net</th><th>Fee</th><th>Type</th></tr>\n"
        for tx in txs_sorted:
            dt = datetime.fromtimestamp(tx.block_time).strftime('%Y-%m-%d %H:%M') if tx.block_time else 'Pending'
            direction_class = f"tx-{tx.direction}"
            type_label = 'Vendor' if tx.is_vendor_withdrawal else ('Self' if tx.is_self_withdrawal else 'Deposit')
            type_color = 'var(--accent-red)' if tx.is_vendor_withdrawal else ('var(--accent-yellow)' if tx.is_self_withdrawal else 'var(--accent-green)')
            net_color = 'var(--accent-green)' if tx.net_flow > 0 else ('var(--accent-red)' if tx.net_flow < 0 else 'var(--text-dim)')
            html += f"<tr><td>{dt}</td><td class='mono'>{tx.txid[:16]}...</td><td class='{direction_class}'>{tx.direction.upper()}</td><td>{tx.amount_in:.8f}</td><td>{tx.amount_out:.8f}</td><td style='color: {net_color}'>{tx.net_flow:+.8f}</td><td>{tx.fee:.8f}</td><td style='color: {type_color}'>{type_label}</td></tr>\n"
        html += "</table>\n"

        html += "<h2>🎯 Vendor Withdrawal Destinations</h2><div class='destination-list'>\n"
        if summary['vendor_destinations']:
            sorted_dests = sorted(summary['vendor_destinations'].items(), key=lambda x: x[1], reverse=True)
            for addr, amount in sorted_dests:
                html += f"<div class='dest-item'><span class='dest-addr'>{addr}</span><span class='dest-amount'>-{amount:.8f} {summary['coin']}</span></div>\n"
        else:
            html += '<p style="color: var(--text-dim);">No vendor withdrawals detected.</p>'
        html += "</div>\n"

        html += "<h2>🗺️ Fund Flow Map</h2><div class='flow-diagram'>\n"
        outgoing_txs = [tx for tx in txs_sorted if tx.direction == 'outgoing']
        for tx in outgoing_txs[:20]:
            dt = datetime.fromtimestamp(tx.block_time).strftime('%Y-%m-%d') if tx.block_time else 'Pending'
            for dest in tx.external_destinations[:3]:
                html += f"<div class='flow-step'><div class='flow-node source'>Your Wallet</div><span class='flow-arrow'>&#8594;</span><div class='flow-amount'>{dest['amount']:.4f} {summary['coin']}</div><span class='flow-arrow'>&#8594;</span><div class='flow-node target'>{dest['address'][:20]}...</div><span style='color: var(--text-dim); font-size: 11px; margin-left: 10px;'>{dt}</span></div>\n"
        self_txs = [tx for tx in txs_sorted if tx.direction == 'self']
        if self_txs:
            html += '<h3 style="margin-top: 20px; color: var(--accent-yellow);">Self-Transfers (Consolidation)</h3>'
            for tx in self_txs[:10]:
                dt = datetime.fromtimestamp(tx.block_time).strftime('%Y-%m-%d') if tx.block_time else 'Pending'
                html += f"<div class='flow-step'><div class='flow-node source'>Your Address</div><span class='flow-arrow'>&#8594;</span><div class='flow-amount'>{tx.amount_in:.4f} {summary['coin']}</div><span class='flow-arrow'>&#8594;</span><div class='flow-node self'>Your Address</div><span style='color: var(--text-dim); font-size: 11px; margin-left: 10px;'>{dt}</span></div>\n"
        html += "</div>\n"

        html += "<h2>📈 Transaction Details (JSON)</h2><pre style='background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 15px; overflow-x: auto; font-size: 11px; color: var(--text-dim);'>\n"
        export_data = []
        for tx in txs_sorted:
            export_data.append({
                'txid': tx.txid, 'direction': tx.direction, 'amount_in': tx.amount_in,
                'amount_out': tx.amount_out, 'net_flow': tx.net_flow, 'fee': tx.fee,
                'is_self_withdrawal': tx.is_self_withdrawal,
                'is_vendor_withdrawal': tx.is_vendor_withdrawal,
                'block_time': tx.block_time, 'external_destinations': tx.external_destinations,
            })
        html += json.dumps(export_data, indent=2)
        html += "\n</pre>\n</div>\n</body>\n</html>"

        with open(output_path, 'w') as f:
            f.write(html)
        print(f"\n[REPORT] HTML report saved to: {output_path}")

    def generate_csv(self, output_path: str):
        txs = sorted(self.scanner.transactions.values(), key=lambda t: t.block_time or 0)
        with open(output_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['txid', 'date', 'direction', 'amount_in', 'amount_out',
                           'net_flow', 'fee', 'type', 'block_height', 'destinations'])
            for tx in txs:
                dt = datetime.fromtimestamp(tx.block_time).strftime('%Y-%m-%d %H:%M:%S') if tx.block_time else ''
                tx_type = 'Vendor' if tx.is_vendor_withdrawal else ('Self' if tx.is_self_withdrawal else 'Deposit')
                dests = '; '.join(f"{d['address']}:{d['amount']:.8f}" for d in tx.external_destinations)
                writer.writerow([
                    tx.txid, dt, tx.direction, tx.amount_in, tx.amount_out,
                    tx.net_flow, tx.fee, tx_type, tx.block_height or '', dests
                ])
        print(f"[REPORT] CSV report saved to: {output_path}")


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Crypto Wallet Recovery & Transaction Analysis',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 wallet_analyzer.py --mnemonic "word1 word2 ... word12" --coin btc
  python3 wallet_analyzer.py --mnemonic-file seed.txt --coin ltc --depth 50
        """
    )
    parser.add_argument('--mnemonic', type=str, help='BIP39 mnemonic phrase')
    parser.add_argument('--mnemonic-file', type=str, help='File containing mnemonic')
    parser.add_argument('--coin', type=str, choices=['btc', 'ltc'], default='btc', help='Cryptocurrency (default: btc)')
    parser.add_argument('--depth', type=int, default=100, help='Max address index to scan (default: 100)')
    parser.add_argument('--gap', type=int, default=20, help='Gap limit (default: 20)')
    parser.add_argument('--output', type=str, default='wallet_report.html', help='Output HTML path')
    parser.add_argument('--csv', type=str, default='wallet_report.csv', help='Output CSV path')
    parser.add_argument('--passphrase', type=str, default='', help='Optional BIP39 passphrase')
    args = parser.parse_args()

    if args.mnemonic_file:
        with open(args.mnemonic_file, 'r') as f:
            mnemonic = f.read().strip()
    elif args.mnemonic:
        mnemonic = args.mnemonic
    else:
        print("Error: Provide --mnemonic or --mnemonic-file")
        parser.print_help()
        sys.exit(1)

    print("=" * 60)
    print("  CRYPTO WALLET RECOVERY & ANALYSIS SYSTEM")
    print("=" * 60)
    print(f"Coin: {args.coin.upper()}")
    print(f"Scan depth: {args.depth}")
    print(f"Gap limit: {args.gap}")

    scanner = WalletScanner(mnemonic, args.coin, gap_limit=args.gap)
    scanner.discover_addresses(max_scan=args.depth)

    if not scanner.our_addresses:
        print("\n[!] No addresses with transaction history found.")
        print("    Try increasing --depth or verify your mnemonic.")
        sys.exit(0)

    scanner.fetch_transactions()

    if not scanner.transactions:
        print("\n[!] No transactions found for discovered addresses.")
        sys.exit(0)

    scanner.classify_transactions()
    summary = scanner.get_summary()

    print("\n" + "=" * 60)
    print("  ANALYSIS SUMMARY")
    print("=" * 60)
    print(f"  Addresses Found:     {summary['addresses_found']}")
    print(f"  Total Transactions:  {summary['total_transactions']}")
    print(f"  Incoming:            {summary['incoming_count']}")
    print(f"  Outgoing:            {summary['outgoing_count']}")
    print(f"  Self-Transfers:      {summary['self_transfer_count']}")
    print(f"  Total Received:      +{summary['total_received']:.8f} {summary['coin']}")
    print(f"  Total Sent:          -{summary['total_sent']:.8f} {summary['coin']}")
    print(f"  Net Balance:         {summary['net_balance']:.8f} {summary['coin']}")
    print(f"  Self-Moved:          {summary['total_self_moved']:.8f} {summary['coin']}")

    print("\n  VENDOR WITHDRAWAL DESTINATIONS:")
    if summary['vendor_destinations']:
        sorted_dests = sorted(summary['vendor_destinations'].items(), key=lambda x: x[1], reverse=True)
        for addr, amount in sorted_dests[:10]:
            print(f"    → {addr}: {amount:.8f} {summary['coin']}")
    else:
        print("    (none)")

    report = ReportGenerator(scanner)
    report.generate(args.output)
    report.generate_csv(args.csv)

    print("\n" + "=" * 60)
    print("  ANALYSIS COMPLETE")
    print("=" * 60)
    print(f"  HTML Report: {args.output}")
    print(f"  CSV Report:  {args.csv}")


if __name__ == '__main__':
    main()
