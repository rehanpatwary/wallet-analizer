#!/usr/bin/env python3
"""Stage 5: render results/classify/report.html from stage-4 outputs."""

import csv
import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wallet_config import PROJ

OUT = os.path.join(PROJ, "results", "classify")

CLASS_LABEL = {
    "SELF_FORWARD": "Self-move (forwarding wallet)",
    "SELF_SWEEP": "Self-move (receive-only / sweep)",
    "MIXED_CUSTODIAL": "3rd-party — custodial / exchange (shared addr)",
    "FIXED_MERCHANT": "3rd-party — fixed merchant (recurring pattern)",
    "VENDOR_REPEAT": "3rd-party — repeat vendor",
    "VENDOR_ONCE": "3rd-party — one-off payment",
    "UNKNOWN": "Unknown (insufficient evidence)",
}
CLASS_ORDER = ["SELF_FORWARD", "SELF_SWEEP", "MIXED_CUSTODIAL",
               "FIXED_MERCHANT", "VENDOR_REPEAT", "VENDOR_ONCE", "UNKNOWN"]


def money(x):
    return f"${x:,.0f}"


def coin(x):
    return f"{x:,.4f}".rstrip("0").rstrip(".")


def main():
    classes = json.load(open(os.path.join(OUT, "classes.json")))
    dests = list(csv.DictReader(open(os.path.join(OUT, "destinations.csv"))))
    dests.sort(key=lambda r: -float(r["our_total"] or 0))

    grand_usd = sum(c.get("usd_at_exit", 0) for c in classes.values())
    grand_txs = sum(c.get("txs", 0) for c in classes.values())
    self_usd = sum(classes.get(k, {}).get("usd_at_exit", 0)
                   for k in ("SELF_FORWARD", "SELF_SWEEP"))
    third_usd = grand_usd - self_usd

    # top destinations: one row per dest, capped, custodial+merchant+top self
    top = dests[:25]

    class_rows = ""
    for k in CLASS_ORDER:
        c = classes.get(k)
        if not c:
            continue
        pct = (c["usd_at_exit"] / grand_usd * 100) if grand_usd else 0
        byc = c.get("by_coin", {})
        class_rows += f"""
      <tr class="{'self' if k.startswith('SELF') else 'third'}">
        <td>{html.escape(CLASS_LABEL.get(k, k))}<br><code>{k}</code></td>
        <td class="num">{c['txs']:,}</td>
        <td class="num">{coin(c['by_coin'].get('btc', 0))} BTC<br>
            {coin(c['by_coin'].get('ltc', 0))} LTC</td>
        <td class="num">{money(c['usd_at_exit'])}</td>
        <td class="num">{pct:.1f}%</td>
      </tr>"""

    dest_rows = ""
    for r in top:
        fan = r["fan_in"] or "—"
        dest_rows += f"""
      <tr>
        <td class="addr" title="{html.escape(r['address'])}">{html.escape(r['address'][:26])}…</td>
        <td>{r['coin'].upper()}</td>
        <td><code class="cls-{r['class']}">{r['class']}</code></td>
        <td class="num">{r['n_ours']}</td>
        <td class="num">{coin(float(r['our_total']))}</td>
        <td class="num">{money(float(r['our_total_usd_equiv'] or 0))}</td>
        <td class="num">{fan if fan == '—' else f'{float(fan):.2f}'}</td>
        <td class="why">{html.escape(r['why'][:80])}</td>
      </tr>"""

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wallet Outflow Classification — BTC &amp; LTC (8 xpub accounts)</title>
<style>
  :root {{
    --bg:#0d1117; --panel:#161b22; --line:#30363d; --fg:#e6edf3; --dim:#8b949e;
    --self:#3fb950; --third:#f0883e; --acc:#58a6ff;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
         font:14px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif; }}
  header {{ padding:28px 32px 12px; }}
  h1 {{ margin:0 0 4px; font-size:22px; }}
  h2 {{ margin:28px 0 10px; font-size:16px; color:var(--acc); }}
  .sub {{ color:var(--dim); }}
  main {{ padding:0 32px 48px; max-width:1200px; }}
  .cards {{ display:flex; gap:14px; flex-wrap:wrap; margin-top:18px; }}
  .card {{ background:var(--panel); border:1px solid var(--line);
          border-radius:10px; padding:14px 18px; min-width:190px; }}
  .card .k {{ color:var(--dim); font-size:12px; text-transform:uppercase; }}
  .card .v {{ font-size:22px; font-weight:650; margin-top:2px; }}
  .card.self .v {{ color:var(--self); }} .card.third .v {{ color:var(--third); }}
  table {{ width:100%; border-collapse:collapse; background:var(--panel);
          border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
  th, td {{ padding:8px 12px; text-align:left; border-bottom:1px solid var(--line);
           vertical-align:top; }}
  th {{ color:var(--dim); font-size:12px; text-transform:uppercase; }}
  td.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
  tr.self td:first-child {{ border-left:3px solid var(--self); }}
  tr.third td:first-child {{ border-left:3px solid var(--third); }}
  td.addr {{ font-family:ui-monospace, monospace; font-size:12px; }}
  td.why {{ color:var(--dim); font-size:12px; max-width:340px; }}
  code {{ background:#21262d; padding:1px 6px; border-radius:5px; font-size:12px; }}
  .caveats {{ background:var(--panel); border:1px solid var(--line);
             border-left:3px solid var(--third); border-radius:8px;
             padding:12px 16px; color:var(--dim); }}
  .caveats li {{ margin:4px 0; }}
</style>
</head>
<body>
<header>
  <h1>Wallet Outflow Classification — BTC &amp; LTC</h1>
  <div class="sub">8 xpub accounts (4 BTC + 4 LTC, Jan 2022 – Oct 2025) ·
  {grand_txs:,} outgoing payments to {len(dests):,} unique destinations ·
  USD valued at each transaction's day rate (Binance daily open)</div>
</header>
<main>
  <div class="cards">
    <div class="card"><div class="k">Total out (USD at exit)</div>
      <div class="v">{money(grand_usd)}</div></div>
    <div class="card self"><div class="k">Self-moved (SELF_FORWARD + SELF_SWEEP)</div>
      <div class="v">{money(self_usd)}</div></div>
    <div class="card third"><div class="k">3rd-party spend (custodial + vendors + merchant)</div>
      <div class="v">{money(third_usd)}</div></div>
    <div class="card"><div class="k">Classified</div>
      <div class="v">{grand_txs:,} / {grand_txs:,}</div></div>
  </div>

  <h2>Classification totals</h2>
  <table>
    <tr><th>Class</th><th class="num">Payments</th>
        <th class="num">Coin amount</th>
        <th class="num">USD at exit</th><th class="num">Share</th></tr>
    {class_rows}
  </table>

  <h2>Top 25 destinations by value sent</h2>
  <table>
    <tr><th>Address</th><th>Coin</th><th>Class</th><th class="num"># pays</th>
        <th class="num">Total sent</th><th class="num">USD equiv</th>
        <th class="num">fan-in</th><th>Why</th></tr>
    {dest_rows}
  </table>

  <h2>Caveats</h2>
  <ul class="caveats">
    <li>8 heavy BTC destinations carry truncated histories (25 txs, annotated
        <code>history_partial</code>); their funded totals come from
        chain_stats, not the truncated history.</li>
    <li>btc-2024-25: 9 transactions (~0.339 BTC) spend to segwit self-change
        counted as external (segwit-side xpubs never provided) — they classify
        as SELF_FORWARD by shape.</li>
    <li>SELF_FORWARD means "funded ≥98% by us, then forwards onward". Final
        owner of hop-2 targets is unresolvable without the segwit xpubs —
        treat self-moved volume as an upper bound.</li>
    <li>USD values use the transaction day's opening rate; intra-day P&amp;L
        and FIFO matching are out of scope.</li>
  </ul>
</main>
</body>
</html>"""

    p = os.path.join(OUT, "report.html")
    with open(p, "w") as f:
        f.write(page)
    print(f"wrote {p} ({len(page)} bytes)")


if __name__ == "__main__":
    main()
