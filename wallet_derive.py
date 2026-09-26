#!/usr/bin/env python3
"""Multi-wallet address derivation for seeds and xpubs.

Supports two wallet input types (configured in wallets.json — see
wallets.example.json; secrets are passed via env var NAMES, never inline):

- "mnemonic": BIP39 seed → full sweep of address types (bip44/bip49/bip84) ×
  accounts × external/internal chains, reusing wallet_analyzer derivation.
- "xpub":     watch-only account-level extended public key (xpub/ypub/zpub,
              tpub/upub/vpub, Ltub/Mtub) → external (and internal, if the
              config requests it) chain scan without any private material.

Gap rule (user-specified): scan each chain in windows; abandon the chain
after `gap` consecutive unfunded addresses. A type with zero activity in its
first window is marked not-used and skipped early.

Outputs per wallet under results/<name>/:
  found_addresses_<coin>.json   funded addresses with path metadata
  derive_report.json            per-type/account/chain verdicts

Usage:
  python3 wallet_derive.py                # all wallets in wallets.json
  python3 wallet_derive.py --wallet main  # one wallet
"""

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import sys
import time
import urllib.request

import ecdsa

from wallet_config import (ACCOUNT_DEPTH, FALLBACK_APIS, GAP_LIMIT, PROJ,
                           require_mnemonic)
from wallet_analyzer import (HARDENED, derive_addresses, hash160,
                             pubkey_to_p2pkh, pubkey_to_p2sh_p2wpkh,
                             pubkey_to_p2wpkh)

RESULTS = os.path.join(PROJ, "results")

# version byte -> (network, script)
XPUB_VERSIONS = {
    0x0488B21E: ("btc", "p2pkh"),
    0x049D7CB2: ("btc", "p2sh-p2wpkh"),
    0x02AA7ED3: ("btc", "p2wpkh"),
    0x043587CF: ("tbtc", "p2pkh"),
    0x044A5262: ("tbtc", "p2sh-p2wpkh"),
    0x045F1CF6: ("tbtc", "p2wpkh"),
    0x019DA462: ("ltc", "p2pkh"),   # Ltub
    0x01B26EF6: ("ltc", "p2sh-p2wpkh"),  # Mtub
}
SCRIPT_TO_FUNCS = {
    "p2pkh": pubkey_to_p2pkh,
    "p2sh-p2wpkh": pubkey_to_p2sh_p2wpkh,
    "p2wpkh": pubkey_to_p2wpkh,
}
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
TYPE_SCRIPT = {"bip44_legacy": "p2pkh", "bip49_segwit": "p2sh-p2wpkh",
               "bip84_native_segwit": "p2wpkh"}


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + B58.index(ch)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * (len(s) - len(s.lstrip("1"))) + body


class BIP32WatchOnly:
    """Public-key-only BIP32 node: non-hardened CKD (watch-only)."""

    def __init__(self, pubkey: bytes, chain_code: bytes, depth: int = 0,
                 index: int = 0, fingerprint: bytes = b"\x00" * 4):
        assert len(pubkey) == 33 and len(chain_code) == 32
        self.pubkey = pubkey
        self.chain_code = chain_code
        self.depth = depth
        self.index = index
        self.fingerprint = fingerprint

    def derive_child(self, index: int) -> "BIP32WatchOnly":
        import hmac as _hmac
        import struct
        data = self.pubkey + struct.pack(">I", index)
        I = _hmac.new(self.chain_code, data, hashlib.sha512).digest()
        left = int.from_bytes(I[:32], "big")
        curve = ecdsa.SECP256k1
        parent = ecdsa.VerifyingKey.from_string(self.pubkey, curve=curve).pubkey.point
        child_point = parent + left * curve.generator
        child_vk = ecdsa.VerifyingKey.from_public_point(child_point, curve=curve)
        x, y = child_vk.pubkey.point.x(), child_vk.pubkey.point.y()
        pub = (b"\x02" if y % 2 == 0 else b"\x03") + x.to_bytes(32, "big")
        fp = hashlib.new("ripemd160", hashlib.sha256(self.pubkey).digest()).digest()[:4]
        return BIP32WatchOnly(pub, I[32:], self.depth + 1, index, fp)


def xpub_to_watchkey(xpub: str):
    """Decode a serialized account xpub -> (BIP32WatchOnly, network, script)."""
    raw = b58decode(xpub.strip())
    if len(raw) != 82:
        raise ValueError(f"bad xpub length {len(raw)}")
    payload, checksum = raw[:78], raw[78:]
    if hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] != checksum:
        raise ValueError("xpub checksum mismatch")
    version = int.from_bytes(payload[0:4], "big")
    if version not in XPUB_VERSIONS:
        raise ValueError(f"unknown xpub version 0x{version:08X} — extend XPUB_VERSIONS")
    network, script = XPUB_VERSIONS[version]
    depth = payload[4]
    fingerprint = payload[5:9]
    index = int.from_bytes(payload[9:13], "big")
    chain_code = payload[13:45]
    key = payload[45:78]
    if key[0] != 0x02 and key[0] != 0x03:
        raise ValueError("not a public key (xprv given?) — watch-only needs the public xpub")
    node = BIP32WatchOnly(key, chain_code, depth, index, fingerprint)
    return node, network, script


def addresses_from_xpub(node: BIP32WatchOnly, script: str, network: str,
                        chain: int, count: int, start: int = 0):
    fn = SCRIPT_TO_FUNCS[script]
    chain_node = node.derive_child(chain)
    out = []
    for i in range(start, start + count):
        child = chain_node.derive_child(i)
        out.append({"index": i, "address": fn(child.pubkey, network)})
    return out


def _get_json(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "wallet-analizer/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def funded_check(coin: str, address: str, timeout=10):
    """Return True if the address has any confirmed or mempool txs. Retries
    across all configured sources; an unreachable source raises — an address
    is only 'unfunded' when a source VERIFIES tx_count == 0."""
    last = None
    for attempt in range(4):
        for base in FALLBACK_APIS[coin]:
            try:
                d = _get_json(f"{base}/address/{address}", timeout)
                ts = (d.get("chain_stats") or {}).get("tx_count", 0) + \
                     (d.get("mempool_stats") or {}).get("tx_count", 0)
                return ts > 0
            except Exception as e:
                last = f"{base}: {e}"
        time.sleep(2.0 * (attempt + 1))  # source-wide backoff (5xx/429 waves)
    raise RuntimeError(f"no source verified {address}: {last}")


def scan_chain_funded(coin, gen_batch, gap):
    """gen_batch(start, count) -> [{address,...}]. Walk windows of 100,
    abandoning the chain after `gap` consecutive unfunded addresses.
    Returns (funded_entries, scanned, hit_gap)."""
    funded, scanned = [], 0
    consecutive_empty = 0
    start = 0
    WINDOW = 100
    while consecutive_empty < gap:
        batch = gen_batch(start, WINDOW)
        if not batch:
            break
        with cf.ThreadPoolExecutor(24) as ex:
            flags = list(ex.map(lambda b: funded_check(coin, b["address"]), batch))
        scanned += len(batch)
        any_hit = False
        for b, hit in zip(batch, flags):
            if hit:
                any_hit = True
                funded.append(b)
        if not any_hit:
            consecutive_empty += len(batch)
            # early-exit: first window with zero activity -> type unused
            if start == 0:
                return funded, scanned, True
        else:
            consecutive_empty = 0
        start += WINDOW
    return funded, scanned, consecutive_empty >= gap


def run_wallet(cfg, gap, types, accounts):
    name = cfg["name"]
    outdir = os.path.join(RESULTS, name)
    os.makedirs(outdir, exist_ok=True)
    report = {"wallet": name, "type": cfg["type"], "chains": []}

    if cfg["type"] == "mnemonic":
        mnemonic = os.environ.get(cfg.get("env", "WALLET_MNEMONIC"), "").strip()
        if not mnemonic:
            print(f"[{name}] env {cfg.get('env','WALLET_MNEMONIC')} not set, skipping")
            return
        coins = cfg.get("coins", ["btc", "ltc"])
        for coin in coins:
            found = []
            for ptype in types:
                script = TYPE_SCRIPT[ptype]
                for acct in range(accounts):
                    for chain in (0, 1):
                        def gen(start, count, p=ptype, a=acct, c=chain):
                            return derive_addresses(mnemonic, coin, p, start=start,
                                                    count=count, change=c, account=a)
                        f, scanned, hit_gap = scan_chain_funded(coin, gen, gap)
                        report["chains"].append({"coin": coin, "type": ptype,
                                                 "account": acct, "chain": chain,
                                                 "scanned": scanned,
                                                 "funded": len(f), "hit_gap": hit_gap})
                        print(f"[{name}] {coin} {ptype} acct{a} chain{chain}: "
                              f"scanned={scanned} funded={len(f)}", flush=True)
                        found.extend(f)
            path = os.path.join(outdir, f"found_addresses_{coin}.json")
            json.dump(found, open(path, "w"))
            print(f"[{name}] wrote {len(found)} funded {coin} addresses", flush=True)

    elif cfg["type"] == "xpub":
        xpub = os.environ.get(cfg.get("env", "XPUB"), "").strip()
        if not xpub:
            print(f"[{name}] env {cfg.get('env','XPUB')} not set, skipping")
            return
        node, network, script = xpub_to_watchkey(xpub)
        if cfg.get("script"):
            script = cfg["script"]
        if cfg.get("network"):
            network = cfg["network"]
        coin = "ltc" if network == "ltc" else "btc"
        found = []
        chains = (0, 1) if cfg.get("include_internal", True) else (0,)
        for chain in chains:
            def gen(start, count, c=chain):
                return addresses_from_xpub(node, script, network, c, count, start)
            f, scanned, hit_gap = scan_chain_funded(coin, gen, gap)
            report["chains"].append({"coin": coin, "script": script, "chain": chain,
                                     "scanned": scanned, "funded": len(f),
                                     "hit_gap": hit_gap})
            print(f"[{name}] {coin} {script} chain{chain}: scanned={scanned} "
                  f"funded={len(f)}", flush=True)
            found.extend(f)
        path = os.path.join(outdir, f"found_addresses_{coin}.json")
        json.dump(found, open(path, "w"))
        print(f"[{name}] wrote {len(found)} funded {coin} addresses", flush=True)

    json.dump(report, open(os.path.join(outdir, "derive_report.json"), "w"), indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wallet", default=None)
    ap.add_argument("--gap", type=int, default=int(os.environ.get("GAP_LIMIT", "500")))
    ap.add_argument("--types", default="bip44_legacy,bip49_segwit,bip84_native_segwit")
    ap.add_argument("--accounts", type=int, default=ACCOUNT_DEPTH)
    args = ap.parse_args()

    cfg_path = os.path.join(PROJ, "wallets.json")
    if not os.path.exists(cfg_path):
        print(f"{cfg_path} not found — copy wallets.example.json and fill it in")
        return 1
    wallets = json.load(open(cfg_path))["wallets"]
    for cfg in wallets:
        if args.wallet and cfg["name"] != args.wallet:
            continue
        run_wallet(cfg, args.gap, args.types.split(","), args.accounts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
