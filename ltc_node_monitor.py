#!/usr/bin/env python3
"""
LTC Node Monitor + Auto-Scanner
Checks for LTC blockbook/trezor node on the network, then automatically
runs full LTC wallet analysis when a node is found.

Usage:
    python3 ltc_node_monitor.py          # one-shot check + scan if found
    python3 ltc_node_monitor.py --loop   # continuous monitoring
    python3 ltc_node_monitor.py --force  # scan even if no local node (uses public APIs)
"""

import json, urllib.request, ssl, socket, concurrent.futures, time, os, sys, argparse
from datetime import datetime

# Mnemonic and config
from wallet_config import MNEMONIC
GAP_LIMIT = 5000
MAX_SCAN = 6000

# Known hosts to check
KNOWN_HOSTS = [
    ("10.10.20.7", [9139, 9030, 9130, 8080, 3000, 9090]),
    ("10.10.20.10", [9139, 9030, 9130, 8080, 3000, 9090]),
    ("10.10.20.11", [9139, 9030, 9130, 8080, 3000, 9090]),
    ("10.10.30.7", [9139, 9030, 9130, 8080, 3000, 9090]),
    ("10.10.30.10", [9139, 9030, 9130, 8080, 3000, 9090]),
    ("10.10.30.11", [9139, 9030, 9130, 8080, 3000, 9090]),
]

# Also scan full subnet for new nodes
SUBNETS = ["10.10.20", "10.10.30"]

# Public fallbacks (last resort)
PUBLIC_LTC_APIS = [
    "https://ltc1.trezor.io",
    "https://ltcbook.nownodes.io",
    "https://mempool.space/litecoin",
]

RESULTS_DIR = os.path.dirname(os.path.abspath(__file__))


def check_blockbook(url, timeout=5):
    """Check if a blockbook node is alive and synced."""
    try:
        req = urllib.request.Request(
            f"{url}/api/v2",
            headers={'User-Agent': 'WalletAnalyzer/1.0'},
            method='GET'
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = json.loads(resp.read())
            return True, data
    except Exception as e:
        return False, str(e)


def check_host_port(ip, port, timeout=2):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return ip, port, True
    except:
        return ip, port, False


def scan_for_ltc_nodes():
    """Scan network for LTC blockbook nodes."""
    print(f"[{datetime.now().isoformat()}] Scanning for LTC nodes...")
    found = []
    
    # Check known hosts first — parallelized with short timeout
    print("  Checking known hosts...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {}
        for ip, ports in KNOWN_HOSTS:
            for port in ports:
                url = f"http://{ip}:{port}"
                fut = executor.submit(check_blockbook, url, timeout=2)
                futures[fut] = (ip, port, url)
        
        for fut in concurrent.futures.as_completed(futures, timeout=30):
            ip, port, url = futures[fut]
            try:
                ok, info = fut.result(timeout=3)
                if ok:
                    print(f"  FOUND LTC node: {url}")
                    found.append((ip, port, info))
            except Exception:
                pass
    
    if found:
        return found
    
    # Quick subnet scan for new nodes — only on most likely blockbook ports
    print("  Scanning subnets for new nodes...")
    likely_ports = [9139, 9030, 9130]
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = []
        for subnet in SUBNETS:
            for i in range(1, 50):
                ip = f"{subnet}.{i}"
                for port in likely_ports:
                    futures.append(executor.submit(check_host_port, ip, port, 1))
        
        open_ports = []
        for future in concurrent.futures.as_completed(futures, timeout=20):
            try:
                ip, port, is_open = future.result(timeout=2)
                if is_open:
                    open_ports.append((ip, port))
            except Exception:
                pass
        
        for ip, port in open_ports:
            ok, info = check_blockbook(f"http://{ip}:{port}", timeout=2)
            if ok:
                print(f"  FOUND new LTC node: http://{ip}:{port}")
                found.append((ip, port, info))
    
    # Check public fallbacks
    print("  Checking public fallbacks...")
    for base in PUBLIC_LTC_APIS:
        ok, info = check_blockbook(base, timeout=5)
        if ok:
            print(f"  FOUND public LTC node: {base}")
            found.append((base, 443, info))
    
    return found


def derive_ltc_addresses(mnemonic, path_type, start=0, count=100, change=0):
    """Derive LTC addresses from mnemonic. Reuses wallet_analyzer.py logic."""
    sys.path.insert(0, RESULTS_DIR)
    from wallet_analyzer import derive_addresses
    return derive_addresses(mnemonic, 'ltc', path_type, start, count, change)


def fetch_blockbook_address(url, address, timeout=10):
    """Fetch address info from blockbook v2 API."""
    try:
        req = urllib.request.Request(
            f"{url}/api/v2/address/{address}",
            headers={'User-Agent': 'WalletAnalyzer/1.0'}
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return None


def fetch_blockbook_address_txs(url, address, page=1, page_size=50, timeout=15):
    """Fetch address transactions from blockbook v2 API."""
    try:
        req = urllib.request.Request(
            f"{url}/api/v2/address/{address}?page={page}&pageSize={page_size}",
            headers={'User-Agent': 'WalletAnalyzer/1.0'}
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return None


def scan_ltc_addresses(node_url, mnemonic, gap_limit=5000):
    """Deep scan for LTC addresses with transactions."""
    from wallet_analyzer import derive_addresses
    
    print(f"\n[LTC SCAN] Starting deep address scan...")
    all_found = []
    
    for path_type in ['bip44_legacy', 'bip49_segwit', 'bip84_native_segwit']:
        for chain in [0, 1]:  # external and change
            chain_label = 'external' if chain == 0 else 'change'
            print(f"\n  Path: {path_type} ({chain_label})")
            found = []
            consecutive_empty = 0
            idx = 0
            
            while idx < MAX_SCAN and consecutive_empty < gap_limit:
                batch = derive_addresses(mnemonic, 'ltc', path_type, idx, 10, change=chain)
                for addr_info in batch:
                    address = addr_info['address']
                    info = fetch_blockbook_address(node_url, address, timeout=5)
                    if info and info.get('txs', 0) > 0:
                        print(f"    [FOUND] {address} ({path_type}, {chain_label}, idx {addr_info['index']}) - {info['txs']} txs")
                        addr_info['tx_count'] = info['txs']
                        addr_info['balance'] = info.get('balance', '0')
                        found.append(addr_info)
                        all_found.append(addr_info)
                        consecutive_empty = 0
                    else:
                        consecutive_empty += 1
                idx += 10
                time.sleep(0.1)
            
            print(f"  Found {len(found)} addresses on {path_type} {chain_label}")
    
    return all_found


def fetch_all_ltc_txs(node_url, addresses):
    """Fetch all transactions for discovered LTC addresses."""
    print(f"\n[LTC TX FETCH] Fetching transactions for {len(addresses)} addresses...")
    all_txs = {}
    
    for i, addr_info in enumerate(addresses):
        address = addr_info['address']
        data = fetch_blockbook_address_txs(node_url, address, page=1, page_size=1000)
        if data and 'transactions' in data:
            for tx in data['transactions']:
                txid = tx.get('txid')
                if txid and txid not in all_txs:
                    all_txs[txid] = tx
            print(f"  [{i+1}/{len(addresses)}] {address}: {len(data['transactions'])} txs")
        else:
            print(f"  [{i+1}/{len(addresses)}] {address}: no txs")
        time.sleep(0.05)
    
    print(f"[LTC TX FETCH] Found {len(all_txs)} unique transactions")
    return all_txs


def save_ltc_results(addresses, transactions):
    """Save LTC scan results."""
    addr_file = os.path.join(RESULTS_DIR, 'found_addresses_ltc.json')
    tx_file = os.path.join(RESULTS_DIR, 'all_ltc_transactions.json')
    
    with open(addr_file, 'w') as f:
        json.dump(addresses, f, indent=2)
    with open(tx_file, 'w') as f:
        json.dump(transactions, f, indent=2)
    
    print(f"[SAVE] LTC addresses -> {addr_file}")
    print(f"[SAVE] LTC transactions -> {tx_file}")


def classify_ltc_transactions(transactions, our_addresses):
    """Classify LTC transactions."""
    our_addr_set = set(a['address'] for a in our_addresses)
    
    incoming = []
    outgoing = []
    
    for txid, tx in transactions.items():
        vin = tx.get('vin', [])
        vout = tx.get('vout', [])
        
        our_inputs = set()
        for inp in vin:
            addr = inp.get('addresses', [None])[0] if isinstance(inp.get('addresses'), list) else inp.get('addr')
            if addr in our_addr_set:
                our_inputs.add(addr)
        
        our_outputs = set()
        external_outputs = []
        for out in vout:
            addr = out.get('addresses', [None])[0] if isinstance(out.get('addresses'), list) else out.get('addr')
            val = out.get('value', 0)
            if addr in our_addr_set:
                our_outputs.add(addr)
            elif addr:
                external_outputs.append({'address': addr, 'value': val})
        
        if our_inputs:
            total_external = sum(o['value'] for o in external_outputs)
            outgoing.append({
                'txid': txid,
                'block_time': tx.get('blockTime'),
                'external_outputs': external_outputs,
                'external_total': total_external,
                'fee': tx.get('fees', 0),
            })
        else:
            our_received = sum(o['value'] for o in vout 
                             if (o.get('addresses', [None])[0] if isinstance(o.get('addresses'), list) else o.get('addr')) in our_addr_set)
            incoming.append({
                'txid': txid,
                'block_time': tx.get('blockTime'),
                'amount_received': our_received,
            })
    
    return incoming, outgoing


def generate_combined_report(ltc_addresses, ltc_incoming, ltc_outgoing):
    """Generate combined BTC+LTC report."""
    report_file = os.path.join(RESULTS_DIR, 'combined_report.json')
    txt_file = os.path.join(RESULTS_DIR, 'combined_report.txt')
    
    total_in = sum(i['amount_received'] for i in ltc_incoming)
    total_out = sum(o['external_total'] for o in ltc_outgoing)
    total_fees = sum(o['fee'] for o in ltc_outgoing)
    
    ltc_report = {
        'total_addresses': len(ltc_addresses),
        'total_transactions': len(ltc_incoming) + len(ltc_outgoing),
        'incoming_count': len(ltc_incoming),
        'outgoing_count': len(ltc_outgoing),
        'total_incoming_ltc': total_in / 1e8,
        'total_outgoing_ltc': total_out / 1e8,
        'total_fees_ltc': total_fees / 1e8,
        'net_flow_ltc': (total_in - total_out - total_fees) / 1e8,
    }
    
    with open(report_file, 'w') as f:
        json.dump({'ltc': ltc_report}, f, indent=2)
    
    lines = []
    lines.append("=" * 70)
    lines.append("         COMBINED BTC + LTC WALLET ANALYSIS REPORT")
    lines.append("=" * 70)
    lines.append("")
    lines.append("LTC WALLET SUMMARY")
    lines.append("-" * 70)
    lines.append(f"Total LTC addresses:      {ltc_report['total_addresses']}")
    lines.append(f"Total LTC transactions:   {ltc_report['total_transactions']}")
    lines.append(f"LTC incoming:             {ltc_report['total_incoming_ltc']:.8f} LTC")
    lines.append(f"LTC outgoing:             {ltc_report['total_outgoing_ltc']:.8f} LTC")
    lines.append(f"LTC fees:                 {ltc_report['total_fees_ltc']:.8f} LTC")
    lines.append(f"LTC net flow:             {ltc_report['net_flow_ltc']:.8f} LTC")
    lines.append("")
    lines.append("=" * 70)
    lines.append(f"Generated: {datetime.now().isoformat()}")
    
    with open(txt_file, 'w') as f:
        f.write('\n'.join(lines))
    
    print('\n'.join(lines))
    print(f"\n[SAVE] Combined report -> {report_file}")
    print(f"[SAVE] Combined report -> {txt_file}")


def run_full_ltc_scan(node_url):
    """Run complete LTC analysis pipeline."""
    print(f"\n{'='*70}")
    print(f"  LTC NODE FOUND: {node_url}")
    print(f"  Starting full LTC wallet analysis...")
    print(f"{'='*70}")
    
    # Step 1: Discover addresses
    addresses = scan_ltc_addresses(node_url, MNEMONIC, GAP_LIMIT)
    
    if not addresses:
        print("[LTC SCAN] No LTC addresses with transactions found.")
        return
    
    # Step 2: Fetch transactions
    transactions = fetch_all_ltc_txs(node_url, addresses)
    
    # Step 3: Save raw data
    save_ltc_results(addresses, transactions)
    
    # Step 4: Classify
    incoming, outgoing = classify_ltc_transactions(transactions, addresses)
    
    # Step 5: Generate report
    generate_combined_report(addresses, incoming, outgoing)
    
    print(f"\n{'='*70}")
    print(f"  LTC ANALYSIS COMPLETE")
    print(f"{'='*70}")


def main():
    parser = argparse.ArgumentParser(description='LTC Node Monitor & Auto-Scanner')
    parser.add_argument('--loop', action='store_true', help='Continuous monitoring mode')
    parser.add_argument('--force', action='store_true', help='Force scan using public APIs even without local node')
    parser.add_argument('--interval', type=int, default=300, help='Check interval in seconds (default: 300 = 5 min)')
    args = parser.parse_args()
    
    if args.force:
        print("[FORCE] Attempting scan with public APIs...")
        for base in PUBLIC_LTC_APIS:
            ok, info = check_blockbook(base, timeout=10)
            if ok:
                run_full_ltc_scan(base)
                return
        print("[FORCE] No working public LTC API found.")
        return
    
    while True:
        nodes = scan_for_ltc_nodes()
        
        if nodes:
            # Use the first found node
            ip, port, info = nodes[0]
            if ip.startswith('http'):
                url = ip
            else:
                url = f"http://{ip}:{port}"
            
            run_full_ltc_scan(url)
            print(f"\n[{datetime.now().isoformat()}] LTC scan complete. Monitoring stopped.")
            break
        else:
            print(f"  No LTC node found.")
        
        if not args.loop:
            print(f"\n[Monitor] One-shot check complete. No LTC node detected.")
            print(f"  Run with --loop to continuously monitor.")
            break
        
        print(f"  Next check in {args.interval} seconds...")
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
