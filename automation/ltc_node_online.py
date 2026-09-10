"""Condition predicate: returns True when an LTC blockbook node is reachable.

Fast, side-effect-free network probe. Must stay well under the 30s limit.
Checks known Proxmox hosts, sweeps both subnets on blockbook ports, then
tries public fallbacks — all with short parallel timeouts.
"""

import json
import socket
import ssl
import urllib.request
import concurrent.futures

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

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def _is_blockbook(url, timeout=2):
    try:
        req = urllib.request.Request(
            f"{url}/api/v2",
            headers={'User-Agent': 'WalletAnalyzer/1.0'},
            method='GET',
        )
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as resp:
            data = json.loads(resp.read())
            # blockbook /api/v2 returns backend info incl. a "blockbook" key
            return isinstance(data, dict) and ('blockbook' in data or 'backend' in data)
    except Exception:
        return False


def _port_open(ip, port, timeout=1):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def should_fire(ctx):
    # 1) Known hosts — parallel blockbook probes
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        futs = [ex.submit(_is_blockbook, f"http://{ip}:{p}", 2)
                for ip, ports in KNOWN_HOSTS for p in ports]
        for f in concurrent.futures.as_completed(futs, timeout=15):
            try:
                if f.result(timeout=2):
                    return True
            except Exception:
                pass

    # 2) Subnet sweep — TCP probe only, fast
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as ex:
        futs = {ex.submit(_port_open, f"{s}.{i}", p, 1): (f"{s}.{i}", p)
                for s in SUBNETS for i in range(1, 50) for p in BLOCKBOOK_PORTS}
        candidates = []
        for f in concurrent.futures.as_completed(futs, timeout=10):
            try:
                if f.result(timeout=1):
                    candidates.append(futs[f])
            except Exception:
                pass

    for ip, port in candidates[:8]:  # bound the follow-up HTTP checks
        if _is_blockbook(f"http://{ip}:{port}", timeout=2):
            return True

    # 3) Public fallbacks
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futs = [ex.submit(_is_blockbook, base, 4) for base in PUBLIC_LTC_APIS]
        for f in concurrent.futures.as_completed(futs, timeout=8):
            try:
                if f.result(timeout=3):
                    return True
            except Exception:
                pass

    return False
