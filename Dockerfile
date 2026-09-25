FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Data dumps are excluded via .dockerignore; restore the committed .gz archives.
RUN gunzip -kf all_transactions.json.gz all_ltc_transactions.json.gz || true

# Provide the mnemonic at runtime:
#   docker build -t wallet-analizer .
#   docker run -e WALLET_MNEMONIC="word1 ... word12" wallet-analizer make ledger
CMD ["make", "help"]
