#!/usr/bin/env python3
"""Combine the completed BTC and LTC analyses into one complete-history report."""
import json
import os
from datetime import datetime, timezone

OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def load(name):
    path = os.path.join(OUT_DIR, name)
    with open(path) as f:
        return json.load(f)


def main():
    btc = load('final_report.json')
    ltc = load('ltc_report_litecoinspace.json')

    bfin = btc['financial_summary']
    bw = btc['wallet_summary']

    combined = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'mnemonic_fingerprint': 'resemble...cliff',
        'bitcoin': {
            'derivation': bw['mnemonic_derivation'],
            'addresses_with_activity': bw['addresses_with_activity'],
            'first_transaction': bw['first_transaction'],
            'last_transaction': bw['last_transaction'],
            'total_incoming_btc': bfin['total_incoming_btc'],
            'total_outgoing_btc': bfin['total_outgoing_btc'],
            'total_fees_btc': bfin['total_fees_btc'],
            'net_flow_btc': bfin['net_flow_btc'],
            'incoming_tx_count': bfin['incoming_tx_count'],
            'outgoing_tx_count': bfin['outgoing_tx_count'],
        },
        'litecoin': {
            'source': ltc['source'],
            'addresses_with_activity': ltc['address_count'],
            'total_incoming_ltc': ltc['total_incoming_ltc'],
            'vendor_outgoing_ltc': ltc['vendor_total_ltc'],
            'self_withdrawal_ltc': ltc['self_total_ltc'],
            'fees_ltc': ltc['fees_ltc'],
            'net_flow_ltc': ltc['net_flow_ltc'],
            'transaction_count': ltc['transaction_count'],
            'incoming_count': ltc['incoming_count'],
            'self_withdrawal_count': ltc['outgoing_self_count'],
            'vendor_withdrawal_count': ltc['outgoing_vendor_count'],
        },
    }

    with open(os.path.join(OUT_DIR, 'combined_history_report.json'), 'w') as f:
        json.dump(combined, f, indent=2)

    b, l = combined['bitcoin'], combined['litecoin']
    lines = [
        "=" * 72,
        "        COMBINED WALLET HISTORY - BTC + LTC (mnemonic resemble...cliff)",
        "=" * 72,
        f"Generated: {combined['generated_at']}",
        "",
        "BITCOIN (BIP44, complete)",
        "-" * 72,
        f"  Active addresses:   {b['addresses_with_activity']}",
        f"  First transaction:  {b['first_transaction']}",
        f"  Last transaction:   {b['last_transaction']}",
        f"  Total incoming:     {b['total_incoming_btc']:.8f} BTC  ({b['incoming_tx_count']} txs)",
        f"  Total outgoing:     {b['total_outgoing_btc']:.8f} BTC  ({b['outgoing_tx_count']} txs)",
        f"  Fees paid:          {b['total_fees_btc']:.8f} BTC",
        f"  NET FLOW:           {b['net_flow_btc']:.8f} BTC",
        "",
        "LITECOIN (BIP44/49/84, complete via litecoinspace.org)",
        "-" * 72,
        f"  Active addresses:   {l['addresses_with_activity']}",
        f"  Total transactions: {l['transaction_count']}",
        f"  Total incoming:     {l['total_incoming_ltc']:.8f} LTC  ({l['incoming_count']} txs)",
        f"  Vendor withdrawals: {l['vendor_outgoing_ltc']:.8f} LTC  ({l['vendor_withdrawal_count']} txs)",
        f"  Self withdrawals:   {l['self_withdrawal_ltc']:.8f} LTC  ({l['self_withdrawal_count']} txs)",
        f"  Fees paid:          {l['fees_ltc']:.8f} LTC",
        f"  NET FLOW:           {l['net_flow_ltc']:.8f} LTC",
        "",
        "=" * 72,
    ]
    text = "\n".join(lines)
    with open(os.path.join(OUT_DIR, 'combined_history_report.txt'), 'w') as f:
        f.write(text + "\n")
    print(text)


if __name__ == '__main__':
    main()
