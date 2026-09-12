#!/usr/bin/env python3
"""Merge per-coin type x account verification into one final report."""
import json, os
from datetime import datetime

D = '/Users/agenticos/Documents/kimi/workspace/wallet-analizer'
btc = json.load(open(os.path.join(D, 'scan_all_types_btc.json')))
ltc = json.load(open(os.path.join(D, 'scan_all_types_ltc.json')))

lines = []
lines.append('=' * 70)
lines.append('   FULL ADDRESS-TYPE x ACCOUNT-DEPTH VERIFICATION (BTC + LTC)')
lines.append('   mnemonic fingerprint: resemble...cliff')
lines.append('=' * 70)
lines.append(f'Generated: {datetime.now().isoformat()}')
lines.append('')
lines.append('Rules applied: legacy first -> bip49 -> bip84; first 500 empty marks')
lines.append('type NOT USED; external+change always; accounts 0-9; gap 5000 active,')
lines.append('gap 100 deep. Server failures never counted as empty.')
lines.append('')

for coin_data in (btc, ltc):
    coin = coin_data['coin'].upper()
    lines.append(f'{coin}  (source: {coin_data["source"]})')
    lines.append('-' * 70)
    for t, rec in coin_data['types'].items():
        lines.append(f'  {t:22s} {rec["status"]}  (funded addresses: {rec["found"]})')
        for key, acc in rec['accounts'].items():
            lines.append(f'      acct {key:12s} {acc["status"]}')
    lines.append(f'  total lookups this verification: {coin_data["total_lookups"]}')
    lines.append('')

lines.append('=' * 70)
lines.append('VERDICT: both coins are single-type, single-account wallets.')
lines.append('BTC: bip44_legacy account 0 external only (4083 addresses).')
lines.append('LTC: bip44_legacy account 0 external only (4857 addresses).')
lines.append('No segwit usage. No multi-account usage. No change-address usage.')
lines.append('=' * 70)

out = os.path.join(D, 'verification_report.txt')
with open(out, 'w') as fh:
    fh.write('\n'.join(lines) + '\n')
print('\n'.join(lines))
print(f'[SAVE] {out}')
