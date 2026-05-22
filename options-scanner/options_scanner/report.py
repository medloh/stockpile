"""Generate self-contained HTML reports (single ticker and portfolio)."""

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui, sans-serif; background: #f0f3f7;
       color: #222; padding: 1.5em; }
h1 { font-size: 1.4em; font-weight: 700; }
h2 { font-size: 1.05em; font-weight: 600; color: #444;
     margin: 1.8em 0 0.5em; text-transform: uppercase;
     letter-spacing: .05em; }
h3 { font-size: 1em; font-weight: 600; color: #333; margin-bottom: .4em; }
.card { background: #fff; border-radius: 8px;
        box-shadow: 0 1px 4px rgba(0,0,0,.1); padding: 1.2em 1.5em;
        margin-bottom: 1.2em; }
.meta { display: flex; flex-wrap: wrap; gap: 1.2em;
        margin-top: .6em; font-size: .9em; color: #555; }
.meta strong { color: #222; }
.tag-earn { color: #b94; font-weight: 600; }
.tag-sell { color: #2980b9; font-weight: 600; }
.tag-buy  { color: #27ae60; font-weight: 600; }
.roll-note { font-size: .85em; color: #666; margin-bottom: .5em; }
table { width: 100%; border-collapse: collapse; font-size: .875em; }
thead th { background: #2c3e50; color: #fff; padding: .55em .9em;
           text-align: right; white-space: nowrap; cursor: pointer;
           user-select: none; position: sticky; top: 0; }
thead th:first-child { text-align: left; }
thead th.sort-asc::after  { content: " \25b2"; font-size: .7em; }
thead th.sort-desc::after { content: " \25bc"; font-size: .7em; }
tbody tr:nth-child(even) { background: #f7f9fb; }
tbody tr:hover { background: #edf3fa; }
td { padding: .45em .9em; text-align: right; border-bottom: 1px solid #eee; }
td:first-child { text-align: left; font-weight: 600; }
.row-count { font-size: .8em; color: #888; margin-top: .4em; }
/* IV+pp colours — sell mode */
.iv-s3 { color: #c0392b; font-weight: 700; }
.iv-s2 { color: #e67e22; font-weight: 600; }
.iv-s1 { color: #e8a020; }
.iv-s0 { color: #aaa; }
/* IV+pp colours — buy mode */
.iv-b3 { color: #27ae60; font-weight: 700; }
.iv-b2 { color: #2980b9; font-weight: 600; }
.iv-b1 { color: #5dade2; }
.iv-b0 { color: #aaa; }
.guide { font-size: .875em; }
.guide dt { font-weight: 600; margin-top: .8em; }
.guide dd { margin-left: 1.4em; color: #555; line-height: 1.5; }
/* Portfolio-specific */
.badge { display: inline-block; padding: .15em .55em; border-radius: 10px;
         font-size: .8em; font-weight: 600; margin-left: .5em;
         vertical-align: middle; }
.badge-covered   { background: #d5f5e3; color: #1e8449; }
.badge-uncovered { background: #ebf5fb; color: #1a5276; }
.open-call-note { background: #fef9e7; border-left: 3px solid #f39c12;
                  padding: .5em 1em; margin-bottom: .8em; font-size: .85em; }
.position-block { border-top: 2px solid #dde3ea; padding-top: .8em;
                  margin-top: 1.6em; }
.summary-table thead th { background: #4a4a4a; }
.summary-table tbody td { font-weight: normal; }
.nav-link { font-size: .8em; color: #888; text-decoration: none;
            float: right; margin-top: .2em; }
.nav-link:hover { color: #444; }
"""

_JS = """
function sortTable(id, col, numeric) {
  var tbl = document.getElementById(id);
  var asc = tbl.dataset.sortCol != col ||
            tbl.dataset.sortDir == 'desc';
  var rows = Array.from(tbl.querySelectorAll('tbody tr'));
  rows.sort(function(a, b) {
    var av = a.cells[col].dataset.val || a.cells[col].textContent.trim();
    var bv = b.cells[col].dataset.val || b.cells[col].textContent.trim();
    if (numeric) {
      av = parseFloat(av) || 0; bv = parseFloat(bv) || 0;
      return asc ? av - bv : bv - av;
    }
    return asc ? av.localeCompare(bv) : bv.localeCompare(av);
  });
  var tb = tbl.querySelector('tbody');
  rows.forEach(function(r) { tb.appendChild(r); });
  tbl.dataset.sortCol = col;
  tbl.dataset.sortDir = asc ? 'asc' : 'desc';
  tbl.querySelectorAll('th').forEach(function(th) {
    th.classList.remove('sort-asc', 'sort-desc');
  });
  tbl.querySelectorAll('th')[col].classList.add(asc ? 'sort-asc' : 'sort-desc');
}
"""


def _iv_class(excess_pct: float, buy: bool) -> str:
    if buy:
        if excess_pct <= -5:  return "iv-b3"
        if excess_pct <= -3:  return "iv-b2"
        if excess_pct < 0:    return "iv-b1"
        return "iv-b0"
    else:
        if excess_pct >= 5:   return "iv-s3"
        if excess_pct >= 3:   return "iv-s2"
        if excess_pct >= 1:   return "iv-s1"
        return "iv-s0"


def _fmt_exp(exp_str: str) -> str:
    return datetime.strptime(exp_str, "%Y-%m-%d").strftime("%b %d '%y")


def _col_headers(has_net_credit: bool) -> list[tuple[str, bool]]:
    cols = [
        ("Strike", True), ("Expiration", False), ("DTE", True),
        ("Bid", True), ("Ask", True), ("Mid", True),
        ("IV%", True), ("IV+pp", True), ("Delta", True),
        ("Ann%", True), ("OI", True),
    ]
    if has_net_credit:
        cols.append(("NetCr", True))
    return cols


def _build_table(sub, table_id: str, buy: bool,
                 roll_close_cost: float | None) -> str:
    has_net = roll_close_cost is not None
    cols = _col_headers(has_net)

    ths = "".join(
        f'<th onclick="sortTable(\'{table_id}\',{i},{"true" if num else "false"})">'
        f"{label}</th>"
        for i, (label, num) in enumerate(cols)
    )

    rows_html = []
    for _, r in sub.iterrows():
        earn_tag = (f' <span class="tag-earn">{int(r["earnings_count"])}E</span>'
                    if r["earnings_count"] > 0 else "")
        iv_ex = r["iv_excess"] * 100
        net_cr = r["mid"] - roll_close_cost if has_net else None

        cells = [
            f'<td data-val="{r["strike"]:.2f}">${r["strike"]:.0f}</td>',
            f'<td data-val="{r["expiration"]}">{_fmt_exp(r["expiration"])}{earn_tag}</td>',
            f'<td data-val="{r["dte"]}">{int(r["dte"])}</td>',
            f'<td data-val="{r["bid"]:.4f}">${r["bid"]:.2f}</td>',
            f'<td data-val="{r["ask"]:.4f}">${r["ask"]:.2f}</td>',
            f'<td data-val="{r["mid"]:.4f}">${r["mid"]:.2f}</td>',
            f'<td data-val="{r["iv"]*100:.4f}">{r["iv"]*100:.1f}</td>',
            f'<td data-val="{iv_ex:.4f}" class="{_iv_class(iv_ex, buy)}">{iv_ex:+.1f}</td>',
            f'<td data-val="{r["delta"]:.4f}">{r["delta"]:.2f}</td>',
            f'<td data-val="{r["ann_yield_pct"]:.4f}">{r["ann_yield_pct"]:.1f}</td>',
            f'<td data-val="{r["open_interest"]}">{r["open_interest"]:,}</td>',
        ]
        if has_net:
            cells.append(f'<td data-val="{net_cr:.4f}">${net_cr:+.2f}</td>')
        rows_html.append(f"<tr>{''.join(cells)}</tr>")

    return (
        f'<table id="{table_id}" data-sort-col="-1">'
        f"<thead><tr>{ths}</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody>"
        f"</table>"
        f'<p class="row-count">{len(sub)} options shown</p>'
    )


def _guide_html(buy: bool) -> str:
    if buy:
        return """
<dl class="guide">
  <dt>IV+pp</dt>
  <dd>Negative = option's IV sits below the fitted surface (IV-cheap
  relative to neighbors). Under &minus;3pp: meaningful ranking
  signal. Under &minus;5pp: strong.</dd>
  <dt>Delta</dt>
  <dd>Probability the option expires in the money (profitable).
  Higher delta = more likely to profit, but costs more.</dd>
  <dt>Ann%</dt>
  <dd>Annualized cost as % of underlying. Lower = cheaper.</dd>
</dl>"""
    else:
        return """
<dl class="guide">
  <dt>IV+pp</dt>
  <dd>How many percentage points above the fitted surface. Higher =
  IV-rich relative to neighbors. Under 3pp: chain's IV is roughly
  uniform, no strike stands out. Over 5pp: stronger signal.</dd>
  <dt>Delta</dt>
  <dd>~Probability of expiring ITM (assignment). Lower = safer,
  less premium.</dd>
  <dt>Ann%</dt>
  <dd>Annualized yield. Calls: vs. spot. Puts: vs. strike.</dd>
  <dt>NetCr (roll mode)</dt>
  <dd>New mid minus close cost. Positive = net credit roll.</dd>
  <dt>LT capital gains</dt>
  <dd>Close after the LT date shown to get long-term rates on premium.</dd>
</dl>"""


# ── Single-ticker report ─────────────────────────────────────────────────────

def render_html(
    df,
    ticker: str,
    spot: float,
    earnings_dates: list,
    mode: str,
    buy: bool,
    roll_close_cost: float | None,
    min_oi: int,
    min_vol: int = 0,
) -> str:
    """Return the single-ticker HTML report as a string."""
    today = date.today()
    lt_date = (today + timedelta(days=366)).strftime("%b %d '%y")
    action_label = "Buy" if buy else "Sell"
    type_label = {"call": "Calls", "put": "Puts"}.get(mode, "Calls & Puts")
    scan_date = today.strftime("%B %d, %Y")

    df = df[(df["open_interest"] >= min_oi)
            & (df["volume"] >= min_vol)].copy()

    earn_html = ""
    if earnings_dates:
        earn_strs = [d.strftime("%b %d") for d in earnings_dates[:4]]
        earn_html = (f'<span class="tag-earn">&#9656; Earnings: '
                     f'{", ".join(earn_strs)}</span>')

    action_cls = "tag-buy" if buy else "tag-sell"
    type_labels = {"call": "Calls", "put": "Puts"}
    to_show = [mode] if mode in type_labels else list(type_labels.keys())
    iv_asc = buy

    sections_html = ""
    for i, opt_type in enumerate(to_show):
        label = type_labels[opt_type]
        sub = (df[df["type"] == opt_type]
               .sort_values(["iv_excess", "open_interest"], ascending=[iv_asc, False]))
        if sub.empty:
            continue
        table_id = f"tbl-{opt_type}-{i}"
        roll_note = ""
        if roll_close_cost is not None:
            roll_note = (f'<p class="roll-note">Close cost (mid): '
                         f'<strong>${roll_close_cost:.2f}</strong> &mdash; '
                         f"NetCr = new mid &minus; this</p>")
        sections_html += (
            f"<h2>{label}</h2>{roll_note}"
            f'<div class="card" style="padding:0;overflow:auto">'
            f"{_build_table(sub, table_id, buy, roll_close_cost)}"
            f"</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{ticker} {action_label} {type_label} &mdash; {scan_date}</title>
  <style>{_CSS}</style>
</head>
<body>
<div class="card">
  <h1>{ticker} &mdash; <span class="{action_cls}">{action_label} {type_label}</span></h1>
  <div class="meta">
    <span>Spot: <strong>${spot:.2f}</strong></span>
    <span>Scanned: {scan_date}</span>
    <span>LT close if opened today: <strong>{lt_date}</strong></span>
    {earn_html}
  </div>
</div>
{sections_html}
<div class="card">
  <h2 style="margin-top:0">How to read this report</h2>
  {_guide_html(buy)}
</div>
<script>{_JS}</script>
</body>
</html>"""


def save_html(
    df,
    ticker: str,
    spot: float,
    earnings_dates: list,
    mode: str,
    buy: bool,
    roll_close_cost: float | None,
    min_oi: int,
    output_path: Path,
) -> None:
    html = render_html(df, ticker, spot, earnings_dates, mode, buy, roll_close_cost, min_oi)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    log.info("HTML report saved: %s", output_path)


# ── Portfolio report ─────────────────────────────────────────────────────────

def render_portfolio_html(
    results: list[dict],
    csv_name: str,
    min_oi: int,
    top_n: int,
    min_vol: int = 0,
) -> str:
    """Return a combined portfolio HTML report as a string."""
    today = date.today()
    scan_date = today.strftime("%B %d, %Y")
    lt_date = (today + timedelta(days=366)).strftime("%b %d '%y")

    # Summary table rows
    summary_rows = ""
    for res in results:
        pos = res["position"]
        ticker = pos["ticker"]
        spot = res["spot"]
        spot_str = f"${spot:.2f}" if spot else "—"
        if res["error"]:
            status_html = '<span style="color:#c0392b">Error</span>'
            calls_str = res["error"]
        elif pos["open_calls"]:
            status_html = '<span class="badge badge-covered">Covered</span>'
            calls_str = "; ".join(
                f'{o["strike"]} {o["expiration"]} ({o["contracts"]}x)'
                for o in pos["open_calls"]
            )
        else:
            status_html = '<span class="badge badge-uncovered">Uncovered</span>'
            calls_str = "—"
        summary_rows += (
            f'<tr><td><a href="#{ticker}">{ticker}</a></td>'
            f"<td>{pos['shares']}</td><td>{spot_str}</td>"
            f"<td>{status_html}</td><td>{calls_str}</td></tr>"
        )

    # Position sections
    sections_html = ""
    tbl_counter = 0
    for res in results:
        pos = res["position"]
        ticker = pos["ticker"]
        covered = bool(pos["open_calls"])
        badge = ('<span class="badge badge-covered">Covered</span>'
                 if covered else
                 '<span class="badge badge-uncovered">Uncovered</span>')

        open_calls_html = ""
        for opt in pos["open_calls"]:
            close = res["roll_close_costs"].get(opt["symbol"])
            close_str = f" &mdash; close mid: <strong>${close:.2f}</strong>" if close else ""
            open_calls_html += (
                f'<p class="open-call-note">Open call: <strong>{opt["symbol"]}</strong> '
                f'&mdash; {opt["contracts"]} contract(s){close_str}</p>'
            )

        if res["error"] or res["spot"] is None or res["df"].empty:
            msg = res["error"] or "No options data returned."
            body_html = f'<p style="color:#c0392b">{msg}</p>'
        else:
            spot = res["spot"]
            earnings_dates = res["earnings_dates"]
            df = res["df"][(res["df"]["open_interest"] >= min_oi)
                           & (res["df"]["volume"] >= min_vol)].copy()

            earn_str = ""
            if earnings_dates:
                earn_str = (f' &nbsp;|&nbsp; <span class="tag-earn">Earnings: '
                            f'{", ".join(d.strftime("%b %d") for d in earnings_dates[:4])}</span>')

            roll_close = None
            if pos["open_calls"]:
                first = pos["open_calls"][0]
                roll_close = res["roll_close_costs"].get(first["symbol"])
                if len(pos["open_calls"]) > 1:
                    open_calls_html += (
                        '<p class="roll-note">Multiple open calls — '
                        "showing roll vs. first position above.</p>"
                    )

            sub = (df[df["type"] == "call"]
                   .sort_values(["iv_excess", "open_interest"], ascending=[False, False])
                   .head(top_n))

            roll_note = ""
            if roll_close is not None:
                roll_note = (f'<p class="roll-note">Close cost (mid): '
                             f'<strong>${roll_close:.2f}</strong> &mdash; '
                             f"NetCr = new mid &minus; this</p>")

            table_html = ""
            if not sub.empty:
                tbl_id = f"tbl-port-{tbl_counter}"
                tbl_counter += 1
                table_html = (
                    f"{roll_note}"
                    f'<div style="overflow:auto">'
                    f"{_build_table(sub, tbl_id, False, roll_close)}"
                    f"</div>"
                )
            else:
                table_html = "<p>No options found matching filters.</p>"

            body_html = (
                f'<p class="meta">Spot: <strong>${spot:.2f}</strong>'
                f"{earn_str}</p>"
                f'<div style="margin:.6em 0">{open_calls_html}</div>'
                f"{table_html}"
            )

        sections_html += (
            f'<div class="position-block" id="{ticker}">'
            f'<a class="nav-link" href="#top">&#8593; top</a>'
            f"<h3>{ticker} &mdash; {pos['shares']} shares {badge}</h3>"
            f'<div class="card" style="padding:1em 1.5em">{body_html}</div>'
            f"</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Portfolio Scan &mdash; {scan_date}</title>
  <style>{_CSS}</style>
</head>
<body id="top">
<div class="card">
  <h1>Portfolio Scan</h1>
  <div class="meta">
    <span>Source: <strong>{csv_name}</strong></span>
    <span>Scanned: {scan_date}</span>
    <span>LT close if opened today: <strong>{lt_date}</strong></span>
  </div>
</div>
<div class="card" style="padding:0;overflow:auto">
  <table class="summary-table" id="tbl-summary" data-sort-col="-1">
    <thead><tr>
      <th onclick="sortTable('tbl-summary',0,false)">Ticker</th>
      <th onclick="sortTable('tbl-summary',1,true)">Shares</th>
      <th onclick="sortTable('tbl-summary',2,true)">Spot</th>
      <th>Status</th>
      <th>Open Calls</th>
    </tr></thead>
    <tbody>{summary_rows}</tbody>
  </table>
</div>
{sections_html}
<div class="card">
  <h2 style="margin-top:0">How to read this report</h2>
  {_guide_html(False)}
</div>
<script>{_JS}</script>
</body>
</html>"""


def save_portfolio_html(
    results: list[dict],
    csv_name: str,
    output_path: Path,
    min_oi: int,
    top_n: int,
    min_vol: int = 0,
) -> None:
    html = render_portfolio_html(results, csv_name, min_oi, top_n, min_vol)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    log.info("Portfolio HTML report saved: %s", output_path)
