#!/usr/bin/env python3
"""
setup_tab.py — Build Position Tracker tabs from a brokerage all-transactions CSV.

Usage:
    python3 setup_tab.py ALL_TRANS.csv Schwab

The script clears the spreadsheet, then creates a tab per ticker plus Summary
tabs and an Other Transactions tab. Live prices are fetched from Yahoo Finance.

Requirements:
    pip install google-auth google-auth-oauthlib google-api-python-client yfinance

================================================================================
YOUTUBE PUBLISHING CHECKLIST — drive views for the setup-video release
================================================================================

BEFORE PUBLISHING (the days leading up to release)
--------------------------------------------------
 1. Title — lead with the outcome, not the tool. Two patterns that work for
    niche finance/dev content:
      "I built a free tool that tracks covered calls across every brokerage"
      "One command turns my Schwab CSV into a live options dashboard"
    Draft 3–5 variants. Pick the one that promises a result a viewer wants.
    Avoid bracket clutter ("[Tutorial]", "[2026]") — kills CTR.

 2. Thumbnail — bigger payoff than the title for cold-traffic CTR.
      • High contrast, 3–5 words max (large, sans-serif, bold).
      • Show the *result* (the formatted Google Sheet) not the code.
      • Add a face/expression if you're on camera; eye contact > none.
      • A/B test using YouTube Studio's built-in thumbnail test.

 3. Hook (first 15 seconds) — state problem + show solution. No long intros,
    no "hey guys welcome back". Cut straight to the working dashboard, then
    explain what it is. Retention at 0:30 dictates everything downstream.

 4. Chapters — required for tutorials. Counterintuitively, letting viewers
    skip improves session metrics because non-skippers churn entirely.
    Suggested chapters: Problem → Demo → Setup → Each broker → Customizing
    → Roadmap. Mark them in the description as `0:00 Title`.

 5. Description — first 2 lines are above the fold on mobile. Put the value
    prop + GitHub link there. Then chapters, then the long form. Include
    keywords naturally: covered calls, sold puts, options tracker, Schwab,
    Robinhood, Fidelity, Merrill Edge, Google Sheets, Python.

 6. Tags — mix broad ("covered calls tracker") and long-tail
    ("schwab csv export python"). Long-tail is where small channels rank.

 7. Captions — upload corrected captions. YouTube indexes them for search.
    Auto-captions on technical jargon ("covered call", "ITM", broker names)
    are usually wrong; fix them.

 8. End screen — last 20 seconds. Point to: subscribe button + your single
    most relevant prior video + a card to the GitHub repo.

 9. Pre-publish dry run — set the video to Unlisted, watch it end-to-end on
    your phone (most viewers will). Listen for audio drops, dead air,
    pacing problems. Re-edit if anything feels slow.

10. Seed comment — pre-write a comment to pin at publish time. Include the
    GitHub link, a timestamp index, and ONE open question that invites
    replies ("which broker should I support next?"). Engagement velocity
    in the first hour is a major ranking signal.

11. Community tab teaser — 24h before publish, post a screenshot of the
    finished sheet in the YouTube Community tab. Builds anticipation,
    notifies subscribers, and pre-warms the algorithm.

12. Plan distribution — list the subreddits / forums / Discords you'll
    share to. Read each sub's self-promotion rules in advance; some
    require a 9:1 contribution-to-promotion ratio.

DURING PUBLISHING (the day of, and the first 24 hours)
------------------------------------------------------
 1. Time the upload to your audience. US covered-call traders skew older
    and weekday 9–5 ET is dead (they're at work). Saturday morning ET
    or weekday evening 7–10 ET tend to perform best for finance hobby
    content. Avoid days with big market news (CPI, FOMC, earnings of a
    mega-cap) — viewer attention goes there instead.

 2. Use a Premiere with a 30–60 minute countdown. The live chat creates
    early engagement signals YouTube weighs heavily, and the "Premiere"
    badge attracts notification clicks the regular upload doesn't.

 3. Pin your seed comment the moment it's live.

 4. Reply to EVERY comment in the first 2 hours. Even just hearting it
    counts. Reply velocity outweighs reply quality for ranking.

 5. Share to 3–5 targeted subreddits within the first 30 minutes:
      r/options, r/CoveredCalls, r/thetagang, r/algotrading,
      r/PersonalFinance (only if framed for a general audience).
    Lead with the value, not the link. A separate "[Tool I built]" post
    pointing to the GitHub repo (with the YouTube link in the README)
    often does better than dropping the YouTube link directly — Reddit
    penalizes overt video drops.

 6. Tweet/X post with screenshot + GitHub link. Reply to your own tweet
    with the YouTube link 10 minutes later — top-level tweets with a
    YouTube URL get suppressed.

 7. Notify your direct channels (Discord, Telegram, email list) within
    30 minutes of publish. The early-hour view spike is what tells the
    algorithm to push the video to non-subscribers.

 8. Watch retention live in YouTube Studio. The drop-off points are the
    single most valuable feedback you'll get for your next video.

 9. 24-hour CTR check. If CTR < 4%, swap the thumbnail. If CTR is fine
    but watch time is low, the title oversold — soften it.

10. Don't edit the title/thumbnail/description in the first 4 hours
    unless something is broken — early edits reset some ranking signals.
    After that, iterate freely.
================================================================================
"""

import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

import config
import sheets
from stocks_shared.analysis import (
    compute_avg_held_anchor,
    compute_closed_avg_days,
    compute_status,
    detect_open_positions,
    detect_suspicious_positions,
    get_last_option,
)
from layout import (TXN_ROW, build_open_sections, build_closed_sections,
                    build_txn_only_sections, _offsets)


# ── Yahoo Finance ─────────────────────────────────────────────────────────────
from stocks_shared.yahoo import (
    fetch_live_price as fetch_yahoo_price,
    fetch_option_market_value,
    fetch_history,
)


# ── Format-request builder ───────────────────────────────────────────────────

def _open_call_section_fmt(sheet_id, base_0: int) -> list:
    """Per-call format requests for one OPEN CALL section pair.

    base_0 is the 0-indexed row of the section header (= the row that
    contains "OPEN CALL — $strike M/DD/YY" and "OPEN CALL METRICS").
    The 8-row block uses base_0..base_0+7.
    """
    return [
        sheets.section_header(sheet_id, base_0),
        sheets.currency(sheet_id, base_0 + 1, 4, base_0 + 2, 5),    # Strike (E)
        sheets.date_fmt(sheet_id, base_0 + 3, 4, base_0 + 4, 5),    # Date Opened (E)
        sheets.currency(sheet_id, base_0 + 1, 7, base_0 + 4, 8),    # Premium/Cost/Unrealized (H)
        sheets.green_if_positive(sheet_id, base_0 + 1, 7, base_0 + 2, 8),  # Premium Received
        sheets.green_if_positive(sheet_id, base_0 + 3, 7, base_0 + 4, 8),  # Unrealized P&L
        sheets.plain_number(sheet_id, base_0 + 4, 4, base_0 + 5, 5),       # Days Open
        sheets.currency(sheet_id, base_0 + 5, 4, base_0 + 6, 5),           # Stock Price at Open
        sheets.plain_number(sheet_id, base_0 + 6, 4, base_0 + 7, 5),       # Days Left
        sheets.plain_number(sheet_id, base_0 + 7, 4, base_0 + 8, 5),       # Contracts
        sheets.right_align(sheet_id, base_0 + 4, 7, base_0 + 5, 8),        # Status
        sheets.currency(sheet_id, base_0 + 5, 7, base_0 + 7, 8),           # Intrinsic / Time Value
        sheets.percent(sheet_id, base_0 + 7, 7, base_0 + 8, 8),             # ** TV Ann Yield
    ]


def _open_put_section_fmt(sheet_id, base_0: int) -> list:
    """Per-put format requests for one OPEN PUT section pair. Mirror
    of _open_call_section_fmt; rows/cols match."""
    return [
        sheets.section_header(sheet_id, base_0),
        sheets.currency(sheet_id, base_0 + 1, 4, base_0 + 2, 5),    # Strike (E)
        sheets.date_fmt(sheet_id, base_0 + 3, 4, base_0 + 4, 5),    # Date Opened (E)
        sheets.currency(sheet_id, base_0 + 1, 7, base_0 + 4, 8),    # Premium/Cost/Unrealized (H)
        sheets.green_if_positive(sheet_id, base_0 + 1, 7, base_0 + 2, 8),  # Premium Received
        sheets.green_if_positive(sheet_id, base_0 + 3, 7, base_0 + 4, 8),  # Unrealized P&L
        sheets.plain_number(sheet_id, base_0 + 4, 4, base_0 + 5, 5),       # Days Open
        sheets.currency(sheet_id, base_0 + 5, 4, base_0 + 6, 5),           # Stock Price at Open
        sheets.plain_number(sheet_id, base_0 + 6, 4, base_0 + 7, 5),       # Days Left
        sheets.plain_number(sheet_id, base_0 + 7, 4, base_0 + 8, 5),       # Contracts
        sheets.right_align(sheet_id, base_0 + 4, 7, base_0 + 5, 8),        # Status
        sheets.currency(sheet_id, base_0 + 5, 7, base_0 + 7, 8),           # Intrinsic / Time Value
        sheets.percent(sheet_id, base_0 + 7, 7, base_0 + 8, 8),             # TV Ann Yield
    ]


def build_fmt_requests(sheet_id, status: str, p: int, i: int, txn_row: int,
                       show_calls: bool, show_puts: bool,
                       n_calls: int = 1, n_puts: int = 1) -> list:
    """Return the list of Sheets API format requests for a position tab.

    Extracted from _setup_position_tab so it can be unit-tested without
    touching Google Sheets. Each request is the dict produced by a
    `sheets.*` formatter helper; the layout module owns *what* goes in
    each cell, this owns *how* each cell is formatted.

    Cell-type invariants worth pinning in tests:
      - B6 (Last Updated) → DATE_TIME
      - E8 (Position Opened) → DATE
      - E13 (Date Opened, calls section) → DATE when shown
      - E{p+3} (Date Opened, puts section) → DATE when shown
      - Closed variants surface a second DATE cell (Date Closed) one
        row below Date Opened.
    """
    p0 = p - 1
    i0 = i - 1

    requests = [
        *sheets.title_row(sheet_id),
        sheets.status_cell_fmt(sheet_id, status),
        sheets.section_header(sheet_id, 2),                     # CURRENT VALUES
        sheets.section_header(sheet_id, i0),                    # INCOME/P&L/RETURNS
        sheets.section_header(sheet_id, txn_row - 3),           # TXN LOG
        sheets.col_header(sheet_id, txn_row - 2),
        sheets.yellow_bg(sheet_id, 4, 1, 8, 2),                 # B5-B8: live-data cells (yellow)
        sheets.currency(sheet_id, 3, 1, 5, 2),                  # B4-B5: Adj Cost, Stock Price
        sheets.datetime_fmt(sheet_id, 5, 1, 6, 2),              # B6: Last Updated
        sheets.currency(sheet_id, 6, 1, 8, 2),                  # B7-B8: Calls/Puts Market Value
        sheets.currency(sheet_id, 3, 4, 4, 5),                  # E4: Avg Cost / Share
        sheets.plain_number(sheet_id, 4, 4, 5, 5),              # E5: Shares Held
        sheets.currency(sheet_id, 5, 4, 7, 5),                  # E6-E7: Total Invested, Market Value
        sheets.date_fmt(sheet_id, 7, 4, 8, 5),                  # E8: Position Opened
        sheets.currency(sheet_id, 3, 7, 4, 8),
        sheets.percent(sheet_id, 4, 7, 5, 8),
        sheets.plain_number(sheet_id, 5, 7, 7, 8),
        sheets.percent(sheet_id, 7, 7, 8, 8),
        # Income / P&L / Returns (dynamic rows)
        sheets.currency(sheet_id, i0 + 1, 1, i0 + 2, 2),        # Total Dividends
        sheets.plain_number(sheet_id, i0 + 2, 1, i0 + 3, 2),    # Dividend Count
        sheets.currency(sheet_id, i0 + 3, 1, i0 + 5, 2),        # Net premiums
        sheets.currency(sheet_id, i0 + 1, 4, i0 + 6, 5),        # P&L data
        sheets.currency(sheet_id, i0 + 1, 7, i0 + 4, 8),        # Amount Invested / Close-out / Total Income
        sheets.percent(sheet_id, i0 + 4, 7, i0 + 6, 8),         # Ann Yields
        sheets.currency(sheet_id, txn_row - 1, 7, 1000, 10),
        sheets.green_if_positive(sheet_id, 3, 7, 5, 8),
        sheets.green_if_positive(sheet_id, 7, 7, 8, 8),
        sheets.green_if_positive(sheet_id, 5, 4, 6, 5),
        sheets.green_if_positive(sheet_id, i0 + 1, 4, i0 + 6, 5),  # P&L breakdown
        sheets.green_if_positive(sheet_id, i0 + 1, 1, i0 + 2, 2),  # Dividends
        sheets.green_if_positive(sheet_id, i0 + 3, 1, i0 + 5, 2),  # Net premiums
        sheets.green_if_positive(sheet_id, i0 + 1, 7, i0 + 4, 8),  # Currency returns
        sheets.green_if_positive(sheet_id, i0 + 4, 7, i0 + 6, 8),  # Ann Yields
    ]

    if show_calls:
        # CALL HISTORY aggregate stats — always at rows 10-15 (0-indexed 9-14)
        requests += [
            sheets.section_header(sheet_id, 9),
            sheets.currency(sheet_id, 10, 1, 15, 2),             # B11-B15
            sheets.green_if_positive(sheet_id, 12, 1, 13, 2),    # Net Call Premium
            sheets.green_if_positive(sheet_id, 14, 1, 15, 2),    # Covered Call Results
            sheets.green_if_positive(sheet_id, 10, 1, 11, 2),    # Call Premium Received
        ]
        if status != "Closed":
            # Per-call OPEN CALL section pairs, stacked vertically.
            n = max(n_calls, 1)
            for idx in range(n):
                base_0 = 9 + 9 * idx
                requests += _open_call_section_fmt(sheet_id, base_0)
        else:
            # Closed position: single LAST CALL section, 9 rows
            requests += [
                sheets.currency(sheet_id, 10, 4, 11, 5),         # Strike
                sheets.date_fmt(sheet_id, 12, 4, 13, 5),         # Date Opened
                sheets.currency(sheet_id, 10, 7, 13, 8),         # Premium/Cost/Unrealized
                sheets.green_if_positive(sheet_id, 10, 7, 11, 8),
                sheets.date_fmt(sheet_id, 13, 4, 14, 5),         # Date Closed
                sheets.plain_number(sheet_id, 14, 4, 15, 5),     # Days Open
                sheets.currency(sheet_id, 15, 4, 16, 5),         # Price at Open
                sheets.currency(sheet_id, 16, 4, 17, 5),         # Price at Close
                sheets.plain_number(sheet_id, 17, 4, 18, 5),     # Contracts
                sheets.right_align(sheet_id, 13, 7, 14, 8),      # Status at Close
                sheets.right_align(sheet_id, 14, 7, 15, 8),      # Closed By
                sheets.currency(sheet_id, 15, 7, 16, 8),         # Missed Upside
                sheets.red_text(sheet_id, 15, 7, 16, 8),
            ]

    if show_puts:
        # PUT HISTORY aggregate stats — always at rows p..p+5
        requests += [
            sheets.section_header(sheet_id, p0),
            sheets.currency(sheet_id, p0 + 1, 1, p0 + 6, 2),
            sheets.green_if_positive(sheet_id, p0 + 3, 1, p0 + 4, 2),  # Net Put Premium
            sheets.green_if_positive(sheet_id, p0 + 5, 1, p0 + 6, 2),  # Put Results
            sheets.green_if_positive(sheet_id, p0 + 1, 1, p0 + 2, 2),  # Put Premium Received
        ]
        if status != "Closed":
            n = max(n_puts, 1)
            for idx in range(n):
                base_0 = p0 + 9 * idx
                requests += _open_put_section_fmt(sheet_id, base_0)
        else:
            requests += [
                sheets.currency(sheet_id, p0 + 1, 4, p0 + 2, 5),   # Strike
                sheets.date_fmt(sheet_id, p0 + 3, 4, p0 + 4, 5),   # Date Opened
                sheets.currency(sheet_id, p0 + 1, 7, p0 + 4, 8),   # Premium/Cost/Unrealized
                sheets.green_if_positive(sheet_id, p0 + 1, 7, p0 + 2, 8),
                sheets.date_fmt(sheet_id, p0 + 4, 4, p0 + 5, 5),   # Date Closed
                sheets.plain_number(sheet_id, p0 + 5, 4, p0 + 6, 5),
                sheets.currency(sheet_id, p0 + 6, 4, p0 + 7, 5),
                sheets.currency(sheet_id, p0 + 7, 4, p0 + 8, 5),
                sheets.plain_number(sheet_id, p0 + 8, 4, p0 + 9, 5),
                sheets.right_align(sheet_id, p0 + 4, 7, p0 + 5, 8),
                sheets.right_align(sheet_id, p0 + 5, 7, p0 + 6, 8),
                sheets.currency(sheet_id, p0 + 6, 7, p0 + 7, 8),
                sheets.red_text(sheet_id, p0 + 6, 7, p0 + 7, 8),
            ]

    return requests


# ── Ticker processing ─────────────────────────────────────────────────────────

def _txn_display(row):
    """Return the row with qty negated for sell-side actions."""
    row = list(row)
    action = str(row[1]).strip() if len(row) > 1 else ""
    if action.startswith("Sell") and row[6] not in ("", None):
        try:
            row[6] = -abs(int(row[6]))
        except (ValueError, TypeError):
            pass
    return row


def process_ticker(ticker, transactions, brokerage, service,
                   current_price=None, current_call_value=None, current_put_value=None):
    """Build/update a single ticker tab and its Summary row."""
    tab_name = ticker
    open_positions = detect_open_positions(transactions)
    status, issues = compute_status(transactions, open_positions)
    suspicious = detect_suspicious_positions(transactions, open_positions)

    print(f"  Ticker: {ticker}  |  Status: {status}  |  Transactions: {len(transactions)}")
    for issue in issues:
        print(f"    ! {issue}")
    for w in suspicious:
        print(f"    ? {w}")

    if status == "Inconsistent":
        print("  Creating transaction-log-only tab for inconsistent position.")
        sheet_id = sheets.recreate_tab(service, tab_name)
        ITXN = 7  # transaction data start row for inconsistent tabs
        sheets.batch_write(service, tab_name, {
            "A1:C1": [[ticker, "Status", "Inconsistent"]],
            "A3:A3": [["; ".join(issues)]],
            "A5:K5": [["TRANSACTION LOG", "", "", "", "", "", "", "", "", "", ""]],
            "A6:K6": [["Date", "Action", "Type", "Symbol", "Strike", "Expiration",
                       "Qty", "Price", "Fees", "Net Amount", "Notes"]],
        })
        chunk = 50
        for idx in range(0, len(transactions), chunk):
            start_row = ITXN + idx
            batch = [_txn_display(r) for r in transactions[idx:idx+chunk]]
            end_row = start_row + len(batch) - 1
            sheets.write_range(service, tab_name, f"A{start_row}:K{end_row}", batch)
        sheets.apply_fmt(service, sheet_id, [
            *sheets.title_row(sheet_id),
            sheets.status_cell_fmt(sheet_id, status),
            sheets.section_header(sheet_id, 4),  # row 5 — blue TXN LOG header
            sheets.col_header(sheet_id, 5),       # row 6 — light green column headers
        ])
        sheets._write_summary_row(service, tab_name, status, issues,
                                   show_calls=False, show_puts=False)
        return

    if current_price is None:
        current_price = fetch_yahoo_price(ticker)
        if current_price is not None:
            print(f"  Fetched price from Yahoo Finance: {current_price}")

    # Fetch underlying price at open_date for each open position
    for p in open_positions:
        p["price_at_open"] = None
        od = p.get("open_date")
        if od:
            try:
                import re as _re
                m = _re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", od)
                if m:
                    ymd = f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
                    from datetime import timedelta, date as _date
                    d = _date.fromisoformat(ymd)
                    end = (d + timedelta(days=4)).isoformat()  # +4 to catch weekends
                    hist = fetch_history(ticker, start=ymd, end=end)
                    if not hist.empty:
                        p["price_at_open"] = round(float(hist["Close"].iloc[0]), 2)
            except Exception:
                pass

    open_calls = [p for p in open_positions if p["type"] == "Call"]
    open_puts  = [p for p in open_positions if p["type"] == "Put"]
    show_calls = bool(open_calls) or any(row[2] == "Call" for row in transactions)
    show_puts  = bool(open_puts)  or any(row[2] == "Put"  for row in transactions)

    _p, _i, txn_row = _offsets(show_calls, show_puts,
                               n_calls=len(open_calls),
                               n_puts=len(open_puts))

    # Per-call/put market values populate each open-position dict's
    # `market_value` field (used by the per-call/per-put OPEN METRICS
    # sections for Cost to Close). The aggregate sum across all open
    # calls/puts becomes current_call_value / current_put_value for
    # the Calls/Puts Market Value cells in CURRENT VALUES.
    if open_calls:
        for pos in open_calls:
            mv = fetch_option_market_value(
                ticker, "Call", pos["expiration"], pos["strike"], pos["contracts"])
            pos["market_value"] = round(mv, 2) if mv is not None else None
        if current_call_value is None:
            total = sum(p["market_value"] or 0.0 for p in open_calls)
            if total != 0:
                current_call_value = round(total, 2)
                print(f"  Fetched call market value from Yahoo: {current_call_value}")

    if open_puts:
        for pos in open_puts:
            mv = fetch_option_market_value(
                ticker, "Put", pos["expiration"], pos["strike"], pos["contracts"])
            pos["market_value"] = round(mv, 2) if mv is not None else None
        if current_put_value is None:
            total = sum(p["market_value"] or 0.0 for p in open_puts)
            if total != 0:
                current_put_value = round(total, 2)
                print(f"  Fetched put market value from Yahoo: {current_put_value}")

    print(f"  Price: {current_price}  Call MV: {current_call_value}  Put MV: {current_put_value}")
    print(f"  Open positions: {len(open_positions)}")
    for p in open_positions:
        print(f"    {p['type']} {p['symbol']}  contracts={p['contracts']}  prem={p['premium']:.2f}")

    sheet_id = sheets.recreate_tab(service, tab_name)
    print(f"  Recreated tab '{tab_name}'.")

    print("  Writing layout...")
    last_row = txn_row + len(transactions) - 1
    avg_held_anchor = compute_avg_held_anchor(transactions)
    if avg_held_anchor:
        print(f"  FIFO avg-held anchor: {avg_held_anchor[0]:04d}-{avg_held_anchor[1]:02d}-{avg_held_anchor[2]:02d}")
    closed_avg_days = compute_closed_avg_days(transactions) if status == "Closed" else None
    if closed_avg_days is not None:
        print(f"  Closed position avg days held: {closed_avg_days}")

    last_call = last_put = None
    if status == "Closed":
        def _fetch_opt_prices(opt, strike, is_call):
            from datetime import timedelta, date as _date
            for date_key, price_key in [("open_date", "price_at_open"),
                                         ("close_date", "price_at_close")]:
                # For assigned options the effective close price is the strike,
                # not the market close — skip to avoid a misleading value.
                if price_key == "price_at_close" and opt.get("disposition") == "Assigned":
                    continue
                ds = opt.get(date_key)
                if not ds:
                    continue
                try:
                    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", ds)
                    if not m:
                        continue
                    ymd = f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
                    end = (_date.fromisoformat(ymd) + timedelta(days=4)).isoformat()
                    hist = fetch_history(ticker, start=ymd, end=end)
                    if not hist.empty:
                        opt[price_key] = round(float(hist["Close"].iloc[0]), 2)
                except Exception:
                    pass
            # Resolve ITM/OTM for BTC cases now that we have the close price
            if opt.get("itm_at_close") is None and opt.get("price_at_close") is not None:
                px = opt["price_at_close"]
                opt["itm_at_close"] = (px > strike) if is_call else (px < strike)

        if show_calls:
            last_call = get_last_option(transactions, "Call")
            if last_call:
                _fetch_opt_prices(last_call, last_call["strike"], is_call=True)
        if show_puts:
            last_put = get_last_option(transactions, "Put")
            if last_put:
                _fetch_opt_prices(last_put, last_put["strike"], is_call=False)

    if status == "Closed":
        sections = build_closed_sections(tab_name, open_positions, last_row,
                                         brokerage, closed_avg_days,
                                         show_calls=show_calls, show_puts=show_puts,
                                         last_call=last_call, last_put=last_put)
    else:
        sections = build_open_sections(tab_name, open_positions, last_row,
                                       avg_held_anchor, brokerage,
                                       show_calls=show_calls, show_puts=show_puts)
    sheets.batch_write(service, tab_name, sections)

    sheets.write_range(service, tab_name, "B5",
                       [[current_price if current_price is not None else ""]])
    if status != "Closed":
        sheets.write_range(service, tab_name, "B7:B8", [
            [current_call_value if current_call_value is not None else ""],
            [current_put_value if current_put_value is not None else ""],
        ])

    print(f"  Writing {len(transactions)} transactions...")
    chunk = 50
    for i in range(0, len(transactions), chunk):
        start_row = txn_row + i
        batch = [_txn_display(r) for r in transactions[i:i+chunk]]
        end_row = start_row + len(batch) - 1
        sheets.write_range(service, tab_name, f"A{start_row}:K{end_row}", batch)

    if suspicious:
        warn_row = txn_row + len(transactions) + 1
        sheets.write_range(service, tab_name, f"A{warn_row}", [["Possible data issues:"]])
        for idx, w in enumerate(suspicious):
            sheets.write_range(service, tab_name, f"A{warn_row + 1 + idx}", [[w]])
        n_warn_rows = 1 + len(suspicious)
        sheets.apply_fmt(service, sheet_id, [
            sheets.warning_label(sheet_id, warn_row - 1, warn_row - 1 + n_warn_rows),
            *[{"mergeCells": {
                "range": {"sheetId": sheet_id,
                          "startRowIndex": warn_row - 1 + r,
                          "endRowIndex": warn_row + r,
                          "startColumnIndex": 0, "endColumnIndex": 11},
                "mergeType": "MERGE_ALL",
            }} for r in range(n_warn_rows)],
        ])

    adj_text = (
        "** Adj Cost Basis / Share: net sum of all cash transactions (stock buys/sells, "
        "option premiums received/paid, dividends, fees) divided by current shares held. "
        "Open options contribute only their received premium since no close transaction "
        "has occurred, making this equivalent to cost basis assuming all open options "
        "expire worthless."
    )
    yellow_legend_text = (
        "Yellow cells = live data, refreshed each run (current stock price, "
        "open-option market values, and update time). All other cells in this "
        "tab are formulas derived from the transaction log."
    )
    tv_call_text = (
        "** TV Ann Yield: annualized yield of the open call's time value relative to "
        "the close-out value of the covered shares (covered shares market value + call market value), "
        "scaled by days remaining on the contract."
    )
    tv_put_text = (
        "** TV Ann Yield: annualized yield of the open put's time value relative to "
        "the cash securing the puts (strike * 100 * contracts), "
        "scaled by days remaining on the contract."
    )
    ic_yield_text = (
        "Ann Yield on Invested Capital: Total P&L divided by total capital invested in the position "
        "(stock purchases net of sales), annualized by Avg Days Held."
    )
    cov_yield_text = (
        "Ann Yield on Close-out Value: Total P&L divided by the current close-out value "
        "(stock market value + open options market value), annualized by Avg Days Held."
    )

    # Aliases used in formatting below (same values as _p/_i/_txn_row computed above)
    p = _p
    i = _i

    if issues:
        sheets.write_range(service, tab_name, "K1", [["Data issues: " + "; ".join(issues)]])
    sheets.write_range(service, tab_name, "K4", [[adj_text]])
    sheets.write_range(service, tab_name, "K5", [[yellow_legend_text]])
    if show_calls and status != "Closed":
        sheets.write_range(service, tab_name, "K17", [[tv_call_text]])
    if show_puts:
        sheets.write_range(service, tab_name, f"K{p+7}", [[tv_put_text]])
    sheets.write_range(service, tab_name, f"K{i+4}", [[ic_yield_text]])
    sheets.write_range(service, tab_name, f"K{i+5}", [[cov_yield_text]])

    def footnote_merge(row0):
        return {"mergeCells": {
            "range": {"sheetId": sheet_id,
                      "startRowIndex": row0, "endRowIndex": row0 + 1,
                      "startColumnIndex": 10, "endColumnIndex": 26},
            "mergeType": "MERGE_ALL",
        }}

    def footnote_overflow(row0):
        return {"repeatCell": {
            "range": {"sheetId": sheet_id,
                      "startRowIndex": row0, "endRowIndex": row0 + 1,
                      "startColumnIndex": 10, "endColumnIndex": 26},
            "cell": {"userEnteredFormat": {"wrapStrategy": "OVERFLOW_CELL"}},
            "fields": "userEnteredFormat.wrapStrategy",
        }}

    # p0, i0: 0-indexed row numbers for put/income sections
    p0 = p - 1
    i0 = i - 1

    merge_fmt = [
        footnote_merge(3),                           # K4:Z4 — Adj Cost Basis footnote
        footnote_merge(4),                           # K5:Z5 — Yellow legend footnote
        # ** TV Ann Yield blue highlight + K footnote anchor, one per
        # open call section (each call's TV row sits at 0-indexed
        # 16 + 9*idx — i.e. the 8th row of the 8-row section starting
        # at base_0 = 9 + 9*idx).
        *([req
           for idx in range(max(len(open_calls), 1))
           for tv_row in [16 + 9 * idx]
           for req in [
               footnote_merge(tv_row),
               sheets.light_bg(sheet_id, tv_row, 6, tv_row + 1, 8),
               sheets.light_bg(sheet_id, tv_row, 10, tv_row + 1, 11),
               footnote_overflow(tv_row),
           ]
           ] if show_calls and status != "Closed" else []),
        # Same per-put — TV row sits at p0 + 7 + 9*idx.
        *([req
           for idx in range(max(len(open_puts), 1))
           for tv_row in [p0 + 7 + 9 * idx]
           for req in [
               footnote_merge(tv_row),
               sheets.light_bg(sheet_id, tv_row, 6, tv_row + 1, 8),
               sheets.light_bg(sheet_id, tv_row, 10, tv_row + 1, 11),
               footnote_overflow(tv_row),
           ]
           ] if show_puts and status != "Closed" else []),
        footnote_merge(i0 + 4), footnote_merge(i0 + 5),
        sheets.light_bg(sheet_id, 3, 0, 4, 2),       # A4:B4 — Adj Cost Basis row
        sheets.light_bg(sheet_id, 3, 3, 4, 5),       # D4:E4 — Avg Cost / Share (paired)
        sheets.light_bg(sheet_id, 3, 10, 4, 11),     # K4 only — Adj Cost footnote anchor
        sheets.yellow_bg(sheet_id, 4, 10, 8, 11),    # K5:K8 — vertical yellow strip mirroring B5:B8
        sheets.light_bg(sheet_id, i0 + 4, 6, i0 + 5, 8),
        sheets.light_bg(sheet_id, i0 + 4, 10, i0 + 5, 11),  # K{i+5} only
        sheets.light_bg(sheet_id, i0 + 5, 6, i0 + 6, 8),
        sheets.light_bg(sheet_id, i0 + 5, 10, i0 + 6, 11),  # K{i+6} only
        footnote_overflow(3), footnote_overflow(4),
        footnote_overflow(i0 + 4), footnote_overflow(i0 + 5),
    ]
    if issues:
        merge_fmt += [
            footnote_merge(0), footnote_overflow(0),
            sheets.light_bg(sheet_id, 0, 10, 1, 11),  # K1 only — issues footer anchor
        ]
    sheets.apply_fmt(service, sheet_id, merge_fmt)

    print("  Applying formatting...")
    fmt_requests = build_fmt_requests(
        sheet_id, status, p, i, txn_row, show_calls, show_puts,
        n_calls=len(open_calls), n_puts=len(open_puts),
    )
    sheets.apply_fmt(service, sheet_id, fmt_requests)

    print("  Updating Summary...")
    sheets._write_summary_row(service, tab_name, status, issues,
                               show_calls=show_calls, show_puts=show_puts)
    print(f"  Done: '{tab_name}'")


# ── Main ──────────────────────────────────────────────────────────────────────

def _load_parser(brokerage: str):
    b = brokerage.lower()
    if b == "schwab":
        from stocks_shared.parsers.schwab import parse_all_transactions
        return parse_all_transactions
    if b == "robinhood":
        from stocks_shared.parsers.robinhood import parse_all_transactions
        return parse_all_transactions
    if b == "fidelity":
        from stocks_shared.parsers.fidelity import parse_all_transactions
        return parse_all_transactions
    if b == "merrill":
        from stocks_shared.parsers.merrill import parse_all_transactions
        return parse_all_transactions
    print(f"Error: Unknown brokerage '{brokerage}'. Supported: schwab, robinhood, fidelity, merrill")
    sys.exit(1)


def _run_account(acct, csv_path: str, service):
    start_time = datetime.now()
    parse_all_transactions = _load_parser(acct.brokerage)

    sheets.configure(acct.sheet_id, config.CREDS_PATH, config.TOKEN_PATH)

    print(f"Parsing {csv_path}...")
    ticker_transactions, other_rows = parse_all_transactions(csv_path)
    tickers = sorted(ticker_transactions.keys())
    print(f"Found {len(tickers)} ticker(s): {', '.join(tickers)}")

    print("Clearing existing tabs...")
    sheets.clear_all_tabs(service)
    print("Creating summary tabs...")
    for stab in ["Summary-Open", "Summary-Closed", "Summary-Inconsistent"]:
        sheets._ensure_summary_tab(service, stab)

    for ticker in tickers:
        print(f"\n  Processing {ticker}...")
        process_ticker(ticker, ticker_transactions[ticker], acct.brokerage, service)

    if other_rows:
        print(f"\nWriting Other Transactions tab ({len(other_rows)} rows)...")
        sheets.write_other_transactions_tab(service, other_rows)

    sheets.delete_placeholder(service)
    sheets.reorder_summary_tabs_first(service)
    print("\nWriting Summary totals...")
    labels_row = sheets.write_summary_totals(service, "Summary-Open")
    sheets.write_summary_totals(service, "Summary-Closed")
    end_time = datetime.now()
    if labels_row is not None:
        sheets.write_run_timestamps(service, "Summary-Open", labels_row, start_time, end_time)
        print(f"  Run timestamps written (start {start_time:%H:%M:%S}, end {end_time:%H:%M:%S}).")


_LOG_PATH = Path(__file__).parent.parent / "tracker.log"


def _log(msg: str):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Build position tracker tabs from brokerage CSV exports.",
        epilog="With no arguments, runs all accounts configured in config.toml.",
    )
    parser.add_argument("--brokerage", metavar="NAME", help="Only run accounts for this brokerage (e.g. schwab).")
    parser.add_argument("--csv", dest="csv_override", metavar="FILE", help="Override the CSV file path from config.")
    args = parser.parse_args()

    _log("=== Run started ===")
    try:
        accounts = config.get_all_accounts(args.brokerage)
        if not accounts:
            desc = f"for brokerage '{args.brokerage}'" if args.brokerage else "in config.toml"
            _log(f"ERROR: No configured accounts found {desc}.")
            sys.exit(1)

        sheets.configure(accounts[0].sheet_id, config.CREDS_PATH, config.TOKEN_PATH)
        _log("Connecting to Google Sheets...")
        service = sheets.get_service()

        if args.csv_override:
            csv_abs = str(Path(args.csv_override).resolve())
            matched = [a for a in accounts if a.csv and Path(a.csv).resolve() == Path(csv_abs).resolve()]
            if matched:
                accounts = matched

        for acct in accounts:
            csv_path = args.csv_override or acct.csv
            if not csv_path:
                _log(f"Skipping {acct.brokerage} ({acct.sheet_id}): no CSV configured and --csv not provided.")
                continue

            _log(f"Processing: {acct.brokerage} | CSV: {csv_path}")
            _run_account(acct, csv_path, service)
            _log(f"Done: {acct.brokerage} / {acct.sheet_id}")

        _log("=== Run completed successfully ===")

    except Exception as e:
        _log(f"ERROR: {e}")
        _log(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
