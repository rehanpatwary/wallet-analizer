#!/usr/bin/env python3
"""Build per-address ledger + interactive HTML wallet report (sweep-aware).

Ledger: for EVERY wallet address (BTC + LTC):
  - received (vout -> our address): coin + USD at the live rate that day
  - spent    (vin prevout <- our address): coin + USD at the live rate that day
Rates: Binance daily kline OPEN (UTC) for BTCUSDT / LTCUSDT.

Vendor destinations: many BTC withdrawals are sweep transactions whose inputs
include OTHER depositors' UTXOs (shared custodial sweeps, 100-300 inputs,
single output). Crediting the full output to us would overstate outflows.
Attribution per outgoing tx:
  our_net = our_inputs - our_fee_share - change_back_to_us
  our_fee_share = tx_fee * (our_inputs / total_inputs)   [proportional when shared]
  our_net is then distributed across external outputs proportionally by value.

Outputs:
  ledger.json / ledger.csv          per-address ledger (both coins)
  wallet_summary.json               wallet-level totals (reconciled)
  vendor_destinations_usd_report.{json,txt}  corrected destination ranking
  vendor_tx_usd.csv                 one row per tx->destination transfer
  wallet_report.html                self-contained interactive report
"""

import csv
import datetime
import json
import sys
import time
import urllib.request
from collections import defaultdict

from wallet_config import PROJ
BINANCE = "https://api.binance.com/api/v3/klines"
DAY = 86_400_000


def load_own_addresses(path):
    with open(path) as f:
        data = json.load(f)
    addrs = set()
    items = data.values() if isinstance(data, dict) else data
    for entry in items:
        if isinstance(entry, str):
            addrs.add(entry)
        elif isinstance(entry, dict) and entry.get("address"):
            addrs.add(entry["address"])
    return addrs


def fetch_daily_prices(symbol, start_ts, end_ts):
    prices = {}
    start_ms = int(start_ts) * 1000
    end_ms = int(end_ts) * 1000
    while start_ms <= end_ms:
        url = (f"{BINANCE}?symbol={symbol}&interval=1d&limit=1000"
               f"&startTime={start_ms}&endTime={end_ms}")
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    rows = json.load(r)
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
        if not rows:
            break
        for row in rows:
            d = datetime.datetime.fromtimestamp(row[0] / 1000, datetime.UTC).strftime("%Y-%m-%d")
            prices[d] = float(row[1])
        start_ms = rows[-1][0] + DAY
        if len(rows) < 1000:
            break
        time.sleep(0.5)
    return prices


def day_str(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime("%Y-%m-%d") if ts else None


def build_coin_ledger(txs, own, prices):
    """Returns (ledger, wallet totals, flow events, vendor transfers, stats)."""
    ledger = {}

    def entry(addr):
        if addr not in ledger:
            ledger[addr] = {"in": 0.0, "out": 0.0, "in_usd": 0.0, "out_usd": 0.0,
                            "n_in": 0, "n_out": 0, "first": None, "last": None}
        return ledger[addr]

    wallet = {"in": 0.0, "out": 0.0, "in_usd": 0.0, "out_usd": 0.0,
              "fees": 0.0, "fees_usd": 0.0, "to_dest": 0.0, "to_dest_usd": 0.0,
              "n_txs": 0, "n_sweep_txs": 0}
    flow, vendor_dests = [], []
    for tx in txs:
        vins = tx.get("vin", [])
        our_in_sats = 0
        total_in_sats = 0
        any_ours = False
        for vin in vins:
            prev = vin.get("prevout") or {}
            val = prev.get("value") or 0
            total_in_sats += val
            if prev.get("scriptpubkey_address") in own:
                our_in_sats += val
                any_ours = True
        vouts = tx.get("vout", [])
        out_to_us_sats = 0
        ext_vouts = []
        total_out_sats = 0
        for v in vouts:
            val = v.get("value")
            if val is None:
                continue
            total_out_sats += val
            addr = v.get("scriptpubkey_address")
            if addr in own:
                out_to_us_sats += val
            elif addr:
                ext_vouts.append((addr, val))
        if not any_ours and not out_to_us_sats:
            continue

        bt = (tx.get("status") or {}).get("block_time")
        price = prices.get(day_str(bt)) if bt else None
        wallet["n_txs"] += 1
        is_sweep = any_ours and total_in_sats > our_in_sats
        if is_sweep:
            wallet["n_sweep_txs"] += 1

        # per-address spent side
        for vin in vins:
            prev = vin.get("prevout") or {}
            addr, val = prev.get("scriptpubkey_address"), prev.get("value")
            if addr in own and val is not None:
                amt = val / 1e8
                e = entry(addr)
                e["out"] += amt
                e["n_out"] += 1
                wallet["out"] += amt
                if price:
                    usd = amt * price
                    e["out_usd"] += usd
                    wallet["out_usd"] += usd
                if bt:
                    e["first"] = bt if e["first"] is None else min(e["first"], bt)
                    e["last"] = bt if e["last"] is None else max(e["last"], bt)
        # per-address received side
        tx_in_usd = 0.0
        for v in vouts:
            addr, val = v.get("scriptpubkey_address"), v.get("value")
            if addr in own and val is not None:
                amt = val / 1e8
                e = entry(addr)
                e["in"] += amt
                e["n_in"] += 1
                wallet["in"] += amt
                if price:
                    usd = amt * price
                    e["in_usd"] += usd
                    wallet["in_usd"] += usd
                    tx_in_usd += usd
                if bt:
                    e["first"] = bt if e["first"] is None else min(e["first"], bt)
                    e["last"] = bt if e["last"] is None else max(e["last"], bt)

        # wallet-level outflow accounting
        fee_sats = max(0, total_in_sats - total_out_sats)
        if not any_ours:
            our_fee_sats = 0  # incoming funding tx: sender paid the fee
        elif is_sweep:
            our_fee_sats = fee_sats * (our_in_sats / total_in_sats)
        else:
            our_fee_sats = fee_sats  # all inputs ours: we paid the whole fee
        our_net_sats = our_in_sats - our_fee_sats - out_to_us_sats
        if our_net_sats < 0:  # numeric guard; should not happen
            our_net_sats = 0
        wallet["fees"] += our_fee_sats / 1e8
        wallet["to_dest"] += our_net_sats / 1e8
        if price:
            wallet["fees_usd"] += (our_fee_sats / 1e8) * price
            wallet["to_dest_usd"] += (our_net_sats / 1e8) * price

        # vendor attribution proportional to external output values
        ext_total = sum(val for _, val in ext_vouts)
        if ext_vouts and our_net_sats:
            for addr, val in ext_vouts:
                share = our_net_sats * (val / ext_total) / 1e8
                usd = share * price if price else None
                vendor_dests.append((bt, addr, share, usd))
        flow.append((bt or 0,
                     (our_net_sats / 1e8) * price if price else 0.0,
                     tx_in_usd))
    return ledger, wallet, flow, vendor_dests


def main():
    print("Loading address sets...", flush=True)
    own_btc = load_own_addresses(f"{PROJ}/found_addresses_btc_bip44_external.json")
    own_ltc = load_own_addresses(f"{PROJ}/found_addresses_ltc.json")

    print("Loading BTC txs...", flush=True)
    with open(f"{PROJ}/all_transactions.json") as f:
        raw_btc = json.load(f)
    btc_txs = list(raw_btc.values()) if isinstance(raw_btc, dict) else raw_btc
    del raw_btc
    print("Loading LTC txs...", flush=True)
    with open(f"{PROJ}/all_ltc_transactions.json") as f:
        raw_ltc = json.load(f)
    ltc_txs = list(raw_ltc) if isinstance(raw_ltc, list) else list(raw_ltc.values())
    del raw_ltc

    def rng(txs, own):
        lo = hi = None
        for tx in txs:
            addrs = [(vin.get("prevout") or {}).get("scriptpubkey_address") for vin in tx.get("vin", [])]
            outs = {v.get("scriptpubkey_address") for v in tx.get("vout", [])}
            if not any(a in own for a in addrs) and not (outs & own):
                continue
            bt = (tx.get("status") or {}).get("block_time")
            if bt:
                lo = bt if lo is None else min(lo, bt)
                hi = bt if hi is None else max(hi, bt)
        return lo, hi

    lo_b, hi_b = rng(btc_txs, own_btc)
    lo_l, hi_l = rng(ltc_txs, own_ltc)
    print(f"BTC activity {day_str(lo_b)} .. {day_str(hi_b)}", flush=True)
    print(f"LTC activity {day_str(lo_l)} .. {day_str(hi_l)}", flush=True)

    print("Fetching BTC rates...", flush=True)
    prices_btc = fetch_daily_prices("BTCUSDT", lo_b - 86400, hi_b + 86400)
    time.sleep(0.5)
    print("Fetching LTC rates...", flush=True)
    prices_ltc = fetch_daily_prices("LTCUSDT", lo_l - 86400, hi_l + 86400)

    print("Building BTC ledger...", flush=True)
    ledger_b, wallet_b, flow_b, vend_b = build_coin_ledger(btc_txs, own_btc, prices_btc)
    print("Building LTC ledger...", flush=True)
    ledger_l, wallet_l, flow_l, vend_l = build_coin_ledger(ltc_txs, own_ltc, prices_ltc)

    for name, w, ledger in (("BTC", wallet_b, ledger_b), ("LTC", wallet_l, ledger_l)):
        print(f"{name}: addresses={len(ledger)}, in={w['in']:.8f} (${w['in_usd']:,.2f}), "
              f"out={w['out']:.8f} (${w['out_usd']:,.2f}), "
              f"fees={w['fees']:.8f} (${w['fees_usd']:,.2f}), "
              f"to_dest={w['to_dest']:.8f} (${w['to_dest_usd']:,.2f}), "
              f"txs={w['n_txs']} ({w['n_sweep_txs']} sweeps)", flush=True)

    # ---- persist ----
    ledger_rows = []
    for coin, ledger in (("BTC", ledger_b), ("LTC", ledger_l)):
        for addr, e in ledger.items():
            ledger_rows.append({
                "coin": coin, "address": addr,
                "n_in": e["n_in"], "in": round(e["in"], 8), "in_usd": round(e["in_usd"], 2),
                "n_out": e["n_out"], "out": round(e["out"], 8), "out_usd": round(e["out_usd"], 2),
                "first": day_str(e["first"]), "last": day_str(e["last"]),
            })

    def wallet_summary(w, ledger):
        return {k: (round(v, 8) if isinstance(v, float) else v) for k, v in w.items()} | {"addresses": len(ledger)}

    summary = {
        "btc": wallet_summary(wallet_b, ledger_b),
        "ltc": wallet_summary(wallet_l, ledger_l),
        "price_source": "Binance daily open (UTC) on tx day",
        "attribution": "Sweep txs (shared inputs): destinations credited with our net contribution "
                       "= our inputs - proportional fee share - change back to us",
    }
    with open(f"{PROJ}/ledger.json", "w") as f:
        json.dump({"summary": summary, "addresses": ledger_rows}, f)
    with open(f"{PROJ}/wallet_summary.json", "w") as f:
        json.dump(summary, f, indent=1)
    with open(f"{PROJ}/ledger.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["coin", "address", "n_in", "total_in", "total_in_usd",
                    "n_out", "total_out", "total_out_usd", "first_active", "last_active"])
        for r in ledger_rows:
            w.writerow([r["coin"], r["address"], r["n_in"], r["in"], r["in_usd"],
                        r["n_out"], r["out"], r["out_usd"], r["first"], r["last"]])

    def agg(vends):
        d = defaultdict(lambda: [0.0, 0.0, 0, None, None])
        for ts, addr, amt, usd in vends:
            e = d[addr]
            e[0] += amt
            e[2] += 1
            if usd:
                e[1] += usd
            if ts:
                e[3] = ts if e[3] is None else min(e[3], ts)
                e[4] = ts if e[4] is None else max(e[4], ts)
        return sorted(([a, round(v[0], 8), round(v[1], 2), v[2], day_str(v[3]), day_str(v[4])]
                       for a, v in d.items()), key=lambda r: r[2], reverse=True)

    dests = {"btc": agg(vend_b), "ltc": agg(vend_l)}
    totals = {"btc": round(wallet_b["to_dest_usd"], 2), "ltc": round(wallet_l["to_dest_usd"], 2)}

    with open(f"{PROJ}/vendor_destinations_usd_report.json", "w") as f:
        json.dump({"summary": summary, "destinations": dests}, f, indent=1)
    with open(f"{PROJ}/vendor_destinations_usd_report.txt", "w") as f:
        for coin in ("btc", "ltc"):
            rows = dests[coin]
            f.write(f"=== {rows and 'BTC' if coin=='btc' else 'LTC'} vendor destinations "
                    f"(sweep-aware net attribution) ===\n")
            f.write(f"total net attributed to destinations: ${totals[coin]:,.2f} "
                    f"({summary[coin]['to_dest']:.8f} {coin.upper()})\n")
            f.write("note: sweep txs share inputs with other depositors; amounts = our net contribution\n\n")
            f.write(f"{'rank':>4} {'address':<40} {'amount':>18} {'USD':>16} {'txs':>5}  period\n")
            for i, r in enumerate(rows, 1):
                f.write(f"{i:>4} {r[0]:<40} {r[1]:>18.8f} {r[2]:>16,.2f} {r[3]:>5}  {r[4] or '-'} .. {r[5] or '-'}\n")
            f.write("\n")
    with open(f"{PROJ}/vendor_tx_usd.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["coin", "date_utc", "destination", "amount", "usd_value"])
        for coin, vends in (("BTC", vend_b), ("LTC", vend_l)):
            for ts, addr, amt, usd in sorted(vends, key=lambda x: x[0] or 0):
                w.writerow([coin, day_str(ts) or "", addr, f"{amt:.8f}",
                            f"{usd:.2f}" if usd is not None else ""])

    chart = {"btc": sorted([t for t in flow_b if t[0]], key=lambda x: x[0]),
             "ltc": sorted([t for t in flow_l if t[0]], key=lambda x: x[0])}

    print("Writing HTML...", flush=True)
    write_html(summary, ledger_rows, dests, chart)
    print("Done.")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wallet Forensic Report — BTC &amp; LTC</title>
<style>
:root{--bg:#0d1117;--panel:#161b22;--border:#30363d;--text:#e6edf3;--muted:#8b949e;
--green:#3fb950;--red:#f85149;--gold:#d29922;--accent:#58a6ff;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif;padding:24px;max-width:1280px;margin:0 auto}
h1{font-size:22px;margin-bottom:4px}
h2{font-size:16px;margin:28px 0 12px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
.sub{color:var(--muted);font-size:13px;margin-bottom:20px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:12px;margin:16px 0}
.card{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:14px 16px}
.card .k{font-size:12px;color:var(--muted)}
.card .v{font-size:19px;font-weight:600;margin-top:2px}
.card .v.g{color:var(--green)}.card .v.r{color:var(--red)}
table{border-collapse:collapse;width:100%;background:var(--panel);border:1px solid var(--border);border-radius:8px;overflow:hidden}
th,td{padding:7px 10px;text-align:right;border-bottom:1px solid var(--border);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th{background:#1c2128;color:var(--muted);font-size:12px;cursor:pointer;user-select:none}
tr:hover td{background:#1c212833}
td.addr,th.addr{font-family:ui-monospace,Menlo,monospace;font-size:12px;max-width:340px;overflow:hidden;text-overflow:ellipsis}
.pos{color:var(--green)}.neg{color:var(--red)}
.wrap{overflow-x:auto;border-radius:8px}
.toggles{display:flex;gap:14px;align-items:center;margin:10px 0;flex-wrap:wrap}
.toggles label{color:var(--muted);font-size:13px;cursor:pointer;display:flex;gap:5px;align-items:center}
canvas#chart{width:100%;height:340px;background:var(--panel);border:1px solid var(--border);border-radius:8px}
#tooltip{position:fixed;pointer-events:none;background:#1c2128;border:1px solid var(--border);border-radius:6px;padding:8px 10px;font-size:12px;display:none;z-index:9}
.controls{display:flex;gap:10px;margin:12px 0;flex-wrap:wrap}
.controls input,.controls select{background:var(--panel);border:1px solid var(--border);color:var(--text);border-radius:6px;padding:7px 10px;font-size:13px}
.controls input{width:340px}
#pageInfo{color:var(--muted);font-size:13px;align-self:center}
.pager button{background:var(--panel);border:1px solid var(--border);color:var(--text);border-radius:6px;padding:6px 14px;cursor:pointer;font-size:13px}
.pager button:disabled{opacity:.4;cursor:default}
.note{font-size:12px;color:var(--muted);margin-top:8px}
</style>
</head>
<body>
<h1>Wallet Forensic Report — Bitcoin &amp; Litecoin</h1>
<div class="sub">BIP44 legacy wallet · __ADDRS__ addresses · rates: Binance daily open (UTC) on the day of each transaction · generated __DATE__</div>

<h2>Wallet Totals</h2>
<div class="cards" id="cards"></div>

<h2>Cumulative USD Flow Over Time</h2>
<div class="toggles">
<label><input type="checkbox" id="showOut" checked> Outflow (net to destinations)</label>
<label><input type="checkbox" id="showIn" checked> Inflow (received)</label>
<label><input type="radio" name="coin" value="both" checked> BTC + LTC</label>
<label><input type="radio" name="coin" value="btc"> BTC only</label>
<label><input type="radio" name="coin" value="ltc"> LTC only</label>
</div>
<canvas id="chart"></canvas>
<div class="note">Outflow = our net contribution per transaction (inputs − fee share − change back). USD valued at the live rate on the day each transaction confirmed. Hover the chart for exact figures.</div>

<h2>Top Vendor Destinations by USD</h2>
<div class="wrap"><table id="destBtc"></table></div>
<div style="height:14px"></div>
<div class="wrap"><table id="destLtc"></table></div>
<div class="note">Sweep transactions share inputs with other depositors (custodial sweeps of 100–300 inputs). Amounts shown are OUR net contribution to each destination, not the sweep total.</div>

<h2>Full Address Ledger (__ADDRS__ addresses)</h2>
<div class="controls">
<input id="q" placeholder="Search address…">
<select id="coinSel"><option value="">Both coins</option><option>BTC</option><option>LTC</option></select>
<select id="onlyActive"><option value="">All addresses</option><option value="used">Only used (tx &gt; 0)</option></select>
<span id="pageInfo"></span>
</div>
<div class="wrap"><table id="ledger"></table></div>
<div class="pager" style="margin-top:10px;display:flex;gap:8px">
<button id="prev">‹ Prev</button><button id="next">Next ›</button>
</div>
<div class="note">In = received by this address · Out = spent by this address, each valued per-transaction at the actual rate that day. Net USD = in − out. Addresses with zero transactions were swept but never funded.</div>

<div id="tooltip"></div>

<script>
const DATA = __DATA__;
const fmt$ = n => '$' + n.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const fmtC = n => n.toLocaleString('en-US',{maximumFractionDigits:8});

(function(){
  const s = DATA.summary, el = document.getElementById('cards');
  const card = (k,v,cls) => `<div class="card"><div class="k">${k}</div><div class="v ${cls||''}">${v}</div></div>`;
  el.innerHTML =
    card('BTC received', fmtC(s.btc.in)+' BTC') + card('BTC received · USD at tx-day rate', fmt$(s.btc.in_usd),'g') +
    card('BTC spent (inputs)', fmtC(s.btc.out)+' BTC') + card('BTC network fees (our share)', fmtC(s.btc.fees)+' BTC') +
    card('BTC net to destinations', fmtC(s.btc.to_dest)+' BTC') + card('BTC net to destinations · USD', fmt$(s.btc.to_dest_usd),'r') +
    card('LTC received', fmtC(s.ltc.in)+' LTC') + card('LTC received · USD at tx-day rate', fmt$(s.ltc.in_usd),'g') +
    card('LTC spent (inputs)', fmtC(s.ltc.out)+' LTC') + card('LTC network fees', fmtC(s.ltc.fees)+' LTC') +
    card('LTC net to destinations', fmtC(s.ltc.to_dest)+' LTC') + card('LTC net to destinations · USD', fmt$(s.ltc.to_dest_usd),'r') +
    card('Combined net to destinations · USD', fmt$(s.btc.to_dest_usd + s.ltc.to_dest_usd),'r') +
    card('Combined fees · USD', fmt$(s.btc.fees_usd + s.ltc.fees_usd));
})();

function cumulate(flow){
  let o=0,i=0; return flow.map(([t,out,inn])=>{o+=out;i+=inn;return [t,o,i];});
}
const CB=cumulate(DATA.chart.btc), CL=cumulate(DATA.chart.ltc);
const cv=document.getElementById('chart'), ctx=cv.getContext('2d'), tt=document.getElementById('tooltip');
function series(){
  const coin=document.querySelector('input[name=coin]:checked').value;
  const so=document.getElementById('showOut').checked, si=document.getElementById('showIn').checked;
  const out=[], inn=[];
  if(coin!=='ltc'){out.push(CB.map(p=>[p[0],p[1]]));inn.push(CB.map(p=>[p[0],p[2]]));}
  if(coin!=='btc'){out.push(CL.map(p=>[p[0],p[1]]));inn.push(CL.map(p=>[p[0],p[2]]));}
  const sum=(arrs)=>arrs[0].map((p,idx)=>[p[0],arrs.reduce((a,s)=>a+s[idx][1],0)]);
  const r=[];
  if(so&&out.length)r.push({pts:sum(out),color:'#f85149',label:'Outflow'});
  if(si&&inn.length)r.push({pts:sum(inn),color:'#3fb950',label:'Inflow'});
  return r;
}
function draw(){
  const dpr=window.devicePixelRatio||1, W=cv.clientWidth, H=cv.clientHeight;
  cv.width=W*dpr; cv.height=H*dpr; ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,W,H);
  const s=series(); if(!s.length)return;
  let all=[]; s.forEach(x=>all=all.concat(x.pts));
  const minT=Math.min(...all.map(p=>p[0])), maxT=Math.max(...all.map(p=>p[0]));
  let maxV=Math.max(...all.map(p=>p[1])); maxV*=1.05;
  const L=64,R=16,T=14,B=30, pw=W-L-R, ph=H-T-B;
  const X=t=>L+(t-minT)/(maxT-minT)*pw, Y=v=>T+ph-(v/maxV)*ph;
  ctx.strokeStyle='#30363d'; ctx.fillStyle='#8b949e'; ctx.font='11px sans-serif';
  for(let g=0;g<=4;g++){const v=maxV*g/4,y=Y(v);
    ctx.beginPath();ctx.moveTo(L,y);ctx.lineTo(W-R,y);ctx.stroke();
    ctx.fillText('$'+(v/1000).toFixed(0)+'k',6,y+3);}
  ctx.fillText(new Date(minT*1000).toISOString().slice(0,10),L,H-10);
  ctx.fillText(new Date(maxT*1000).toISOString().slice(0,10),W-R-70,H-10);
  s.forEach(sr=>{
    ctx.strokeStyle=sr.color; ctx.lineWidth=2; ctx.beginPath();
    sr.pts.forEach((p,i)=>{const x=X(p[0]),y=Y(p[1]); i?ctx.lineTo(x,y):ctx.moveTo(x,y);});
    ctx.stroke();
  });
  cv._geom={s,minT,maxT,maxV};
}
function hover(ev){
  const g=cv._geom; if(!g)return;
  const r=cv.getBoundingClientRect(), mx=ev.clientX-r.left;
  const t=g.minT+(mx-64)/(r.width-80)*(g.maxT-g.minT);
  let html='';
  g.s.forEach(sr=>{
    let best=sr.pts[0]; sr.pts.forEach(p=>{if(Math.abs(p[0]-t)<Math.abs(best[0]-t))best=p;});
    html+=`<div style="color:${sr.color}">${sr.label}: <b>${fmt$(best[1])}</b></div><div style="color:var(--muted)">${new Date(best[0]*1000).toISOString().slice(0,10)}</div>`;
  });
  tt.innerHTML=html; tt.style.display='block';
  tt.style.left=(ev.clientX+14)+'px'; tt.style.top=(ev.clientY+14)+'px';
}
cv.addEventListener('mousemove',hover); cv.addEventListener('mouseleave',()=>tt.style.display='none');
document.querySelectorAll('.toggles input').forEach(i=>i.addEventListener('change',draw));
window.addEventListener('resize',draw);

function destTable(el,rows,coin,limit){
  const head=`<tr><th class="addr">Address</th><th>${coin}</th><th>USD (tx-day rate)</th><th>Txs</th><th>First</th><th>Last</th></tr>`;
  const body=rows.slice(0,limit).map((r,i)=>
    `<tr><td class="addr" title="${r[0]}">${i+1}. ${r[0]}</td><td>${fmtC(r[1])}</td><td>${fmt$(r[2])}</td><td>${r[3]}</td><td>${r[4]||'-'}</td><td>${r[5]||'-'}</td></tr>`).join('');
  el.innerHTML=head+body;
}
destTable(document.getElementById('destBtc'),DATA.dests.btc,'BTC',15);
destTable(document.getElementById('destLtc'),DATA.dests.ltc,'LTC',15);

let rows=DATA.ledger, page=0; const PAGE=100, sort={key:'out_usd',dir:-1};
const cols=[['coin','Coin'],['address','Address'],['n_in','Tx In'],['in','In'],['in_usd','In USD'],
            ['n_out','Tx Out'],['out','Out'],['out_usd','Out USD'],['net_usd','Net USD'],['first','First'],['last','Last']];
function filtered(){
  const q=document.getElementById('q').value.trim().toLowerCase();
  const c=document.getElementById('coinSel').value;
  const used=document.getElementById('onlyActive').value;
  let r=rows;
  if(c)r=r.filter(x=>x.coin===c);
  if(used)r=r.filter(x=>x.n_in+x.n_out>0);
  if(q)r=r.filter(x=>x.address.toLowerCase().includes(q));
  return r.slice().sort((a,b)=>{
    const va=a[sort.key], vb=b[sort.key];
    return (typeof va==='string'?va.localeCompare(vb):va-vb)*sort.dir;
  });
}
function renderLedger(){
  const r=filtered(), start=page*PAGE, slice=r.slice(start,start+PAGE);
  const head='<tr>'+cols.map(([k,l])=>`<th class="${k==='address'?'addr':''}" data-k="${k}">${l}${sort.key===k?(sort.dir>0?' ▲':' ▼'):''}</th>`).join('')+'</tr>';
  const body=slice.map(x=>{
    const net=x.in_usd-x.out_usd;
    return `<tr><td>${x.coin}</td><td class="addr" title="${x.address}">${x.address}</td><td>${x.n_in}</td><td>${fmtC(x.in)}</td><td class="pos">${fmt$(x.in_usd)}</td><td>${x.n_out}</td><td>${fmtC(x.out)}</td><td class="neg">${fmt$(x.out_usd)}</td><td class="${net>=0?'pos':'neg'}">${fmt$(net)}</td><td>${x.first||'-'}</td><td>${x.last||'-'}</td></tr>`;
  }).join('');
  document.getElementById('ledger').innerHTML=head+body;
  document.getElementById('pageInfo').textContent=`${r.length} rows · page ${page+1} of ${Math.max(1,Math.ceil(r.length/PAGE))}`;
  document.getElementById('prev').disabled=page===0;
  document.getElementById('next').disabled=start+PAGE>=r.length;
  document.querySelectorAll('#ledger th').forEach(th=>th.onclick=()=>{
    const k=th.dataset.k; if(sort.key===k)sort.dir*=-1;else{sort.key=k;sort.dir=-1;} page=0; renderLedger();});
}
['q','coinSel','onlyActive'].forEach(id=>document.getElementById(id).addEventListener('input',()=>{page=0;renderLedger();}));
document.getElementById('prev').onclick=()=>{if(page>0){page--;renderLedger();}};
document.getElementById('next').onclick=()=>{page++;renderLedger();};
renderLedger(); draw();
</script>
</body></html>
"""


def write_html(summary, ledger_rows, dests, chart):
    payload = {
        "summary": summary,
        "ledger": ledger_rows,
        "dests": {"btc": dests["btc"][:50], "ltc": dests["ltc"][:50]},
        "chart": {
            "btc": [[t, round(o, 2), round(i, 2)] for t, o, i in chart["btc"]],
            "ltc": [[t, round(o, 2), round(i, 2)] for t, o, i in chart["ltc"]],
        },
    }
    data_js = json.dumps(payload)
    html = (HTML_TEMPLATE
            .replace("__DATA__", data_js)
            .replace("__ADDRS__", f"{len(ledger_rows):,}")
            .replace("__DATE__", datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
    with open(f"{PROJ}/wallet_report.html", "w") as f:
        f.write(html)
    print(f"  wallet_report.html: {len(html)/1e6:.1f} MB")


if __name__ == "__main__":
    sys.exit(main())
