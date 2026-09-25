#!/usr/bin/env python3
from wallet_config import PROJ
"""
Transaction classifier for wallet analysis.
Classifies each transaction as:
  - incoming: funds received to our wallet
  - self_withdrawal: funds moved between our own addresses
  - vendor_withdrawal: funds sent to external addresses
"""

import json
from datetime import datetime
from collections import defaultdict

ADDRESSES_FILE = f'{PROJ}/found_addresses_btc_bip44_external.json'
TXS_FILE = f'{PROJ}/all_transactions.json'
REPORT_FILE = f'{PROJ}/classified_report.json'

# Threshold: outputs below this value to our own addresses may be change
# But user said change chain is empty, so all our-address outputs are on external chain
# We'll still use this for heuristic analysis
DUST_THRESHOLD = 1000  # sats

def load_data():
    with open(ADDRESSES_FILE, 'r') as f:
        addresses = json.load(f)
    with open(TXS_FILE, 'r') as f:
        txs = json.load(f)
    return addresses, txs

def build_address_set(addresses):
    """Build a set of all our addresses for O(1) lookup."""
    return set(a['address'] for a in addresses)

def get_block_time(tx):
    """Get block timestamp or mempool timestamp."""
    status = tx.get('status', {})
    return status.get('block_time', status.get('confirmed', 0))

def classify_transaction(tx, our_addrs):
    """
    Classify a single transaction.
    Returns: (classification, details_dict)
    """
    txid = tx['txid']
    vin = tx.get('vin', [])
    vout = tx.get('vout', [])
    
    # Collect input addresses
    input_addrs = set()
    total_input_value = 0
    our_input_value = 0
    for inp in vin:
        prevout = inp.get('prevout', {})
        addr = prevout.get('scriptpubkey_address')
        value = prevout.get('value', 0)
        if addr:
            input_addrs.add(addr)
            total_input_value += value
            if addr in our_addrs:
                our_input_value += value
    
    # Collect output addresses
    output_addrs = set()
    total_output_value = 0
    our_output_value = 0
    external_output_value = 0
    outputs_to_us = []
    outputs_to_external = []
    
    for out in vout:
        addr = out.get('scriptpubkey_address')
        value = out.get('value', 0)
        if addr:
            output_addrs.add(addr)
            total_output_value += value
            if addr in our_addrs:
                our_output_value += value
                outputs_to_us.append({'address': addr, 'value': value})
            else:
                external_output_value += value
                outputs_to_external.append({'address': addr, 'value': value})
    
    # Determine if we are sender (our address in inputs)
    we_are_sender = bool(input_addrs & our_addrs)
    # Determine if we are receiver (our address in outputs)
    we_are_receiver = bool(output_addrs & our_addrs)
    
    # Classification
    if not we_are_sender and we_are_receiver:
        # Pure incoming - funds arrived to us
        classification = 'incoming'
    elif we_are_sender:
        # Outgoing transaction
        if external_output_value > 0:
            classification = 'vendor_withdrawal'
        else:
            # All outputs go to our addresses
            classification = 'self_withdrawal'
    else:
        # Should not happen if data is correct, but handle edge case
        classification = 'unknown'
    
    details = {
        'txid': txid,
        'classification': classification,
        'block_time': get_block_time(tx),
        'fee': tx.get('fee', 0),
        'our_input_value': our_input_value,
        'our_output_value': our_output_value,
        'external_output_value': external_output_value,
        'total_output_value': total_output_value,
        'inputs_from_us_count': len([a for a in input_addrs if a in our_addrs]),
        'outputs_to_us_count': len(outputs_to_us),
        'outputs_to_external_count': len(outputs_to_external),
        'external_destinations': outputs_to_external,
        'our_destinations': outputs_to_us,
        'all_input_addresses': list(input_addrs),
        'all_output_addresses': list(output_addrs),
    }
    
    return classification, details

def main():
    print("Loading data...")
    addresses, txs = load_data()
    our_addrs = build_address_set(addresses)
    
    print(f"Loaded {len(addresses)} addresses")
    print(f"Loaded {len(txs)} transactions")
    
    # Classify all transactions
    classified = {
        'incoming': [],
        'self_withdrawal': [],
        'vendor_withdrawal': [],
        'unknown': []
    }
    
    # For fund flow tracking
    vendor_destinations = defaultdict(lambda: {'count': 0, 'total_sats': 0, 'txids': []})
    
    total_incoming = 0
    total_self_moved = 0
    total_vendor_sent = 0
    total_fees_paid = 0
    
    for txid, tx in txs.items():
        classification, details = classify_transaction(tx, our_addrs)
        classified[classification].append(details)
        
        if classification == 'incoming':
            total_incoming += details['our_output_value']
        elif classification == 'self_withdrawal':
            total_self_moved += details['our_output_value']
            total_fees_paid += details['fee']
        elif classification == 'vendor_withdrawal':
            total_vendor_sent += details['external_output_value']
            total_fees_paid += details['fee']
            # Track destinations
            for dest in details['external_destinations']:
                addr = dest['address']
                vendor_destinations[addr]['count'] += 1
                vendor_destinations[addr]['total_sats'] += dest['value']
                if len(vendor_destinations[addr]['txids']) < 5:
                    vendor_destinations[addr]['txids'].append(txid)
    
    # Sort by block time
    for key in classified:
        classified[key].sort(key=lambda x: x['block_time'] or 0)
    
    # Build top destinations
    top_destinations = sorted(
        [(addr, info) for addr, info in vendor_destinations.items()],
        key=lambda x: x[1]['total_sats'],
        reverse=True
    )
    
    report = {
        'generated_at': datetime.now().isoformat(),
        'summary': {
            'total_addresses': len(addresses),
            'total_transactions': len(txs),
            'incoming_count': len(classified['incoming']),
            'self_withdrawal_count': len(classified['self_withdrawal']),
            'vendor_withdrawal_count': len(classified['vendor_withdrawal']),
            'unknown_count': len(classified['unknown']),
            'total_incoming_sats': total_incoming,
            'total_incoming_btc': total_incoming / 1e8,
            'total_self_moved_sats': total_self_moved,
            'total_self_moved_btc': total_self_moved / 1e8,
            'total_vendor_sent_sats': total_vendor_sent,
            'total_vendor_sent_btc': total_vendor_sent / 1e8,
            'total_fees_paid_sats': total_fees_paid,
            'total_fees_paid_btc': total_fees_paid / 1e8,
            'net_flow_sats': total_incoming - total_vendor_sent - total_fees_paid,
            'net_flow_btc': (total_incoming - total_vendor_sent - total_fees_paid) / 1e8,
        },
        'top_vendor_destinations': [
            {
                'address': addr,
                'total_btc': info['total_sats'] / 1e8,
                'total_sats': info['total_sats'],
                'tx_count': info['count'],
                'sample_txids': info['txids']
            }
            for addr, info in top_destinations[:50]
        ],
        'transactions': {
            'incoming': classified['incoming'],
            'self_withdrawal': classified['self_withdrawal'],
            'vendor_withdrawal': classified['vendor_withdrawal'],
        }
    }
    
    with open(REPORT_FILE, 'w') as f:
        json.dump(report, f, indent=2)
    
    print("\n" + "="*60)
    print("CLASSIFICATION REPORT")
    print("="*60)
    print(f"Incoming transactions:       {len(classified['incoming']):>6}")
    print(f"Self-withdrawal transactions: {len(classified['self_withdrawal']):>6}")
    print(f"Vendor-withdrawal transactions: {len(classified['vendor_withdrawal']):>6}")
    print(f"Unknown:                      {len(classified['unknown']):>6}")
    print(f"\nTotal incoming:     {total_incoming/1e8:>12.8f} BTC")
    print(f"Total self-moved:   {total_self_moved/1e8:>12.8f} BTC")
    print(f"Total vendor sent:  {total_vendor_sent/1e8:>12.8f} BTC")
    print(f"Total fees paid:    {total_fees_paid/1e8:>12.8f} BTC")
    print(f"\nNet flow:           {report['summary']['net_flow_btc']:>12.8f} BTC")
    print(f"\nTop 10 vendor destinations:")
    for i, (addr, info) in enumerate(top_destinations[:10], 1):
        print(f"  {i}. {addr}: {info['total_sats']/1e8:.8f} BTC ({info['count']} tx)")
    
    print(f"\nReport saved to: {REPORT_FILE}")

if __name__ == '__main__':
    main()
