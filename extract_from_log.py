#!/usr/bin/env python3
from wallet_config import PROJ
"""Extract addresses from deep scan log and save to JSON."""

import re, json, sys

log_path = '/Users/agenticos/Library/Application Support/kimi-desktop/daimon-share/daimon/runtime/kimi-code/home/sessions/wd_workspace_b34f1e299d9e/conv-ea4d05babe8ea46a39f925a9/agents/main/tasks/bash-8r5v990u/output.log'

found = []
with open(log_path, 'r') as f:
    for line in f:
        m = re.search(r'\[FOUND\]\s+([13][a-zA-Z0-9]{26,34})\s+\(idx\s+(\d+)\)\s+—\s+(\d+)\s+tx\(s\)', line)
        if m:
            found.append({
                'address': m.group(1),
                'index': int(m.group(2)),
                'n_tx': int(m.group(3)),
                'path': f"m/44'/0'/0'/0/{m.group(2)}",
                'type': 'bip44',
                'coin': 'btc',
                'chain': 'external'
            })

print(f"Extracted {len(found)} addresses")
if found:
    print(f"Index range: {min(a['index'] for a in found)} - {max(a['index'] for a in found)}")
    # Distribution of tx counts
    from collections import Counter
    tx_counts = Counter(a['n_tx'] for a in found)
    print(f"Tx count distribution: {dict(sorted(tx_counts.items()))}")

out_path = f'{PROJ}/found_addresses_btc_bip44_external.json'
with open(out_path, 'w') as f:
    json.dump(found, f, indent=2)
print(f"Saved to {out_path}")
