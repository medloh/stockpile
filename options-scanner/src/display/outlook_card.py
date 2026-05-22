"""Market View card for the Single Ticker tab.

Translates the user's (Direction × Option Type) selection into a
short stance summary + example strategies, rendered as a small
accent-bordered callout. The mapping table is the single source of
truth for the six legitimate stance combinations.

Also exports OUTLOOK_TONE_HEX, the four-tone accent palette
(positive / negative / neutral / vol) — shared with the Portfolio
tab's Recommended Action card.
"""

from __future__ import annotations

import streamlit as st


OUTLOOK_TABLE: dict[tuple[bool, str], dict[str, str]] = {
    # (buy?, opt_type) -> {stance, tone, summary, examples}
    # 'tone' picks the accent color: pos = green, neg = red, neutral = amber, vol = purple
    (False, "Calls"): {
        "stance": "Bearish / neutral-down",
        "tone": "neg",
        "summary": "Collect premium on calls you expect to expire worthless. "
                   "Profits if the underlying stays below the strike — the "
                   "classic 'covered call' or 'short call' setup. IV-rich "
                   "premium boosts the credit you receive.",
        "examples": "Covered call · Short call · Credit call spread",
    },
    (False, "Puts"): {
        "stance": "Bullish / neutral-up",
        "tone": "pos",
        "summary": "Collect premium on puts you expect to expire worthless. "
                   "Profits if the underlying stays above the strike. The "
                   "'cash-secured put' is the bullish income trade — you're "
                   "paid to wait for a price you'd be happy to buy at.",
        "examples": "Cash-secured put · Short put · Credit put spread",
    },
    (False, "Both"): {
        "stance": "Range-bound (short volatility)",
        "tone": "neutral",
        "summary": "Sell premium on both sides because you expect the "
                   "underlying to stay inside a range. Profits if IV "
                   "contracts AND the move is small. Beware of binary "
                   "events (earnings, FDA) that can crush range-bound bets.",
        "examples": "Iron condor · Short strangle · Short straddle",
    },
    (True, "Calls"): {
        "stance": "Bullish",
        "tone": "pos",
        "summary": "Pay premium for upside leverage. Profits if the "
                   "underlying rises enough to cover the debit. IV-cheap "
                   "candidates give you a better entry point because you "
                   "buy when volatility is under-priced.",
        "examples": "Long call · Debit call spread · Diagonal / PMCC",
    },
    (True, "Puts"): {
        "stance": "Bearish",
        "tone": "neg",
        "summary": "Pay premium for downside exposure. Profits if the "
                   "underlying falls enough to cover the debit. IV-cheap "
                   "candidates make the directional bet more efficient "
                   "because vol isn't already priced in.",
        "examples": "Long put · Debit put spread · Protective put",
    },
    (True, "Both"): {
        "stance": "Volatility expansion (long vol)",
        "tone": "vol",
        "summary": "Pay premium for a big move in either direction. "
                   "Profits if realized vol exceeds implied vol OR if IV "
                   "expands. Best entered when IV is low AND a catalyst "
                   "is approaching (earnings, FDA). Beware vol crush.",
        "examples": "Long straddle · Long strangle · Calendar spread",
    },
}


OUTLOOK_TONE_HEX = {
    "pos":     "#059669",   # green — success
    "neg":     "#DC2626",   # red — destructive
    "neutral": "#D97706",   # amber — accent
    "vol":     "#8B5CF6",   # purple — vol expansion
}


def render_outlook_card(buy: bool, opt_type: str) -> None:
    """Render the directional-outlook callout for the Single Ticker tab.

    Maps the user's (Direction × Option Type) selection to a structured
    market-view summary so users know what the scan is actually screening
    for. Renders as a small card in the third column of Group 2.
    """
    cfg = OUTLOOK_TABLE.get((buy, opt_type))
    if not cfg:
        return
    accent = OUTLOOK_TONE_HEX[cfg["tone"]]
    st.html(
        f"""
        <div style="
            border-left: 3px solid {accent};
            background: rgba(255,255,255,0.6);
            border-radius: 6px;
            padding: 0.5rem 0.7rem;
            font-family: var(--osc-font), -apple-system, sans-serif;
            line-height: 1.45;
            color: var(--osc-ink-1);
            height: 100%;
        ">
            <div style="font-size: 0.65rem; font-weight: 700;
                        text-transform: uppercase; letter-spacing: 0.08em;
                        color: var(--osc-ink-4); margin-bottom: 2px;">
                Market view
            </div>
            <div style="font-size: 0.92rem; font-weight: 600;
                        color: {accent}; margin-bottom: 4px;">
                {cfg['stance']}
            </div>
            <div style="font-size: 0.78rem; color: var(--osc-ink-2);
                        margin-bottom: 4px;">
                {cfg['summary']}
            </div>
            <div style="font-size: 0.7rem; font-weight: 500;
                        color: var(--osc-ink-3); font-style: italic;">
                e.g. {cfg['examples']}
            </div>
        </div>
        """
    )
