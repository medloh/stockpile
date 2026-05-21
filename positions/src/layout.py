"""Google Sheets layout construction — builds section data, no API calls."""

import re
from datetime import datetime

TXN_ROW = 39  # default transaction data start row (used for inconsistent tabs)


def date_to_formula(exp_str):
    """'MM/DD/YYYY' → 'DATE(YYYY,MM,DD)'"""
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", exp_str or "")
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

def _offsets(show_calls, show_puts):
    """Return (p, i, txn_row) — dynamic row starts based on sections present."""
    p = 19 if show_calls else 10
    i = (p + 9) if show_puts else (19 if show_calls else 10)
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


# ── Public layout builders ─────────────────────────────────────────────────

def build_open_sections(ticker, open_positions, last_row, avg_held_anchor=None,
                        brokerage="", show_calls=True, show_puts=True):
    """Build position tab sections for an open (Consistent) position."""
    L = last_row
    p, i, txn_row = _offsets(show_calls, show_puts)
    T = txn_row

    if avg_held_anchor:
        y, mo, d = avg_held_anchor
        avg_days_formula = f"=TODAY()-DATE({y},{mo},{d})"
    else:
        avg_days_formula = "0"

    open_calls     = [pos for pos in open_positions if pos["type"] == "Call"]
    open_puts_list = [pos for pos in open_positions if pos["type"] == "Put"]

    itm = open_calls[0] if open_calls else None
    if itm:
        itm_strike        = itm["strike"]
        oc_strike         = itm["strike"]
        oc_exp            = itm["expiration"]
        oc_cts            = -itm["contracts"]
        oc_prem           = round(itm["premium"], 2)
        oc_df             = date_to_formula(itm["expiration"])
        oc_status         = f"=IF(B5>{itm_strike},\"ITM\",\"OTM\")"
        oc_open_date      = itm.get("open_date", "") or ""
        oc_open_date_f    = date_to_formula(oc_open_date) if oc_open_date else ""
        oc_open_date_cell = f"={oc_open_date_f}" if oc_open_date_f else ""
        oc_days_open      = f"=TODAY()-{oc_open_date_f}" if oc_open_date_f else ""
        oc_price_at_open  = itm.get("price_at_open", "") or ""
    else:
        itm_strike        = ""
        oc_strike         = oc_exp = oc_cts = oc_prem = ""
        oc_df             = "DATE(2099,1,1)"
        oc_status         = ""
        oc_open_date      = oc_open_date_f = oc_open_date_cell = ""
        oc_days_open      = oc_price_at_open = ""

    op = open_puts_list[0] if open_puts_list else None
    if op:
        op_strike         = op["strike"]
        op_exp            = op["expiration"]
        op_cts            = -op["contracts"]
        op_prem           = round(op["premium"], 2)
        op_df             = date_to_formula(op["expiration"])
        op_status         = f"=IF(B5<{op_strike},\"ITM\",\"OTM\")"
        op_open_date      = op.get("open_date", "") or ""
        op_open_date_f    = date_to_formula(op_open_date) if op_open_date else ""
        op_open_date_cell = f"={op_open_date_f}" if op_open_date_f else ""
        op_days_open      = f"=TODAY()-{op_open_date_f}" if op_open_date_f else ""
        op_price_at_open  = op.get("price_at_open", "") or ""
    else:
        op_strike         = op_exp = op_cts = op_prem = ""
        op_df             = "DATE(2099,1,1)"
        op_status         = ""
        op_open_date      = op_open_date_f = op_open_date_cell = ""
        op_days_open      = op_price_at_open = ""

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
        sections.update({
            "A10:B15": _call_history_rows(T, L),
            "D10:E17": [
                ["OPEN CALLS", ""],
                ["Strike", oc_strike],
                ["Expiration", oc_exp],
                ["Date Opened", oc_open_date_cell],
                ["Days Open", oc_days_open],
                ["Stock Price at Open", oc_price_at_open],
                ["Days Left", f"=DAYS({oc_df},TODAY())" if itm else ""],
                ["Contracts", oc_cts],
            ],
            "G10:H17": [
                ["OPEN CALL METRICS", ""],
                ["Premium Received", oc_prem],
                ["Cost to Close", "=B7" if itm else ""],
                ["Unrealized P&L", "=H11+H12" if itm else ""],
                ["Status", oc_status],
                ["Intrinsic Value",
                 f"=MAX(0,B5-{itm_strike})*E17*100" if itm_strike != "" else ""],
                ["Time Value", "=H12-H15" if itm else ""],
                ["** TV Ann Yield",
                 "=IFERROR(MAX(0,-H16)/(-E17*100*B5+B7)*(365/E16),0)" if itm else ""],
            ],
        })

    if show_puts:
        sections.update({
            f"A{p}:B{p+5}": _put_history_rows(T, L, p),
            f"D{p}:E{p+7}": [
                ["OPEN PUTS", ""],
                ["Strike", op_strike],
                ["Expiration", op_exp],
                ["Date Opened", op_open_date_cell],
                ["Days Open", op_days_open],
                ["Stock Price at Open", op_price_at_open],
                ["Days Left", f"=DAYS({op_df},TODAY())" if op else ""],
                ["Contracts", op_cts],
            ],
            f"G{p}:H{p+7}": [
                ["OPEN PUTS METRICS", ""],
                ["Premium Received", op_prem],
                ["Cost to Close", "=B8" if op else ""],
                ["Unrealized P&L", f"=H{p+1}+H{p+2}" if op else ""],
                ["Status", op_status],
                ["Intrinsic Value",
                 f"=MAX(0,{op_strike}-B5)*E{p+7}*100" if op else ""],
                ["Time Value", f"=H{p+2}-H{p+5}" if op else ""],
                ["TV Ann Yield",
                 f"=IFERROR(IF(E{p+6}>0,MAX(0,-H{p+6})/(-E{p+7}*100*E{p+1})*(365/E{p+6}),0),0)"
                 if op else ""],
            ],
        })

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
