#!/usr/bin/env python3
"""
Exhaustive address-type x account-depth verification scanner.

Rules (per owner spec):
  1. Legacy (old default) address style first per coin, then bip49, bip84.
  2. If the FIRST 500 addresses of an address type (account 0, external)
     have zero transactions -> mark that type "not used" for the coin.
  3. Always check BOTH external (0) and internal/change (1) chains.
  4. Check at least 10 account depth (accounts 0..9).
  5. Abandon a chain after N consecutive unfunded addresses and move on:
       - account 0, active type            : gap 5000 (owner's earlier mandate)
       - account 0, first-500-empty detect : stop at 500 (+100 tail)
       - accounts 1..9 / inactive types    : gap 100 (owner range 100-500)

Reuses prior verified results so funded chains are never re-downloaded:
  - BTC bip44_legacy acct0 external: imported from found_addresses_btc_bip44_external.json
  - LTC bip44_legacy acct0 external: imported from found_addresses_ltc.json
  - LTC acct0 change + bip49 + bip84 (both chains): verified empty
    by the previous gap-5000 scan (ltc_scan_litecoinspace.py run log)

Usage: python3 scan_all_types_accounts.py <btc|ltc>
Output: scan_all_types_<coin>.json  (+ progress to stdout)
"""

import json, os, sys, time, socket, urllib.request, concurrent.futures
from datetime import datetime

socket.setdefaulttimeout(15)

sys.path.insert(0, '/Users/agenticos/Library/Python/3.9/lib/python/site-packages')
sys.path.insert(0, '/Users/agenticos/Documents/kimi/workspace/wallet-analizer')

from wallet_analyzer import derive_addresses

MNEMONIC = "resemble praise oxygen rhythm rate rose mutual upon beach april behave cliff"
OUT_DIR = "/Users/agenticos/Documents/kimi/workspace/wallet-analizer"
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}

TYPES = ['bip44_legacy', 'bip49_segwit', 'bip84_native_segwit']
ACCOUNTS = range(10)            # 0..9
GAP_ACTIVE = 5000               # account 0 of a type with usage (owner mandate)
DETECT_LIMIT = 500              # first-500-empty -> "not used"
GAP_TAIL = 100                  # extra tail after detection window
GAP_DEEP = 100                  # accounts 1..9 and inactive-type chains
BATCH = 10
WORKERS = 10

CONFIG = {
    'btc': {
        # mempool.space unreachable from this network (2026-09-13); local
        # 10.10.20.3:3006 node also down -> blockstream.info Esplora API
        'base': 'https://blockstream.info/api',
        'coin_arg': 'btc',
        'prior_file': 'found_addresses_btc_bip44_external.json',
        'prior_funded': {('bip44_legacy', 0, 0): 'found'},
        'prior_empty': set(),  # BTC change/segwit never verified before
    },
    'ltc': {
        'base': 'https://litecoinspace.org/api',
        'coin_arg': 'ltc',
        'prior_file': 'found_addresses_ltc.json',
        'prior_funded': {('bip44_legacy', 0, 0): 'found'},
        # verified empty at gap 5000 by ltc_scan_litecoinspace.py (2026-09-10):
        'prior_empty': {('bip44_legacy', 0, 1), ('bip49_segwit', 0, 0),
                        ('bip49_segwit', 0, 1), ('bip84_native_segwit', 0, 0),
                        ('bip84_native_segwit', 0, 1)},
    },
}


def make_req(base):
    def req(path, timeout=12, retries=4):
        delay = 1.5
        for attempt in range(retries):
            try:
                r = urllib.request.Request(f"{base}{path}", headers=HEADERS)
                with urllib.request.urlopen(r, timeout=timeout) as resp:
                    return json.loads(resp.read())
            except Exception:
                if attempt == retries - 1:
                    return None
                time.sleep(delay)
                delay *= 2
        return None
    return req


def check_address(req, address):
    data = req(f"/address/{address}", timeout=8)
    if data and data.get('chain_stats', {}).get('tx_count', 0) > 0:
        return {
            'address': address,
            'tx_count': data['chain_stats']['tx_count'],
            'balance': data['chain_stats']['funded_txo_sum'] - data['chain_stats']['spent_txo_sum'],
        }
    return None


def scan_chain(req, mnemonic, coin_arg, path_type, account, change,
               gap_limit, start_idx=0):
    """Scan one chain until gap_limit consecutive unfunded. Returns (found, last_idx)."""
    found = []
    consecutive_empty = 0
    idx = start_idx
    label = 'external' if change == 0 else 'change'
    while consecutive_empty < gap_limit:
        batch = derive_addresses(mnemonic, coin_arg, path_type, idx, BATCH,
                                 change=change, account=account)
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            results = list(ex.map(lambda b: check_address(req, b['address']), batch))
        for b, r in zip(batch, results):
            if r:
                info = dict(b)
                info.update({'tx_count': r['tx_count'], 'balance': r['balance'],
                             'account': account})
                found.append(info)
                print(f"    [FOUND] {b['address']} ({path_type} acct{account} {label} idx {b['index']}) - {r['tx_count']} txs", flush=True)
                consecutive_empty = 0
            else:
                consecutive_empty += 1
        idx += BATCH
        if idx % 500 == 0:
            print(f"    [PROGRESS] {path_type}/acct{account}/{label} idx={idx} empty_streak={consecutive_empty} found={len(found)}", flush=True)
    return found, idx


def main():
    coin = sys.argv[1]
    cfg = CONFIG[coin]
    base = cfg['base']
    req = make_req(base)
    out_file = os.path.join(OUT_DIR, f'scan_all_types_{coin}.json')
    report = {'coin': coin, 'source': base, 'generated': datetime.now().isoformat(),
              'types': {}, 'total_found_new': 0, 'total_lookups': 0}

    print(f"[SCAN {coin.upper()}] type x account-depth verification via {base}", flush=True)

    for path_type in TYPES:
        type_rec = {'status': None, 'accounts': {}, 'found': 0, 'lookups': 0}
        report['types'][path_type] = type_rec
        type_active = False

        for account in ACCOUNTS:
            for change in (0, 1):
                label = 'external' if change == 0 else 'change'
                key = (path_type, account, change)

                # --- prior verified results: never re-scan ---
                if key in cfg['prior_funded']:
                    with open(os.path.join(OUT_DIR, cfg['prior_file'])) as fh:
                        prior = json.load(fh)
                    n = len(prior)
                    type_rec['accounts'][f"{account}/{label}"] = {
                        'status': f'funded (prior verified scan, {n} addresses imported)',
                        'found': n, 'lookups': 0}
                    type_rec['found'] += n
                    type_active = True
                    print(f"  [PRIOR-FUNDED] {path_type} acct{account} {label}: {n} addresses", flush=True)
                    continue
                if key in cfg['prior_empty']:
                    type_rec['accounts'][f"{account}/{label}"] = {
                        'status': 'empty (prior verified gap-5000 scan)',
                        'found': 0, 'lookups': 0}
                    print(f"  [PRIOR-EMPTY] {path_type} acct{account} {label}", flush=True)
                    continue

                # --- new scans ---
                if account == 0 and change == 0:
                    # detection chain: first DETECT_LIMIT decide "used or not"
                    found, idx = scan_chain(req, MNEMONIC, cfg['coin_arg'],
                                            path_type, account, change,
                                            DETECT_LIMIT)
                    if not found:
                        # not used -> short tail then mark
                        tail, idx2 = scan_chain(req, MNEMONIC, cfg['coin_arg'],
                                                path_type, account, change,
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
                    else:
                        type_active = True
                        # active: continue to full GAP_ACTIVE
                        rest, idx2 = scan_chain(req, MNEMONIC, cfg['coin_arg'],
                                                path_type, account, change,
                                                GAP_ACTIVE, start_idx=idx)
                        found += rest
                        status = f'funded (gap-{GAP_ACTIVE} verified, scanned to idx {idx2})'
                        lookups = idx2
                elif account == 0 and change == 1 and type_active:
                    found, idx2 = scan_chain(req, MNEMONIC, cfg['coin_arg'],
                                             path_type, account, change, GAP_ACTIVE)
                    status = f'{"funded" if found else "empty"} (gap-{GAP_ACTIVE} verified, scanned to idx {idx2})'
                    lookups = idx2
                else:
                    gap = GAP_DEEP if (account > 0 or not type_active) else GAP_ACTIVE
                    found, idx2 = scan_chain(req, MNEMONIC, cfg['coin_arg'],
                                             path_type, account, change, gap)
                    status = f'{"funded" if found else "empty"} (gap-{gap} checked to idx {idx2})'
                    lookups = idx2

                type_rec['accounts'][f"{account}/{label}"] = {
                    'status': status, 'found': len(found), 'lookups': lookups}
                type_rec['found'] += len(found)
                type_rec['lookups'] += lookups
                report['total_lookups'] += lookups
                report['total_found_new'] += len(found)
                if found:
                    print(f"  [FUNDED] {path_type} acct{account} {label}: {len(found)} addresses", flush=True)
                    with open(os.path.join(OUT_DIR, f'new_found_{coin}_{path_type}_acct{account}_{"ext" if change == 0 else "chg"}.json'), 'w') as fh:
                        json.dump(found, fh)

        type_rec['status'] = 'USED' if type_active or type_rec['found'] else 'NOT USED'
        print(f"[TYPE-VERDICT] {coin} {path_type}: {type_rec['status']}", flush=True)

    report['grand_total_found'] = (report['total_found_new'] +
                                   sum(t['found'] for t in report['types'].values()))
    with open(out_file, 'w') as fh:
        json.dump(report, fh, indent=2)
    print(f"[SAVE] {out_file}", flush=True)
    print(f"[{coin.upper()} SCAN] Complete. new_found={report['total_found_new']} lookups={report['total_lookups']}", flush=True)


if __name__ == '__main__':
    main()
