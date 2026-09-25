PYTHON ?= python3
SHELL := /bin/bash

.PHONY: help setup env scan fetch-txs restore-data fix-funding ledger report all clean-data

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

setup: ## Install python dependencies
	$(PYTHON) -m pip install -r requirements.txt

env: ## Create .env from template if missing
	@test -f .env || cp .env.example .env && echo ".env ready - fill in WALLET_MNEMONIC"

scan: ## Derive addresses from mnemonic and sweep all types/accounts
	$(PYTHON) scan_all_types_accounts.py

fetch-txs: ## Download full transaction history for all found addresses
	$(PYTHON) fetch_all_txs_fast.py

restore-data: ## Restore committed .gz tx archives into working JSON files
	gunzip -kf all_transactions.json.gz
	gunzip -kf all_ltc_transactions.json.gz

fix-funding: ## Find + fetch any funding txs missing from the tx dumps
	$(PYTHON) fetch_missing_funding.py both

ledger: ## Rebuild per-address ledger, USD valuation, and interactive HTML
	$(PYTHON) build_ledger.py

wallets: ## Run the full multi-wallet pipeline (wallets.json -> results/<name>/)
	$(PYTHON) run_wallets.py

report: ledger ## Alias for ledger (produces wallet_report.html)

all: setup env restore-data fix-funding ledger ## Full pipeline from a fresh clone
	@echo "Done. Open wallet_report.html"

clean-data: ## Remove raw tx dumps (they can be restored via restore-data)
	rm -f all_transactions.json all_ltc_transactions.json classified_report.json
