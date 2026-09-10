"""Execution: full LTC wallet analysis, run when the LTC blockbook node is online.

Steps:
  1. Re-probe the network to locate the working node URL.
  2. Deep-scan BIP44/49/84 x (external, change) for funded LTC addresses.
  3. Fetch every transaction touching those addresses via blockbook v2 API.
  4. Classify incoming vs outgoing (self vs vendor withdrawal).
  5. Save raw data + combined BTC+LTC report next to the BTC results.
  6. Return a summary artifact and push a notification.
"""

import json
import os
import sys
import time
import urllib.request
import ssl
import concurrent.futures
from datetime import datetime

MNEMONIC = "resemble praise oxygen rhythm rate rose mutual upon beach april behave cliff"
GAP_LIMIT = 5000
MAX_SCAN = 6000

KNOWN_HOSTS = [
    ("10.10.20.7", [9139, 9030, 9130, 8080, 3000, 9090]),
    ("10.10.20.10", [9139, 9030, 9130, 8080, 3000, 9090]),
    ("10.10.20.11", [9139, 9030, 9130, 8080, 3000, 9090]),
    ("10.10.30.7", [9139, 9030, 9130, 8080, 3000, 9090]),
    ("10.10.30.10", [9139, 9030, 9130, 8080, 3000, 9090]),
    ("10.10.30.11", [9139, 9030, 9130, 8080, 3000, 9090]),
]
SUBNETS = ["10.10.20", "10.10.30"]
BLOCKBOOK_PORTS = [9139, 9030, 9130]
PUBLIC_LTC_APIS = [
    "https://ltc1.trezor.io",
    "https://ltcbook.nownodes.io",
    "https://mempool.space/litecoin",
]

PROJECT_DIR = "/Users/agenticos/Documents/kimi/workspace/wallet-analizer"

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


# ---------------------------------------------------------------------------
# Node discovery
# ---------------------------------------------------------------------------

def _is_blockbook(url, timeout=3):
    try:
        req = urllib.request.Request(f"{url}/api/v2", method='GET',
                                     headers={'User-Agent': 'WalletAnalyzer/1.0'})
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as resp:
            data = json.loads(resp.read())
            return isinstance(data, dict) and ('blockbook' in data or 'backend' in data)
    except Exception:
        return False


def _port_open(ip, port, timeout=1):
    try:
        import socket
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def find_node():
    urls = [f"http://{ip}:{p}" for ip, ports in KNOWN_HOSTS for p in ports]
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        futs = {ex.submit(_is_blockbook, u, 2): u for u in urls}
        for f in concurrent.futures.as_completed(futs, timeout=15):
            try:
                if f.result(timeout=2):
                    return futs[f]
            except Exception:
                pass

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as ex:
        futs = {ex.submit(_port_open, f"{s}.{i}", p, 1): f"http://{s}.{i}:{p}"
                for s in SUBNETS for i in range(1, 50) for p in BLOCKBOOK_PORTS}
        candidates = []
        for f in concurrent.futures.as_completed(futs, timeout=10):
            try:
                if f.result(timeout=1):
                    candidates.append(futs[f])
            except Exception:
                pass
    for u in candidates[:8]:
        if _is_blockbook(u, timeout=2):
            return u

    for base in PUBLIC_LTC_APIS:
        if _is_blockbook(base, timeout=5):
            return base
    return None


# ---------------------------------------------------------------------------
# Blockbook v2 API
# ---------------------------------------------------------------------------

def _get(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'WalletAnalyzer/1.0'})
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def fetch_address_info(node, address, timeout=8):
    return _get(f"{node}/api/v2/address/{address}", timeout)


def fetch_address_txs(node, address, page=1, page_size=1000, timeout=20):
    return _get(f"{node}/api/v2/address/{address}?page={page}&pageSize={page_size}", timeout)


def _vin_addr(inp):
    a = inp.get('addresses')
    if isinstance(a, list) and a:
        return a[0]
    return inp.get('addr')


def _vout_addr(out):
    a = out.get('addresses')
    if isinstance(a, list) and a:
        return a[0]
    return out.get('addr')


# ---------------------------------------------------------------------------
# Analysis pipeline
# ---------------------------------------------------------------------------

def scan_addresses(node):
    sys.path.insert(0, PROJECT_DIR)
    from wallet_analyzer import derive_addresses

    print("[scan] deep address discovery started")
    found = []
    for path_type in ['bip44_legacy', 'bip49_segwit', 'bip84_native_segwit']:
        for chain in (0, 1):
            label = 'external' if chain == 0 else 'change'
            consecutive_empty = 0
            idx = 0
            while idx < MAX_SCAN and consecutive_empty < GAP_LIMIT:
                batch = derive_addresses(MNEMONIC, 'ltc', path_type, idx, 10, change=chain)
                for info in batch:
                    data = fetch_address_info(node, info['address'], timeout=6)
                    if data and data.get('txs', 0) > 0:
                        info['tx_count'] = data['txs']
                        info['balance'] = data.get('balance', '0')
                        found.append(info)
                        consecutive_empty = 0
                    else:
                        consecutive_empty += 1
                idx += 10
                if idx % 500 == 0:
                    print(f"[scan] {path_type}/{label}: idx={idx}, found={len(found)}")
    print(f"[scan] funded addresses found: {len(found)}")
    return found


def fetch_transactions(node, addresses):
    print(f"[tx] fetching transactions for {len(addresses)} addresses")
    txs = {}
    for i, info in enumerate(addresses):
        data = fetch_address_txs(node, info['address'])
        if data and 'transactions' in data:
            for tx in data['transactions']:
                txid = tx.get('txid')
                if txid and txid not in txs:
                    txs[txid] = tx
        if (i + 1) % 10 == 0:
            print(f"[tx] {i + 1}/{len(addresses)} done, {len(txs)} unique txs")
        time.sleep(0.05)
    print(f"[tx] total unique transactions: {len(txs)}")
    return txs


def classify(txs, addresses):
    ours = {a['address'] for a in addresses}
    incoming, outgoing_self, outgoing_vendor = [], [], []

    for txid, tx in txs.items():
        vin, vout = tx.get('vin', []), tx.get('vout', [])
        our_inputs = {_vin_addr(i) for i in vin if _vin_addr(i) in ours}

        if our_inputs:
            external, self_outputs = [], []
            for o in vout:
                addr, val = _vout_addr(o), o.get('value', 0)
                if addr in ours:
                    self_outputs.append({'address': addr, 'value': val})
                elif addr:
                    external.append({'address': addr, 'value': val})
            rec = {
                'txid': txid,
                'block_time': tx.get('blockTime'),
                'fee': tx.get('fees', 0),
            }
            if external:
                rec['external_outputs'] = external
                rec['external_total'] = sum(e['value'] for e in external)
                outgoing_vendor.append(rec)
            else:
                rec['self_outputs'] = self_outputs
                rec['self_total'] = sum(s['value'] for s in self_outputs)
                outgoing_self.append(rec)
        else:
            received = sum(o.get('value', 0) for o in vout if _vout_addr(o) in ours)
            incoming.append({'txid': txid, 'block_time': tx.get('blockTime'),
                             'amount_received': received})

    return incoming, outgoing_self, outgoing_vendor


def save_results(addresses, txs, incoming, out_self, out_vendor, node):
    run_dir = os.environ.get('DAIMON_BLUEPRINT_AUTOMATION_RUN_DIRECTORY', os.getcwd())
    targets = []
    if os.path.isdir(PROJECT_DIR):
        targets.append(PROJECT_DIR)
    targets.append(run_dir)

    report_paths = []
    payload = {
        'generated_at': datetime.now().isoformat(),
        'node_url': node,
        'mnemonic_fingerprint': 'resemble...cliff',
        'addresses': addresses,
        'transactions': txs,
        'incoming': incoming,
        'outgoing_self_withdrawal': out_self,
        'outgoing_vendor_withdrawal': out_vendor,
    }
    summary = {
        'generated_at': datetime.now().isoformat(),
        'node_url': node,
        'address_count': len(addresses),
        'transaction_count': len(txs),
        'incoming_count': len(incoming),
        'outgoing_self_count': len(out_self),
        'outgoing_vendor_count': len(out_vendor),
        'total_incoming_ltc': sum(i['amount_received'] for i in incoming) / 1e8,
        'vendor_total_ltc': sum(o['external_total'] for o in out_vendor) / 1e8,
        'self_total_ltc': sum(o['self_total'] for o in out_self) / 1e8,
        'vendor_fees_ltc': sum(o['fee'] for o in out_vendor) / 1e8,
    }
    summary['net_flow_ltc'] = (summary['total_incoming_ltc']
                               - summary['vendor_total_ltc']
                               - summary['self_total_ltc']
                               - summary['vendor_fees_ltc'])

    for target in targets:
        try:
            addr_f = os.path.join(target, 'found_addresses_ltc.json')
            tx_f = os.path.join(target, 'all_ltc_transactions.json')
            rep_f = os.path.join(target, 'ltc_report.json')
            for path, obj in ((addr_f, addresses), (tx_f, txs),
                              (rep_f, payload), ):
                with open(path, 'w') as fh:
                    json.dump(obj, fh, indent=2)
            report_paths.extend([addr_f, tx_f, rep_f])
        except Exception as e:
            print(f"[save] failed for {target}: {e}")

    print(f"[save] results written: {report_paths}")
    return summary, report_paths


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(ctx):
    started = datetime.now()
    print(f"[run] started at {started.isoformat()}")

    node = find_node()
    if not node:
        return {"artifact": {
            "summary": "Condition fired but no LTC node was reachable at execution time.",
            "node_url": "",
            "completed_at": datetime.now().isoformat(),
        }}

    print(f"[run] node found: {node}")
    addresses = scan_addresses(node)
    if not addresses:
        return {"artifact": {
            "summary": f"LTC node {node} is online, but no funded LTC addresses were found.",
            "node_url": node,
            "addresses_found": 0,
            "report_paths": [],
            "completed_at": datetime.now().isoformat(),
        }}

    txs = fetch_transactions(node, addresses)
    incoming, out_self, out_vendor = classify(txs, addresses)
    summary, report_paths = save_results(addresses, txs, incoming, out_self, out_vendor, node)

    summary_text = (
        f"LTC scan complete via {node}: {len(addresses)} addresses, "
        f"{len(txs)} transactions — in {summary['total_incoming_ltc']:.8f} LTC, "
        f"vendor out {summary['vendor_total_ltc']:.8f} LTC, "
        f"self out {summary['self_total_ltc']:.8f} LTC, "
        f"net {summary['net_flow_ltc']:.8f} LTC"
    )
    print(f"[run] {summary_text}")

    return {"artifact": {
        "summary": summary_text,
        "node_url": node,
        "addresses_found": len(addresses),
        "transactions_found": len(txs),
        "incoming_count": len(incoming),
        "outgoing_count": len(out_self) + len(out_vendor),
        "total_incoming_ltc": summary['total_incoming_ltc'],
        "total_outgoing_ltc": summary['vendor_total_ltc'] + summary['self_total_ltc'],
        "net_flow_ltc": summary['net_flow_ltc'],
        "report_paths": report_paths,
        "completed_at": datetime.now().isoformat(),
    }}
