#!/usr/bin/env python3
"""Multi-wallet orchestrator.

Runs the full pipeline for every wallet in wallets.json (secrets referenced
by env var NAME only — never inline):

  derive (wallet_derive.py)  -> results/<name>/found_addresses_<coin>.json
  fetch  (fetch_all_txs_fast.py for BTC; LTC: supply the dump or see README)
  repair (fetch_missing_funding.py both)
  ledger (build_ledger.py)   -> results/<name>/wallet_report.html + reports
  index  (this script)       -> results/index.html across all wallets

Each stage runs with WALLET_DIR=results/<name> so all data files are scoped
per wallet. Stages are skipped when their outputs already exist (use
--force to redo). Run from the project root.

Usage:
  python3 run_wallets.py                # all wallets in wallets.json
  python3 run_wallets.py --wallet main  # one wallet
  python3 run_wallets.py --force        # redo every stage
"""

import argparse
import datetime
import html
import json
import os
import subprocess
import sys

from wallet_config import PROJ

RESULTS = os.path.join(PROJ, "results")


def run(cmd, env_extra, stage, name):
    env = dict(os.environ)
    env.update(env_extra)
    print(f"\n=== [{name}] {stage}: {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, cwd=PROJ, env=env)
    if r.returncode != 0:
        raise SystemExit(f"[{name}] stage '{stage}' failed with exit {r.returncode}")


def process_wallet(cfg, force):
    name = cfg["name"]
    wdir = os.path.join(RESULTS, name)
    os.makedirs(wdir, exist_ok=True)
    env = {"WALLET_DIR": wdir}

    has_btc_addrs = os.path.exists(os.path.join(wdir, "found_addresses_btc.json")) or \
        os.path.exists(os.path.join(wdir, "found_addresses_btc_bip44_external.json"))
    has_ltc_addrs = os.path.exists(os.path.join(wdir, "found_addresses_ltc.json"))

    # 1. derive
    if force or not (has_btc_addrs or has_ltc_addrs):
        run([sys.executable, "wallet_derive.py", "--wallet", name], env, "derive", name)
    else:
        print(f"[{name}] derive: found_addresses already present, skipping")

    # normalize: build_ledger expects the legacy filename for BTC
    legacy = os.path.join(wdir, "found_addresses_btc_bip44_external.json")
    modern = os.path.join(wdir, "found_addresses_btc.json")
    if os.path.exists(modern) and not os.path.exists(legacy):
        os.link(modern, legacy)

    # 2. fetch BTC txs
    btc_dump = os.path.join(wdir, "all_transactions.json")
    if has_btc_addrs and (force or not os.path.exists(btc_dump)):
        run([sys.executable, "fetch_all_txs_fast.py"], env, "fetch-btc", name)
    else:
        print(f"[{name}] fetch-btc: dump present or no BTC addresses, skipping")

    # 3. repair missing funding txs (needs dumps; harmless if absent)
    if os.path.exists(btc_dump) or os.path.exists(os.path.join(wdir, "all_ltc_transactions.json")):
        run([sys.executable, "fetch_missing_funding.py", "both"], env, "repair", name)
    else:
        print(f"[{name}] repair: no tx dumps yet, skipping")

    # 4. ledger + html
    if os.path.exists(btc_dump) or os.path.exists(os.path.join(wdir, "all_ltc_transactions.json")):
        run([sys.executable, "build_ledger.py"], env, "ledger", name)
    else:
        print(f"[{name}] ledger: no tx dumps yet, skipping")


def build_index():
    rows = []
    for d in sorted(os.listdir(RESULTS)):
        summary = os.path.join(RESULTS, d, "wallet_summary.json")
        report = os.path.join(RESULTS, d, "wallet_report.html")
        if not os.path.isfile(summary):
            continue
        s = json.load(open(summary))
        coins = [c for c in ("btc", "ltc") if c in s]
        for c in coins:
            rows.append((d, c.upper(), s[c]["addresses"], s[c]["in_usd"],
                         s[c]["out_usd"], s[c]["to_dest_usd"], s[c]["fees_usd"],
                         os.path.isfile(report)))
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    body = "".join(
        f"<tr><td>{html.escape(w)}</td><td>{c}</td><td>{a:,}</td>"
        f"<td>${i:,.2f}</td><td>${o:,.2f}</td><td>${t:,.2f}</td><td>${f:,.2f}</td>"
        f"<td>{'<a href=\"' + w + '/wallet_report.html\">open</a>' if has_report else '—'}</td></tr>"
        for w, c, a, i, o, t, f, has_report in rows)
    page = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>wallet-analizer results</title>
<style>
body{{background:#0d1117;color:#e6edf3;font:14px/1.5 -apple-system,sans-serif;padding:32px;max-width:1100px;margin:auto}}
h1{{font-size:20px}} p{{color:#8b949e;font-size:13px}}
table{{border-collapse:collapse;width:100%;margin-top:18px;background:#161b22;border:1px solid #30363d;border-radius:8px}}
th,td{{padding:8px 12px;text-align:right;border-bottom:1px solid #30363d}}
th:first-child,td:first-child{{text-align:left}}
th{{background:#1c2128;color:#8b949e;font-size:12px}}
a{{color:#58a6ff}}
</style></head><body>
<h1>Wallet Analysis Results</h1>
<p>{len(set(w for w, *_ in rows))} wallet(s) · generated {now} · USD at tx-day rates</p>
<table><tr><th>Wallet</th><th>Coin</th><th>Addresses</th><th>Received USD</th><th>Spent USD</th><th>Net to destinations USD</th><th>Fees USD</th><th>Report</th></tr>
{body}</table></body></html>"""
    with open(os.path.join(RESULTS, "index.html"), "w") as f:
        f.write(page)
    print(f"\nresults/index.html written ({len(rows)} wallet-coin rows)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wallet", default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    cfg_path = os.path.join(PROJ, "wallets.json")
    if not os.path.exists(cfg_path):
        print("wallets.json not found — copy wallets.example.json to wallets.json and fill it in")
        return 1
    for cfg in json.load(open(cfg_path))["wallets"]:
        if args.wallet and cfg["name"] != args.wallet:
            continue
        process_wallet(cfg, args.force)
    build_index()
    return 0


if __name__ == "__main__":
    sys.exit(main())
