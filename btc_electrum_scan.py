#!/usr/bin/env python3
"""
BTC type x account-depth verification via Electrum servers (no REST API).

Why: every public REST source for BTC was unusable on 2026-09-13
(mempool.space unreachable, blockstream.info 700 req/h cap hit,
community mirrors flaky). Electrum servers (ElectrumX/Fulcrum) expose
blockchain.scripthash.get_history which needs no key and pipelines well.

Same owner rules as scan_all_types_accounts.py:
  legacy first -> bip49 -> bip84; first-500-empty marks type NOT USED;
  ext+change always; accounts 0..9; gap 5000 active / 100 deep.

Hard correctness rule: a server/query failure is NEVER counted as
"empty". Persistent failures abort the chain with status ERROR.

Output: scan_all_types_btc.json (same schema as the REST scanner).
"""

import json, os, sys, time, ssl, socket, hashlib, threading
from datetime import datetime

sys.path.insert(0, '/Users/agenticos/Library/Python/3.9/lib/python/site-packages')
sys.path.insert(0, '/Users/agenticos/Documents/kimi/workspace/wallet-analizer')

from wallet_analyzer import derive_addresses, hash160

MNEMONIC = "resemble praise oxygen rhythm rate rose mutual upon beach april behave cliff"
OUT_DIR = "/Users/agenticos/Documents/kimi/workspace/wallet-analizer"

TYPES = ['bip44_legacy', 'bip49_segwit', 'bip84_native_segwit']
ACCOUNTS = range(10)
GAP_ACTIVE = 5000
DETECT_LIMIT = 500
GAP_TAIL = 100
GAP_DEEP = 100
BATCH = 64

# (host, port, use_tls) -- probed reachable 2026-09-13
SERVERS = [
    ("electrum.emzy.de", 50002, True),
    ("bitcoin.lu.ke", 50001, False),
    ("bitcoin.lu.ke", 50002, True),
    ("electrum.arkade.sh", 50001, False),
    ("blackie.c3-soft.com", 57006, False),
]

PRIOR_FUNDED_FILE = 'found_addresses_btc_bip44_external.json'  # 4083 addrs, acct0 ext


class ElectrumConn:
    def __init__(self, host, port, tls):
        self.host, self.port, self.tls = host, port, tls
        self.sock = None
        self.f = None
        self._id = 0
        self.lock = threading.Lock()
        self.connect()

    def connect(self):
        self.close()
        raw = socket.create_connection((self.host, self.port), timeout=15)
        if self.tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            raw = ctx.wrap_socket(raw, server_hostname=self.host)
        raw.settimeout(30)
        self.sock = raw
        self.f = raw.makefile("r")
        self._rpc("server.version", ["btc-verify", "1.4"])

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None

    def _send(self, obj):
        self.sock.sendall((json.dumps(obj) + "\n").encode())

    def _read_matching(self, want_id):
        for _ in range(100000):
            line = self.f.readline()
            if not line:
                raise ConnectionError("server closed connection")
            r = json.loads(line)
            if r.get("id") == want_id:
                return r
        raise ConnectionError("id not found")

    def _rpc(self, method, params):
        with self.lock:
            self._id += 1
            rid = self._id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            r = self._read_matching(rid)
            if "error" in r and r["error"] is not None:
                raise RuntimeError(f"{method}: {r['error']}")
            return r.get("result")

    def pipeline_history(self, scripthashes):
        """Send all, then collect; returns list aligned with input (None on per-item error)."""
        with self.lock:
            base = self._id + 1
            for i, sh in enumerate(scripthashes):
                self._send({"jsonrpc": "2.0", "id": base + i,
                            "method": "blockchain.scripthash.get_history",
                            "params": [sh]})
            self._id = base + len(scripthashes) - 1
            results = {}
            remaining = set(range(len(scripthashes)))
            while remaining:
                line = self.f.readline()
                if not line:
                    raise ConnectionError("server closed during pipeline")
                r = json.loads(line)
                rid = r.get("id")
                if rid is None or rid < base:
                    continue
                i = rid - base
                if i not in remaining:
                    continue
                remaining.discard(i)
                if "error" in r and r["error"] is not None:
                    results[i] = None
                else:
                    results[i] = r.get("result")
            return [results.get(i) for i in range(len(scripthashes))]


def script_hex(pubkey_hex, path_type):
    h = hash160(bytes.fromhex(pubkey_hex)).hex()
    if path_type == 'bip44_legacy':
        return "76a914" + h + "88ac"
    if path_type == 'bip49_segwit':
        redeem = bytes.fromhex("0014" + h)
        return "a914" + hash160(redeem).hex() + "87"
    if path_type == 'bip84_native_segwit':
        return "0014" + h
    raise ValueError(path_type)


def scripthash(script_hex_str):
    return hashlib.sha256(bytes.fromhex(script_hex_str)).digest()[::-1].hex()


class Scanner:
    def __init__(self):
        self.conns = []
        self.conn_idx = 0
        self.conn_lock = threading.Lock()
        self.errors = 0
        self.lookups = 0

    def get_conn(self):
        with self.conn_lock:
            for _ in range(len(self.conns)):
                c = self.conns[self.conn_idx % len(self.conns)]
                self.conn_idx += 1
                if c.sock is not None:
                    return c
            tried = set((c.host, c.port) for c in self.conns)
            for h, p, t in SERVERS:
                if (h, p) in tried:
                    continue
                try:
                    c = ElectrumConn(h, p, t)
                    self.conns.append(c)
                    return c
                except Exception as e:
                    print(f"  [CONN-FAIL] {h}:{p} {e}", flush=True)
                    tried.add((h, p))
            if self.conns:
                c = self.conns[0]
                c.connect()
                return c
            raise RuntimeError("no electrum servers available")

    def check_batch(self, entries):
        """entries: list of dicts with 'pubkey','type'. Returns list of tx_count or None(error)."""
        shs = [scripthash(script_hex(e['pubkey'], e['type'])) for e in entries]
        last_err = None
        for _ in range(3):
            try:
                c = self.get_conn()
                res = c.pipeline_history(shs)
                self.lookups += len(shs)
                out = []
                for r in res:
                    out.append(None if r is None else len(r))
                if all(v == 0 for v in out) and self.lookups % 5000 < BATCH:
                    # sync heartbeat
                    pass
                return out
            except Exception as e:
                last_err = e
                self.errors += 1
                try:
                    c.close(); c.connect()
                except Exception:
                    pass
                time.sleep(1.5)
        raise RuntimeError(f"batch failed after retries: {last_err}")

    def tip_height(self):
        c = self.get_conn()
        return c._rpc("blockchain.headers.subscribe", [])["height"]


def scan_chain(scanner, path_type, account, change, gap_limit, start_idx=0):
    found = []
    consecutive_empty = 0
    consecutive_errors = 0
    idx = start_idx
    label = 'external' if change == 0 else 'change'
    while consecutive_empty < gap_limit:
        entries = derive_addresses(MNEMONIC, 'btc', path_type, idx, BATCH,
                                   change=change, account=account)
        counts = scanner.check_batch(entries)
        if counts is None:
            consecutive_errors += 1
            if consecutive_errors > 10:
                raise RuntimeError(f"chain {path_type}/acct{account}/{label}: too many errors")
            continue
        consecutive_errors = 0
        failed = [(e, i) for i, (e, n) in enumerate(zip(entries, counts)) if n is None]
        # retry per-item failures up to 3 rounds before aborting the chain
        for _round in range(3):
            if not failed:
                break
            retry_entries = [e for e, _ in failed]
            retry_counts = scanner.check_batch(retry_entries)
            new_failed = []
            for (e, _i), n in zip(failed, retry_counts):
                if n is None:
                    new_failed.append((e, _i))
                else:
                    counts[_i] = n
            failed = new_failed
        if failed:
            raise RuntimeError(
                f"per-item error at {path_type}/acct{account}/{label} idx "
                f"{failed[0][0]['index']} ({len(failed)} items) after retries")
        for e, n in zip(entries, counts):
            if n is None:
                raise RuntimeError(f"per-item error at {path_type}/acct{account}/{label} idx {e['index']}")
            if n > 0:
                e2 = dict(e)
                e2.update({'tx_count': n, 'account': account})
                found.append(e2)
                print(f"    [FOUND] {e['address']} ({path_type} acct{account} {label} idx {e['index']}) - {n} txs", flush=True)
                consecutive_empty = 0
            else:
                consecutive_empty += 1
        idx += BATCH
        if idx % 512 == 0:
            print(f"    [PROGRESS] {path_type}/acct{account}/{label} idx={idx} empty_streak={consecutive_empty} found={len(found)}", flush=True)
    return found, idx


def main():
    scanner = Scanner()
    tip = scanner.tip_height()
    print(f"[SCAN BTC] Electrum verification, server tip height {tip}", flush=True)
    report = {'coin': 'btc', 'source': 'electrum:' + ",".join(f"{h}:{p}" for h, p, _ in SERVERS),
              'server_tip_height': tip, 'generated': datetime.now().isoformat(),
              'types': {}, 'total_found_new': 0, 'total_lookups': 0}

    with open(os.path.join(OUT_DIR, PRIOR_FUNDED_FILE)) as fh:
        prior = json.load(fh)
    n_prior = len(prior)
    print(f"  [PRIOR-FUNDED] bip44_legacy acct0 external: {n_prior} addresses", flush=True)

    for path_type in TYPES:
        type_rec = {'status': None, 'accounts': {}, 'found': 0, 'lookups': 0}
        report['types'][path_type] = type_rec
        type_active = False

        for account in ACCOUNTS:
            for change in (0, 1):
                label = 'external' if change == 0 else 'change'

                if path_type == 'bip44_legacy' and account == 0 and change == 0:
                    type_rec['accounts'][f"{account}/{label}"] = {
                        'status': f'funded (prior verified scan, {n_prior} addresses imported)',
                        'found': n_prior, 'lookups': 0}
                    type_rec['found'] += n_prior
                    type_active = True
                    continue

                try:
                    if account == 0 and change == 0:
                        found, idx = scan_chain(scanner, path_type, account, change, DETECT_LIMIT)
                        if not found:
                            tail, idx2 = scan_chain(scanner, path_type, account, change,
                                                    GAP_TAIL, start_idx=idx)
                            found += tail
                            type_rec['accounts'][f"{account}/{label}"] = {
                                'status': f'NOT USED (first {DETECT_LIMIT} empty, '
                                          f'{DETECT_LIMIT + idx2 - idx} checked)',
                                'found': len(found), 'lookups': idx2}
                            type_rec['lookups'] += idx2
                            report['total_lookups'] += idx2
                            print(f"  [NOT-USED] {path_type}: first {DETECT_LIMIT} empty -> type not used", flush=True)
                            continue
                        type_active = True
                        rest, idx2 = scan_chain(scanner, path_type, account, change,
                                                GAP_ACTIVE, start_idx=idx)
                        found += rest
                        status = f'funded (gap-{GAP_ACTIVE} verified, scanned to idx {idx2})'
                        lookups = idx2
                    elif account == 0 and change == 1 and type_active:
                        found, idx2 = scan_chain(scanner, path_type, account, change, GAP_ACTIVE)
                        status = f'{"funded" if found else "empty"} (gap-{GAP_ACTIVE} verified, scanned to idx {idx2})'
                        lookups = idx2
                    else:
                        gap = GAP_DEEP if (account > 0 or not type_active) else GAP_ACTIVE
                        found, idx2 = scan_chain(scanner, path_type, account, change, gap)
                        status = f'{"funded" if found else "empty"} (gap-{gap} checked to idx {idx2})'
                        lookups = idx2
                except RuntimeError as e:
                    type_rec['accounts'][f"{account}/{label}"] = {
                        'status': f'ERROR: {e}', 'found': -1, 'lookups': 0}
                    print(f"  [ERROR] {path_type} acct{account} {label}: {e}", flush=True)
                    continue

                type_rec['accounts'][f"{account}/{label}"] = {
                    'status': status, 'found': len(found), 'lookups': lookups}
                type_rec['found'] += len(found)
                type_rec['lookups'] += lookups
                report['total_lookups'] += lookups
                report['total_found_new'] += len(found)
                if found:
                    print(f"  [FUNDED] {path_type} acct{account} {label}: {len(found)} addresses", flush=True)
                    with open(os.path.join(OUT_DIR, f'new_found_btc_{path_type}_acct{account}_{"ext" if change == 0 else "chg"}.json'), 'w') as fh:
                        json.dump(found, fh)

        type_rec['status'] = 'USED' if type_active or type_rec['found'] else 'NOT USED'
        print(f"[TYPE-VERDICT] btc {path_type}: {type_rec['status']}", flush=True)

    report['grand_total_found'] = report['total_found_new'] + (
        n_prior if True else 0)
    report['server_end_tip_height'] = scanner.tip_height()
    out_file = os.path.join(OUT_DIR, 'scan_all_types_btc.json')
    with open(out_file, 'w') as fh:
        json.dump(report, fh, indent=2)
    print(f"[SAVE] {out_file}", flush=True)
    print(f"[BTC SCAN] Complete. new_found={report['total_found_new']} "
          f"lookups={report['total_lookups']} errors={scanner.errors}", flush=True)


if __name__ == '__main__':
    main()
