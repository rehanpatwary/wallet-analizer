# Crypto Wallet Recovery & Transaction Analysis System

A comprehensive Python tool to recover Bitcoin (BTC) and Litecoin (LTC) wallet addresses from BIP39 mnemonic phrases, scan blockchain transaction history, and classify outgoing transactions into **self-withdrawals** and **vendor withdrawals**.

## Features

- **Multi-Path Address Discovery**: Scans BIP44 (Legacy), BIP49 (SegWit Compatible), and BIP84 (Native SegWit) derivation paths
- **Change Address Detection**: Also scans internal/change chains (m/.../1/*) for complete wallet coverage
- **Blockchain API Integration**: Uses mempool.space, BlockCypher, Chain.so, and Blockchain.info for redundancy
- **Transaction Classification**:
  - **Incoming**: Deposits received from external addresses
  - **Outgoing (Vendor Withdrawal)**: Payments sent to external addresses
  - **Self-Transfer**: Funds moved between your own addresses (consolidation)
- **Fund Flow Mapping**: Visual HTML report showing where your money went
- **CSV Export**: Machine-readable export for spreadsheet analysis

## Installation

```bash
pip3 install ecdsa base58 mnemonic bech32
```

Or use the provided requirements file:
```bash
pip3 install -r requirements.txt
```

## Usage

### Basic Usage

```bash
python3 wallet_analyzer.py --mnemonic "your twelve word seed phrase here" --coin btc
```

### With Options

```bash
python3 wallet_analyzer.py \
  --mnemonic "word1 word2 word3 word4 word5 word6 word7 word8 word9 word10 word11 word12" \
  --coin btc \
  --depth 100 \
  --gap 20 \
  --output my_wallet_report.html \
  --csv my_wallet_report.csv
```

### From File

```bash
# Save your mnemonic to a file first
echo "your twelve word seed phrase" > seed.txt

# Run the analyzer
python3 wallet_analyzer.py --mnemonic-file seed.txt --coin btc
```

### Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `--mnemonic` | BIP39 mnemonic phrase (space-separated words) | - |
| `--mnemonic-file` | File containing the mnemonic phrase | - |
| `--coin` | Cryptocurrency to analyze (`btc` or `ltc`) | `btc` |
| `--depth` | Maximum address index to scan | `100` |
| `--gap` | Consecutive empty addresses before stopping | `20` |
| `--output` | Output HTML report path | `wallet_report.html` |
| `--csv` | Output CSV report path | `wallet_report.csv` |
| `--passphrase` | Optional BIP39 passphrase | (empty) |

## How It Works

### 1. Address Discovery
The tool derives addresses from your mnemonic across multiple derivation paths:
- **BIP44 Legacy**: `m/44'/0'/0'/0/i` → `1xxx...` addresses
- **BIP49 SegWit**: `m/49'/0'/0'/0/i` → `3xxx...` addresses  
- **BIP84 Native SegWit**: `m/84'/0'/0'/0/i` → `bc1q...` addresses

It also scans change addresses at `m/...'/1/i`.

### 2. Transaction Fetching
For each discovered address with transaction history, the tool queries multiple blockchain APIs to retrieve all related transactions.

### 3. Classification Logic

| Condition | Classification |
|-----------|---------------|
| No inputs from our addresses | **Incoming** (deposit) |
| Inputs from our addresses, outputs to external addresses | **Outgoing / Vendor Withdrawal** |
| Inputs from our addresses, all outputs to our addresses | **Self-Transfer** |

### 4. Report Generation

The HTML report includes:
- **Summary Dashboard**: Total received, sent, net balance, transaction counts
- **Address List**: All discovered addresses with derivation paths
- **Transaction History**: Chronological table with direction, amounts, fees
- **Vendor Destinations**: Aggregated list of where money was sent
- **Fund Flow Map**: Visual diagram of outgoing and self-transfer transactions
- **JSON Export**: Raw transaction data for further analysis

## Security Notes

- **Never share your mnemonic phrase** with anyone or commit it to version control
- This tool runs entirely locally - your mnemonic is never sent to any server
- The tool only uses **public blockchain APIs** to query transaction data
- Consider running on an offline/air-gapped machine for maximum security

## Example Output

```
============================================================
  CRYPTO WALLET RECOVERY & ANALYSIS SYSTEM
============================================================
Coin: BTC
Scan depth: 100
Gap limit: 20

[DISCOVERY] Scanning BTC addresses...
  Path: bip44_legacy (external chain)
    [FOUND] 1LqBGSKu... (bip44_legacy, external, index 0)
    [FOUND] 1Ak8PffB... (bip44_legacy, external, index 1)
  Path: bip84_native_segwit (external chain)
    [FOUND] bc1qcr8te... (bip84_native_segwit, external, index 0)

[DISCOVERY] Total addresses with transactions: 3

[TX FETCH] Retrieving transaction history...
  Fetching txs for 1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA...
  Fetching txs for 1Ak8PffB2meyfYnbXZR9EGfLfFZVpzJvQP...
  Fetching txs for bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu...
[TX FETCH] Found 15 unique transactions

[CLASSIFY] Analyzing transaction directions...

============================================================
  ANALYSIS SUMMARY
============================================================
  Addresses Found:     3
  Total Transactions:  15
  Incoming:            8
  Outgoing:            5
  Self-Transfers:      2
  Total Received:      +12.50000000 BTC
  Total Sent:          -8.25000000 BTC
  Net Balance:         +4.25000000 BTC
  Self-Moved:          +2.00000000 BTC

  VENDOR WITHDRAWAL DESTINATIONS:
    → 1Abc...: 3.50000000 BTC
    → bc1q...: 2.75000000 BTC
    → 3Def...: 2.00000000 BTC

[REPORT] HTML report saved to: wallet_report.html
[REPORT] CSV report saved to: wallet_report.csv
```

## License

MIT License - Use at your own risk. Verify all transactions independently before taking any action.
