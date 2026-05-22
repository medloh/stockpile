"""Recommended Action card for the Portfolio tab.

For each open position in the uploaded brokerage CSV, picks the
same rank-1 IV-rich call the candidates table would, then renders
it as an explicit instruction card above the table:

  ROLL                — existing covered call: BTC + STO with the
                        net credit and the new stock breakeven.
  SELL TO OPEN        — ≥100 shares, no open call: auto-sized
                        contracts, premium, max profit, ~delta
                        assignment probability, stock breakeven.
  Stock position too  — <100 shares: amber accent, explicit "not
  small               actionable" rather than a misleading partial
                        figure.

Shares the four-tone accent palette with the Market View card via
display.outlook_card.OUTLOOK_TONE_HEX.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from options_scanner.display.outlook_card import OUTLOOK_TONE_HEX


def render_portfolio_action_card(
    ticker: str,
    df_filt: pd.DataFrame,
    spot: float,
    shares: int,
    covered: bool,
    roll_close: float | None,
    open_calls: list[dict],
    min_oi: int,
    min_vol: int,
) -> None:
    """Translate the top IV-rich candidate into an explicit buy/sell action.

    The Portfolio table shows raw option data — this card surfaces the
    'so what should I do?' answer with strike, premium, cash flow, and
    breakeven math computed against the user's actual share count.

    Covered (existing short call) → ROLL: buy back the open call, sell
    the new top pick, show net credit/debit + new breakeven.

    Uncovered (just stock) → SELL TO OPEN: write covered calls. Number
    of contracts is auto-sized to the user's share count (shares // 100).
    """
    # Pick the same #1 row the ranked table picks: IV-rich (descending
    # iv_excess) with open_interest tie-break.
    eligible = df_filt[
        (df_filt["type"] == "call")
        & (df_filt["open_interest"] >= min_oi)
        & (df_filt["volume"] >= min_vol)
    ]
    if eligible.empty:
        return
    pick = (
        eligible.sort_values(["iv_excess", "open_interest"],
                             ascending=[False, False])
        .iloc[0]
    )
    strike = float(pick["strike"])
    expiry = pd.to_datetime(pick["expiration"]).strftime("%b %d '%y")
    mid = float(pick["mid"])
    iv_excess_pp = float(pick["iv_excess"]) * 100.0
    delta = float(pick.get("delta", 0.0))
    max_contracts = max(1, shares // 100)

    accent = OUTLOOK_TONE_HEX["pos"]   # green — premium income

    if covered and roll_close is not None and open_calls:
        # ── ROLL action ───────────────────────────────────────────────
        existing = open_calls[0]
        net_cr_per_contract = (mid - roll_close) * 100.0
        net_cr_total = net_cr_per_contract * existing["contracts"]
        sign = "+" if net_cr_per_contract >= 0 else "−"
        action_label = "ROLL existing covered call"
        action_lines = [
            f"<b>1) Buy to close</b> {existing['contracts']}× <code>{existing['symbol']}</code> at mid ~${roll_close:.2f} → pay <b>${roll_close * 100 * existing['contracts']:,.0f}</b>",
            f"<b>2) Sell to open</b> {existing['contracts']}× <code>{ticker} ${strike:.0f}C exp {expiry}</code> at mid ~${mid:.2f} → collect <b>${mid * 100 * existing['contracts']:,.0f}</b>",
            f"<b>Net result:</b> {sign}${abs(net_cr_total):,.0f} ({sign}${abs(net_cr_per_contract):.2f}/contract)",
        ]
        breakeven_line = f"<b>New breakeven (stock):</b> ${strike + (mid - roll_close):.2f} — below this the roll costs you net"
    else:
        # ── SELL TO OPEN (covered call) ───────────────────────────────
        if shares < 100:
            action_label = "Stock position too small for covered call"
            action_lines = [
                f"You hold <b>{shares}</b> shares — a covered call requires at least 100 shares per contract.",
                f"Top IV-rich call for reference: <code>{ticker} ${strike:.0f}C exp {expiry}</code> at mid ~${mid:.2f}",
            ]
            breakeven_line = ""
            accent = OUTLOOK_TONE_HEX["neutral"]   # amber — informational, not actionable
        else:
            premium_per_contract = mid * 100.0
            premium_total = premium_per_contract * max_contracts
            max_profit_per_share = max(0.0, strike - spot) + mid
            max_profit_total = max_profit_per_share * 100 * max_contracts
            assign_prob = abs(delta) * 100.0
            action_label = "SELL TO OPEN covered call"
            action_lines = [
                f"<b>Action:</b> Sell {max_contracts}× <code>{ticker} ${strike:.0f}C exp {expiry}</code> to open at mid ~${mid:.2f}",
                f"<b>Premium collected:</b> ${premium_total:,.0f} ({max_contracts} contract(s) × ${premium_per_contract:,.0f})",
                f"<b>Max profit if assigned at ${strike:.0f}:</b> ${max_profit_total:,.0f} (capped — your stock gets called away)",
                f"<b>Assignment probability:</b> ~{assign_prob:.0f}% (Δ proxy)",
            ]
            breakeven_line = f"<b>Breakeven (stock):</b> ${spot - mid:.2f} — covered down to this price by the premium received"

    lines_html = "".join(f"<li style='margin: 3px 0;'>{l}</li>" for l in action_lines)
    be_html = (f"<div style='margin-top: 6px; font-size: 0.78rem; "
               f"color: var(--osc-ink-3);'>{breakeven_line}</div>"
               if breakeven_line else "")
    st.html(
        f"""
        <div style="
            border-left: 4px solid {accent};
            background: rgba(255,255,255,0.7);
            border-radius: 8px;
            padding: 0.75rem 1rem;
            margin: 0.6rem 0;
            font-family: var(--osc-font), -apple-system, sans-serif;
            line-height: 1.5;
        ">
            <div style="font-size: 0.65rem; font-weight: 700;
                        text-transform: uppercase; letter-spacing: 0.08em;
                        color: var(--osc-ink-4); margin-bottom: 2px;">
                Recommended action · top IV+pp signal ({iv_excess_pp:+.1f} pp)
            </div>
            <div style="font-size: 1rem; font-weight: 700; color: {accent};
                        margin-bottom: 6px;">
                {action_label}
            </div>
            <ul style="margin: 0; padding-left: 1.1rem; font-size: 0.85rem;
                       color: var(--osc-ink-1);">
                {lines_html}
            </ul>
            {be_html}
        </div>
        """
    )
