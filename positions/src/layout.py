"""Google Sheets layout construction — builds section data, no API calls."""

import re
from datetime import datetime

TXN_ROW = 39  # default transaction data start row (used for inconsistent tabs)


def date_to_formula(exp_str):
    """Convert a US-style date to a Sheets DATE() formula.

    Accepts both zero-padded (`05/14/2020`) and unpadded
    (`5/14/2020`) month/day — Robinhood CSV exports use the former
    historically and the latter in newer rows / our merge scripts.
    """
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", exp_str or "")
    if not m:
        return "DATE(2099,1,1)"
    return f"DATE({m.group(3)},{int(m.group(1))},{int(m.group(2))})"


def shorten_symbol(symbol):
    """'NVDA 01/16/2026 150.00 C' → '150C 01/16/26'"""
    m = re.match(r"\S+\s+(\d{2}/\d{2})/(\d{4})\s+([\d.]+)\s+([CP])", symbol)
    if not m:
        return symbol
    strike = m.group(3).rstrip("0").rstrip(".")
    typ = "C" if m.group(4) == "C" else "P"
    return f"{strike}{typ} {m.group(1)}/{m.group(2)[2:]}"


def _short_exp(exp_str):
    """'12/17/2027' → '12/17/27' for compact section headers."""
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", exp_str or "")
    if not m:
        return exp_str or ""
    return f"{m.group(1)}/{m.group(2)}/{m.group(3)[2:]}"


def _sort_by_expiration(positions):
    """Sort open positions by expiration date ascending (earliest first)."""
    def key(pos):
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", pos.get("expiration") or "")
        if m:
            return (int(m.group(3)), int(m.group(1)), int(m.group(2)))
        return (9999, 99, 99)
    return sorted(positions, key=key)


def build_txn_only_sections(last_row):
    """Minimal layout for Inconsistent positions: just the transaction log."""
    return {
        f"A{TXN_ROW-2}:K{TXN_ROW-1}": [
            ["TRANSACTION LOG", "", "", "", "", "", "", "", "", "", ""],
            ["Date", "Action", "Type", "Symbol", "Strike", "Expiration",
             "Qty", "Price", "Fees", "Net Amount", "Notes"],
        ],
    }


# ── Shared row-builder helpers ─────────────────────────────────────────────
# Each returns a 2-D list (rows × cols) for one section of the tab.
# Callers assemble these into the sections dict with the appropriate range key.

def _offsets(show_calls, show_puts, n_calls=1, n_puts=1):
    """Return (p, i, txn_row) — dynamic row starts based on sections present.

    n_calls / n_puts are the count of OPEN CALL / OPEN PUT sections to
    stack (one per unique strike/expiration). Each section is 8 rows
    + 1 gap row = 9 rows tall. When show_calls / show_puts is True but
    no positions exist, we still reserve 1 section's worth of space
    (8+1 rows) for the placeholder header.
    """
    nc = max(n_calls, 1) if show_calls else 0
    npts = max(n_puts, 1) if show_puts else 0
    p = 10 + 9 * nc if show_calls else 10
    i = p + 9 * npts if show_puts else p
    return p, i, i + 9


def _stock_position_rows(T, L):
    # Avg Cost / Share sits at the top of the section (E4) so it can be
    # compared at-a-glance with ** Adj Cost Basis / Share at B4. Shares
    # Held moves down a row to E5. References to E5 below all mean
    # Shares Held in the new layout.
    return [
        ["STOCK POSITION", ""],
        ["Avg Cost / Share",
         f"=IFERROR(-(SUMPRODUCT((C${T}:C${L}=\"Stock\")*(B${T}:B${L}=\"Buy\")*J${T}:J${L})"
         f"+SUMPRODUCT((C${T}:C${L}=\"Stock\")*(B${T}:B${L}=\"Sell\")*J${T}:J${L}))/E5,0)"],
        ["Shares Held",
         f"=SUMPRODUCT((C${T}:C${L}=\"Stock\")*(B${T}:B${L}=\"Buy\")*G${T}:G${L})"
         f"+SUMPRODUCT((C${T}:C${L}=\"Stock\")*(B${T}:B${L}=\"Sell\")*G${T}:G${L})"],
        ["Total Invested", "=E4*E5"],
        ["Market Value", "=E5*B5"],
        ["Position Opened",
         f"=IFERROR(MINIFS(A${T}:A${L},C${T}:C${L},\"Stock\",B${T}:B${L},\"Buy\"),\"\")"],
    ]


def _stock_results_rows(T, L, avg_days_formula):
    return [
        ["STOCK RESULTS", ""],
        ["Gain $",
         f"=IF(E5=0,"
         f"SUMPRODUCT((C${T}:C${L}=\"Stock\")*(B${T}:B${L}=\"Buy\")*J${T}:J${L})"
         f"+SUMPRODUCT((C${T}:C${L}=\"Stock\")*(B${T}:B${L}=\"Sell\")*J${T}:J${L}),"
         f"E7-E6)"],
        ["Gain %",
         f"=IFERROR(-H4/SUMPRODUCT((C${T}:C${L}=\"Stock\")*(B${T}:B${L}=\"Buy\")*J${T}:J${L}),0)"],
        ["Total Days Held",
         f"=IF(COUNTIFS(C${T}:C${L},\"Stock\",B${T}:B${L},\"Buy\")>0,"
         f"DAYS("
         f"IF(COUNTIFS(C${T}:C${L},\"Stock\",B${T}:B${L},\"Sell\")>0,"
         f"MAXIFS(A${T}:A${L},C${T}:C${L},\"Stock\",B${T}:B${L},\"Sell\"),"
         f"TODAY()),"
         f"MINIFS(A${T}:A${L},C${T}:C${L},\"Stock\",B${T}:B${L},\"Buy\")"
         f"),\"\")"],
        ["Avg Days Held", avg_days_formula],
        ["Ann Gain %", "=IFERROR(H5*(365/H7),0)"],
    ]


def _income_rows(T, L, i, p, show_calls, show_puts):
    return [
        ["INCOME", ""],
        ["Total Dividends", f"=SUMPRODUCT((C${T}:C${L}=\"Dividend\")*J${T}:J${L})"],
        ["Dividend Count", f"=COUNTIF(C${T}:C${L},\"Dividend\")"],
        ["Net Call Premium (all time)", "=B13" if show_calls else ""],
        ["Net Put Premium (all time)", f"=B{p+3}" if show_puts else ""],
    ]


def _pnl_rows(i, p, show_calls, show_puts):
    return [
        ["P&L BREAKDOWN", ""],
        ["Stock Gain", "=H4"],
        ["Covered Call Results", "=B15" if show_calls else ""],
        ["Put Results", f"=B{p+5}" if show_puts else ""],
        ["Dividends", f"=B{i+1}"],
        ["Total P&L", f"=E{i+1}+E{i+2}+E{i+3}+E{i+4}"],
    ]


def _call_history_rows(T, L):
    return [
        ["CALL HISTORY STATS", ""],
        ["Call Premium Received",
         f"=SUMPRODUCT((C${T}:C${L}=\"Call\")*(J${T}:J${L}>0)*J${T}:J${L})"],
        ["Call Premium Paid",
         f"=SUMPRODUCT((C${T}:C${L}=\"Call\")*(J${T}:J${L}<0)*J${T}:J${L})"],
        ["Net Call Premium (all time)",
         f"=SUMPRODUCT((C${T}:C${L}=\"Call\")*J${T}:J${L})"],
        ["Calls Market Value", "=B7"],
        ["Covered Call Results", "=B13+B14"],
    ]


def _put_history_rows(T, L, p):
    return [
        ["PUT HISTORY STATS", ""],
        ["Put Premium Received",
         f"=SUMPRODUCT((C${T}:C${L}=\"Put\")*(J${T}:J${L}>0)*J${T}:J${L})"],
        ["Put Premium Paid",
         f"=SUMPRODUCT((C${T}:C${L}=\"Put\")*(J${T}:J${L}<0)*J${T}:J${L})"],
        ["Net Put Premium (all time)",
         f"=SUMPRODUCT((C${T}:C${L}=\"Put\")*J${T}:J${L})"],
        ["Puts Market Value", "=B8"],
        ["Put Results", f"=B{p+3}+B{p+4}"],
    ]


# ── Per-call / per-put section helpers ───────────────────────────────────────
#
# Each open call (or put) — meaning a unique strike/expiration combo —
# gets its own 8-row OPEN section pair (D:E for the position info, G:H
# for the metrics). Sections stack vertically starting at row 10 for
# calls, then puts. `base` is the section's first row (the header row).

def _open_call_rows(call):
    strike = call["strike"]
    exp = call["expiration"]
    df = date_to_formula(exp)
    cts = -call["contracts"]
    open_date = call.get("open_date") or ""
    od_f = date_to_formula(open_date) if open_date else ""
    od_cell = f"={od_f}" if od_f else ""
    days_open = f"=TODAY()-{od_f}" if od_f else ""
    price_at_open = call.get("price_at_open") or ""
    header = f"OPEN CALL — ${strike} {_short_exp(exp)}"
    return [
        [header, ""],
        ["Strike", strike],
        ["Expiration", exp],
        ["Date Opened", od_cell],
        ["Days Open", days_open],
        ["Stock Price at Open", price_at_open],
        ["Days Left", f"=DAYS({df},TODAY())"],
        ["Contracts", cts],
    ]


def _open_call_metrics_rows(call, base):
    """Per-call metrics; formulas reference cells inside this section
    (H{base+1}..H{base+7}, E{base+6}, E{base+7}) plus the global Stock
    Price at B5. Cost to Close is the per-call market value (static
    value populated by setup_tab) so each call's TV Ann Yield uses
    only its own cost — not the aggregate of all open calls.
    """
    strike = call["strike"]
    prem = round(call["premium"], 2)
    mv = call.get("market_value")
    if mv is None:
        mv = ""
    return [
        ["OPEN CALL METRICS", ""],
        ["Premium Received", prem],
        ["Cost to Close", mv],
        ["Unrealized P&L", f"=H{base+1}+H{base+2}"],
        ["Status", f"=IF(B5>{strike},\"ITM\",\"OTM\")"],
        ["Intrinsic Value", f"=MAX(0,B5-{strike})*E{base+7}*100"],
        ["Time Value", f"=H{base+2}-H{base+5}"],
        ["** TV Ann Yield",
         f"=IFERROR(MAX(0,-H{base+6})/"
         f"(-E{base+7}*100*B5+H{base+2})*(365/E{base+6}),0)"],
    ]


def _empty_call_rows():
    return [["OPEN CALLS", ""]] + [["", ""]] * 7


def _empty_call_metrics_rows():
    return [["OPEN CALL METRICS", ""]] + [["", ""]] * 7


def _open_put_rows(put):
    strike = put["strike"]
    exp = put["expiration"]
    df = date_to_formula(exp)
    cts = -put["contracts"]
    open_date = put.get("open_date") or ""
    od_f = date_to_formula(open_date) if open_date else ""
    od_cell = f"={od_f}" if od_f else ""
    days_open = f"=TODAY()-{od_f}" if od_f else ""
    price_at_open = put.get("price_at_open") or ""
    header = f"OPEN PUT — ${strike} {_short_exp(exp)}"
    return [
        [header, ""],
        ["Strike", strike],
        ["Expiration", exp],
        ["Date Opened", od_cell],
        ["Days Open", days_open],
        ["Stock Price at Open", price_at_open],
        ["Days Left", f"=DAYS({df},TODAY())"],
        ["Contracts", cts],
    ]


def _open_put_metrics_rows(put, base):
    strike = put["strike"]
    prem = round(put["premium"], 2)
    mv = put.get("market_value")
    if mv is None:
        mv = ""
    return [
        ["OPEN PUT METRICS", ""],
        ["Premium Received", prem],
        ["Cost to Close", mv],
        ["Unrealized P&L", f"=H{base+1}+H{base+2}"],
        ["Status", f"=IF(B5<{strike},\"ITM\",\"OTM\")"],
        ["Intrinsic Value", f"=MAX(0,{strike}-B5)*E{base+7}*100"],
        ["Time Value", f"=H{base+2}-H{base+5}"],
        ["TV Ann Yield",
         f"=IFERROR(IF(E{base+6}>0,"
         f"MAX(0,-H{base+6})/(-E{base+7}*100*{strike})*(365/E{base+6}),0),0)"],
    ]


def _empty_put_rows():
    return [["OPEN PUTS", ""]] + [["", ""]] * 7


def _empty_put_metrics_rows():
    return [["OPEN PUT METRICS", ""]] + [["", ""]] * 7


# ── Public layout builders ─────────────────────────────────────────────────

def build_open_sections(ticker, open_positions, last_row, avg_held_anchor=None,
                        brokerage="", show_calls=True, show_puts=True):
    """Build position tab sections for an open (Consistent) position.

    Each open call (unique strike/expiration combo) gets its own
    OPEN CALL + OPEN CALL METRICS section pair, stacked vertically
    starting at row 10. Same for puts at row p (computed from how
    much vertical space the call sections consume).
    """
    L = last_row

    open_calls     = _sort_by_expiration(
        [pos for pos in open_positions if pos["type"] == "Call"])
    open_puts_list = _sort_by_expiration(
        [pos for pos in open_positions if pos["type"] == "Put"])

    n_calls = len(open_calls)
    n_puts  = len(open_puts_list)

    p, i, txn_row = _offsets(show_calls, show_puts,
                             n_calls=n_calls, n_puts=n_puts)
    T = txn_row

    if avg_held_anchor:
        y, mo, d = avg_held_anchor
        avg_days_formula = f"=TODAY()-DATE({y},{mo},{d})"
    else:
        avg_days_formula = "0"

    sections = {
        "A1:C1": [[ticker, "Status", "Consistent"]],

        "A3:B8": [
            ["CURRENT VALUES", ""],
            ["** Adj Cost Basis / Share", f"=IFERROR(-SUM(J${T}:J${L})/E5,0)"],
            ["Stock Price", ""],
            ["Last Updated", datetime.now().strftime("%m/%d/%y %H:%M")],
            ["Calls Market Value", ""],
            ["Puts Market Value", ""],
        ],

        "D3:E8": _stock_position_rows(T, L),
        "G3:H8": _stock_results_rows(T, L, avg_days_formula),

        f"A{i}:B{i+4}": _income_rows(T, L, i, p, show_calls, show_puts),
        f"D{i}:E{i+5}": _pnl_rows(i, p, show_calls, show_puts),

        f"G{i}:H{i+5}": [
            ["RETURNS", ""],
            ["Amount Invested",
             f"=-SUMPRODUCT((C${T}:C${L}=\"Stock\")*(B${T}:B${L}=\"Buy\")*J${T}:J${L})"],
            ["Close-out Value", "=E7+B7+B8"],
            ["Total Income", f"=E{i+5}-E{i+1}"],
            ["Ann Yield on Invested Capital",
             f"=IFERROR(E{i+5}/E6*(365/H7),0)"],
            ["Ann Yield on Close-out Value",
             f"=IFERROR(E{i+5}/H{i+2}*(365/H7),0)"],
        ],

        f"A{txn_row-2}:K{txn_row-1}": [
            ["TRANSACTION LOG", "", "", "", "", "", "", "", "", "", ""],
            ["Date", "Action", "Type", "Symbol", "Strike", "Expiration",
             "Qty", "Price", "Fees", "Net Amount", "Notes"],
        ],
    }

    if show_calls:
        # Call history (aggregate stats) stays at A10:B15. With multiple
        # open call sections, the A:B column area below A15 is left blank.
        sections["A10:B15"] = _call_history_rows(T, L)
        if open_calls:
            for idx, call in enumerate(open_calls):
                base = 10 + 9 * idx
                sections[f"D{base}:E{base+7}"] = _open_call_rows(call)
                sections[f"G{base}:H{base+7}"] = _open_call_metrics_rows(call, base)
        else:
            sections["D10:E17"] = _empty_call_rows()
            sections["G10:H17"] = _empty_call_metrics_rows()

    if show_puts:
        sections[f"A{p}:B{p+5}"] = _put_history_rows(T, L, p)
        if open_puts_list:
            for idx, put in enumerate(open_puts_list):
                base = p + 9 * idx
                sections[f"D{base}:E{base+7}"] = _open_put_rows(put)
                sections[f"G{base}:H{base+7}"] = _open_put_metrics_rows(put, base)
        else:
            sections[f"D{p}:E{p+7}"] = _empty_put_rows()
            sections[f"G{p}:H{p+7}"] = _empty_put_metrics_rows()

    return sections


def build_closed_sections(ticker, open_positions, last_row,
                           brokerage="", closed_avg_days=None,
                           show_calls=True, show_puts=True,
                           last_call=None, last_put=None):
    """Build position tab sections for a closed position."""
    L = last_row
    p, i, txn_row = _offsets(show_calls, show_puts)
    T = txn_row

    avg_days_formula = str(closed_avg_days) if closed_avg_days is not None else "0"

    sections = {
        "A1:C1": [[ticker, "Status", "Closed"]],

        "A3:B6": [
            ["CURRENT VALUES", ""],
            ["** Adj Cost Basis / Share", f"=IFERROR(-SUM(J${T}:J${L})/E5,0)"],
            ["Stock Price", ""],
            ["Last Updated", datetime.now().strftime("%m/%d/%y %H:%M")],
        ],

        "D3:E8": _stock_position_rows(T, L),
        "G3:H8": _stock_results_rows(T, L, avg_days_formula),

        f"A{i}:B{i+4}": _income_rows(T, L, i, p, show_calls, show_puts),
        f"D{i}:E{i+5}": _pnl_rows(i, p, show_calls, show_puts),

        f"G{i}:H{i+5}": [
            ["RETURNS", ""],
            ["Amount Invested",
             f"=-SUMPRODUCT((C${T}:C${L}=\"Stock\")*(B${T}:B${L}=\"Buy\")*J${T}:J${L})"],
            ["Close-out Value",
             f"=SUMPRODUCT((C${T}:C${L}=\"Stock\")*(B${T}:B${L}=\"Sell\")*J${T}:J${L})"],
            ["Total Income", f"=E{i+5}-E{i+1}"],
            ["Ann Yield on Invested Capital",
             f"=IFERROR(E{i+5}/H{i+1}*(365/H7),0)"],
            ["Ann Yield on Close-out Value",
             f"=IFERROR(E{i+5}/H{i+2}*(365/H7),0)"],
        ],

        f"A{txn_row-2}:K{txn_row-1}": [
            ["TRANSACTION LOG", "", "", "", "", "", "", "", "", "", ""],
            ["Date", "Action", "Type", "Symbol", "Strike", "Expiration",
             "Qty", "Price", "Fees", "Net Amount", "Notes"],
        ],
    }

    if show_calls:
        lc = last_call or {}
        lc_open_date       = lc.get("open_date", "")
        lc_open_date_f     = date_to_formula(lc_open_date) if lc_open_date else ""
        lc_open_date_cell  = f"={lc_open_date_f}" if lc_open_date_f else ""
        lc_close_date      = lc.get("close_date", "")
        lc_close_date_f    = date_to_formula(lc_close_date) if lc_close_date else ""
        lc_close_date_cell = f"={lc_close_date_f}" if lc_close_date_f else ""
        lc_days_open       = lc.get("days_open") if lc.get("days_open") is not None else ""
        lc_cts             = -lc["contracts"] if lc.get("contracts") else ""
        sections.update({
            "A10:B15": _call_history_rows(T, L),
            "D10:E18": [
                ["LAST CALL", ""],
                ["Strike",               lc.get("strike", "")],
                ["Expiration",           lc.get("expiration", "")],
                ["Date Opened",          lc_open_date_cell],
                ["Date Closed",          lc_close_date_cell],
                ["Days Open",            lc_days_open],
                ["Stock Price at Open",  lc.get("price_at_open", "") or ""],
                ["Stock Price at Close", lc.get("price_at_close", "") or ""],
                ["Contracts",            lc_cts],
            ],
            "G10:H18": [
                ["LAST CALL METRICS", ""],
                ["Premium Received",  lc.get("premium", "")],
                ["Cost to Close",     ""],
                ["Unrealized P&L",    ""],
                ["Status at Close",
                 "ITM" if lc.get("itm_at_close") is True
                 else "OTM" if lc.get("itm_at_close") is False else ""],
                ["Closed By",         lc.get("disposition", "")],
                ["Missed Upside",
                 f"=MAX(0,B5-{lc['strike']})*{lc['contracts']}*100"
                 if lc.get("disposition") == "Assigned" and lc.get("strike") and lc.get("contracts")
                 else ""],
                ["", ""],
                ["", ""],
            ],
        })

    if show_puts:
        lp = last_put or {}
        lp_open_date       = lp.get("open_date", "")
        lp_open_date_f     = date_to_formula(lp_open_date) if lp_open_date else ""
        lp_open_date_cell  = f"={lp_open_date_f}" if lp_open_date_f else ""
        lp_close_date      = lp.get("close_date", "")
        lp_close_date_f    = date_to_formula(lp_close_date) if lp_close_date else ""
        lp_close_date_cell = f"={lp_close_date_f}" if lp_close_date_f else ""
        lp_days_open       = lp.get("days_open") if lp.get("days_open") is not None else ""
        lp_cts             = -lp["contracts"] if lp.get("contracts") else ""
        sections.update({
            f"A{p}:B{p+5}": _put_history_rows(T, L, p),
            f"D{p}:E{p+8}": [
                ["LAST PUT", ""],
                ["Strike",               lp.get("strike", "")],
                ["Expiration",           lp.get("expiration", "")],
                ["Date Opened",          lp_open_date_cell],
                ["Date Closed",          lp_close_date_cell],
                ["Days Open",            lp_days_open],
                ["Stock Price at Open",  lp.get("price_at_open", "") or ""],
                ["Stock Price at Close", lp.get("price_at_close", "") or ""],
                ["Contracts",            lp_cts],
            ],
            f"G{p}:H{p+8}": [
                ["LAST PUT METRICS", ""],
                ["Premium Received",  lp.get("premium", "")],
                ["Cost to Close",     ""],
                ["Unrealized P&L",    ""],
                ["Status at Close",
                 "ITM" if lp.get("itm_at_close") is True
                 else "OTM" if lp.get("itm_at_close") is False else ""],
                ["Closed By",         lp.get("disposition", "")],
                ["Assignment Loss",
                 f"=MAX(0,{lp['strike']}-B5)*{lp['contracts']}*100"
                 if lp.get("disposition") == "Assigned" and lp.get("strike") and lp.get("contracts")
                 else ""],
                ["", ""],
                ["", ""],
            ],
        })

    return sections
