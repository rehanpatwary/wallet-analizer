#!/usr/bin/env python3
"""Stage 7: merchant funding-chain visualization -> results/merchant/merchant_chain.html

Renders the proven payment chain:
  legacy xpub accounts --(direct)--> merchant
  legacy xpub accounts --(sweep)--> native-segwit self wallet --(spend)--> merchant
with per-account address-depth stats and a monthly timeline by path.
Pure HTML/SVG, no external libs (works offline, any workspace).
"""

import csv
import datetime
import html
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wallet_config import PROJ

MERCH = os.path.join(PROJ, "data", "merchant", "merchant_txs.csv")
DEPTH = os.path.join(PROJ, "results", "merchant", "merchant_depth.json")
DESTS = os.path.join(PROJ, "results", "classify", "destinations.csv")
OUTP = os.path.join(PROJ, "results", "merchant", "merchant_chain.html")


def money(x):
    return f"${x:,.0f}"


def main():
    d = json.load(open(DEPTH))
    s = d["summary"]
    chain = s["chain"]
    merch = list(csv.DictReader(open(MERCH)))
    dests = {r["address"]: r for r in csv.DictReader(open(DESTS))}
    ours = {r["txid"]: r for r in d["rows"]}
    unresolved = {u["txid"] for u in chain["unresolved"]}

    # ---- per-account direct-to-merchant stats (txs + usd from rows) ----
    acct = defaultdict(lambda: {"txs": 0, "usd": 0.0, "addrs": set()})
    for r in d["rows"]:
        usd = float(r["usd"] or 0)
        srcs = {f["src"] for f in r["funding"] if f["src"].startswith("xpub:")}
        for src in srcs:
            slug = src.split(":", 1)[1]
            acct[slug]["txs"] += 1 if len(srcs) == 1 else 0
            acct[slug]["usd"] += usd / max(1, len(srcs))
            for f in r["funding"]:
                if f["src"] == src:
                    acct[slug]["addrs"].add(f["addr"])

    # ---- path per merchant tx ----
    def path_of(r):
        h = r["transaction_hash"].strip()
        if h in ours:
            return "direct"
        if h in unresolved:
            return "unresolved"
        return "segwit"

    # ---- monthly timeline ----
    months = defaultdict(lambda: defaultdict(float))
    for r in merch:
        m = r["created_at"][:7]
        months[m][path_of(r)] += float(r["amount"] or 0)
        months[m]["_n"] += 1
    mkeys = sorted(months)

    # ---- merchant deposit addresses ----
    maddrs = defaultdict(lambda: {"n": 0, "usd": 0.0, "coin": 0.0,
                                  "first": None, "last": None})
    for r in merch:
        a = maddrs[r["input_address"]]
        a["n"] += 1
        a["usd"] += float(r["amount"] or 0)
        a["coin"] += float(r["amount_btc"] or 0)
        t = r["created_at"]
        a["first"] = t if a["first"] is None or t < a["first"] else a["first"]
        a["last"] = t if a["last"] is None or t > a["last"] else a["last"]
    maddrs = dict(sorted(maddrs.items(), key=lambda kv: -kv[1]["usd"]))

    tot_usd = sum(v["usd"] for v in maddrs.values())
    direct_usd = sum(v["usd"] for v in chain["direct_from_our_wallets"].values())
    segwit_usd = sum(v["usd"] for v in chain["via_segwit_self_layer"].values())
    unres_usd = tot_usd - direct_usd - segwit_usd

    # ---- flow edge labels ----
    acct_rows = ""
    for slug in sorted(acct, key=lambda k: -acct[k]["usd"]):
        a = acct[slug]
        st = s["index_stats"].get(f"xpub:{slug}", {})
        ext = st.get("external") or {}
        dep = (f"ext idx {ext.get('min')}–{ext.get('max')}"
               if ext else "—")
        acct_rows += f"""<tr>
      <td><code>{slug}</code></td>
      <td class="num">{a['txs']}</td>
      <td class="num">{len(a['addrs'])}</td>
      <td class="num">{money(a['usd'])}</td>
      <td class="num">{dep}</td></tr>"""

    # ---- timeline SVG (stacked bars by path) ----
    W, H, bw, gap = 980, 220, 22, 6
    maxv = max(sum(v[k] for k in ("direct", "segwit", "unresolved"))
               for v in months.values()) or 1
    x0, y0 = 46, 10
    bars = ""
    COLORS = {"direct": "#58a6ff", "segwit": "#3fb950",
              "unresolved": "#8b949e"}
    labels = []
    for i, m in enumerate(mkeys):
        v = months[m]
        x = x0 + i * (bw + gap)
        y = y0 + H
        for k in ("direct", "segwit", "unresolved"):
            h = (v.get(k, 0) / maxv) * (H - 30)
            y -= h
            bars += (f'<rect x="{x}" y="{y:.1f}" width="{bw}" height="{h:.1f}" '
                     f'fill="{COLORS[k]}"><title>{m} {k}: '
                     f'{money(v.get(k,0))}</title></rect>')
        if i % 3 == 0:
            labels.append(f'<text x="{x}" y="{y0+H+16}" font-size="9" '
                          f'fill="#8b949e">{m}</text>')
    svg = f'''<svg viewBox="0 0 {W} {H+34}" width="100%">
      {bars}{''.join(labels)}
      <line x1="{x0-4}" y1="{y0+H}" x2="{W-6}" y2="{y0+H}" stroke="#30363d"/></svg>'''

    # ---- merchant address cards ----
    ma_rows = ""
    for addr, a in list(maddrs.items()):
        dd = dests.get(addr)
        cls = dd["class"] if dd else "not in ledger"
        ma_rows += f"""<tr>
      <td class="addr" title="{html.escape(addr)}">{html.escape(addr[:30])}…</td>
      <td class="num">{a['n']}</td>
      <td class="num">{money(a['usd'])}</td>
      <td class="num">{a['coin']:,.4f}</td>
      <td>{a['first'][:10]} → {a['last'][:10]}</td>
      <td><code>{cls}</code></td></tr>"""

    legend = "".join(
        f'<span class="lg"><span class="sw" style="background:{c}"></span>{n}</span> '
        for n, c in (("direct from xpubs", COLORS["direct"]),
                     ("via segwit self-layer", COLORS["segwit"]),
                     ("unresolved", COLORS["unresolved"])))

    page = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Merchant Funding Chain — wallet-analizer</title>
<style>
:root {{ --bg:#0d1117; --panel:#161b22; --line:#30363d; --fg:#e6edf3;
        --dim:#8b949e; --self:#3fb950; --acc:#58a6ff; --warn:#f0883e; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg);
       font:14px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif; }}
header {{ padding:28px 32px 12px; }}
h1 {{ margin:0 0 4px; font-size:22px; }}
h2 {{ margin:26px 0 10px; font-size:16px; color:var(--acc); }}
.sub {{ color:var(--dim); }}
main {{ padding:0 32px 48px; max-width:1200px; }}
table {{ width:100%; border-collapse:collapse; background:var(--panel);
        border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
th,td {{ padding:7px 12px; text-align:left; border-bottom:1px solid var(--line); }}
th {{ color:var(--dim); font-size:12px; text-transform:uppercase; }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
td.addr {{ font-family:ui-monospace,monospace; font-size:12px; }}
code {{ background:#21262d; padding:1px 6px; border-radius:5px; font-size:12px; }}
.flow {{ display:flex; align-items:stretch; gap:0; margin:20px 0 6px; }}
.col {{ flex:1; background:var(--panel); border:1px solid var(--line);
       border-radius:10px; padding:12px 14px; }}
.col.mid {{ border-color:var(--self); }}
.col h3 {{ margin:0 0 8px; font-size:13px; color:var(--dim); }}
.col .big {{ font-size:19px; font-weight:650; }}
.col .sm {{ color:var(--dim); font-size:12px; }}
.edge {{ width:150px; display:flex; flex-direction:column; justify-content:center;
        align-items:center; gap:4px; padding:0 6px; text-align:center; }}
.edge .line {{ height:2px; width:100%; background:var(--acc); position:relative; }}
.edge .line:after {{ content:"▶"; position:absolute; right:-4px; top:-8px;
                     color:var(--acc); font-size:11px; }}
.edge.g .line {{ background:var(--self); }}
.edge.g .line:after {{ color:var(--self); }}
.edge .lb {{ font-size:12px; color:var(--fg); }}
.edge .us {{ font-size:12px; color:var(--dim); }}
.chips {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }}
.chip {{ background:#21262d; border:1px solid var(--line); border-radius:20px;
        padding:2px 10px; font-size:11px; color:var(--dim); }}
.lg {{ margin-right:14px; font-size:12px; color:var(--dim); }}
.sw {{ display:inline-block; width:10px; height:10px; border-radius:2px;
      margin-right:5px; }}
.note {{ background:var(--panel); border:1px solid var(--line);
        border-left:3px solid var(--warn); border-radius:8px;
        padding:10px 16px; color:var(--dim); font-size:13px; }}
@media (max-width:900px) {{ .flow {{ flex-direction:column; }} .edge {{ width:auto; height:56px; }} }}
</style></head><body>
<header>
  <h1>Merchant Funding Chain</h1>
  <div class="sub">{len(merch)} merchant payments · {money(tot_usd)} ·
  Apr 2022 – Oct 2025 · proven from on-chain vin funding (not heuristics)</div>
</header>
<main>

<div class="flow">
  <div class="col">
    <h3>LEGACY XPUB ACCOUNTS (5 of 8 used)</h3>
    <div class="big">{money(direct_usd + segwit_usd)}</div>
    <div class="sm">reaching merchant via both paths (direct {money(direct_usd)} + via segwit {money(segwit_usd)})</div>
    <div class="chips">
      <span class="chip">1,076 distinct funding addresses</span>
      <span class="chip">external idx ≤ 6,119</span>
    </div>
  </div>
  <div class="edge">
    <div class="lb">direct · 96 payments</div>
    <div class="us">{money(direct_usd)}</div>
    <div class="line"></div>
  </div>
  <div class="col mid">
    <h3>NATIVE-SEGWIT SELF WALLET (bc1q / ltc1q)</h3>
    <div class="big">{money(segwit_usd)}</div>
    <div class="sm">220 payments · 297 distinct segwit funders —
    every one a SELF_FORWARD destination the xpubs swept to (fan-in = 1.0)</div>
    <div class="chips">
      <span class="chip">241 BTC funders</span><span class="chip">56 LTC funders</span>
    </div>
  </div>
  <div class="edge g">
    <div class="lb">sweep in · spend out</div>
    <div class="us">{money(segwit_usd)}</div>
    <div class="line"></div>
  </div>
  <div class="col">
    <h3>MERCHANT DEPOSIT ADDRESSES ({len(maddrs)})</h3>
    <div class="big">{money(tot_usd)}</div>
    <div class="sm">{len(merch)} payments · 10 legacy + 1 other styles ·
    unresolved third-party-funded: {money(unres_usd)} ({sum(1 for u in chain['unresolved'])} txs)</div>
  </div>
</div>

<h2>Monthly payment volume by funding path</h2>
<div class="sub">{legend}</div>
{svg}

<h2>Per-account depth used to fund merchant payments (direct path)</h2>
<table><tr><th>Account</th><th class="num">Merchant txs</th>
<th class="num">Funding addresses</th><th class="num">USD (direct)</th>
<th class="num">External index depth</th></tr>
{acct_rows}</table>

<h2>Merchant deposit addresses</h2>
<table><tr><th>Address</th><th class="num">Payments</th><th class="num">USD</th>
<th class="num">Coin</th><th>First → last</th><th>Ledger class</th></tr>
{ma_rows}</table>

<p class="note"><strong>Unresolved ({money(unres_usd)}):</strong>
{len(chain['unresolved'])} payments are funded by addresses outside every
wallet layer we control — likely a separate wallet of yours (native-segwit,
1–4 inputs per payment). Identifying that seed/xpub would close the last gap.
Also note: this chain retro-validates the stage-4 SELF_FORWARD label —
the segwit layer that receives $4.2M of sweeps is the same wallet that pays
this merchant.</p>
</main></body></html>"""

    with open(OUTP, "w") as f:
        f.write(page)
    print(f"wrote {OUTP} ({len(page)} bytes)")


if __name__ == "__main__":
    main()
