"""Generate a plain-language analysis of scan results using Claude."""

import os
import logging

log = logging.getLogger(__name__)


def generate_explanation(
    df,
    ticker: str,
    spot: float,
    earnings_dates: list,
    mode: str,
    roll_close_cost: float | None = None,
) -> str | None:
    """Return a plain-language analysis, or None if the API is unavailable."""
    try:
        import anthropic
    except ImportError:
        log.debug("anthropic package not installed, skipping explanation")
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.debug("ANTHROPIC_API_KEY not set, skipping explanation")
        return None

    type_label = {
        "call": "covered calls",
        "put": "cash-secured puts",
        "both": "calls and puts",
    }.get(mode, "options")

    top = df.sort_values(
        ["iv_excess", "open_interest"], ascending=[False, False]
    ).head(10)

    rows_lines = []
    for _, r in top.iterrows():
        spread_pct = (r["ask"] - r["bid"]) / r["mid"] * 100 if r["mid"] > 0 else 0
        line = (
            f"  ${r['strike']:.0f} exp {r['expiration']} "
            f"DTE={r['dte']} mid=${r['mid']:.2f} "
            f"spread={spread_pct:.0f}% "
            f"IV={r['iv']*100:.1f}% IV+pp={r['iv_excess']*100:+.1f} "
            f"delta={r['delta']:.2f} ann={r['ann_yield_pct']:.1f}% "
            f"OI={r['open_interest']}"
        )
        if "earnings_count" in r and r["earnings_count"] > 0:
            line += f" earn={int(r['earnings_count'])}"
        if roll_close_cost is not None:
            line += f" net_credit=${r['mid'] - roll_close_cost:+.2f}"
        rows_lines.append(line)

    earn_text = (
        f"Upcoming earnings: {', '.join(d.strftime('%b %d') for d in earnings_dates[:4])}"
        if earnings_dates
        else "WARNING: No earnings dates returned by data source -- verify manually before trading."
    )

    roll_context = (
        f"\nThis is a roll analysis. Current position close cost (mid): ${roll_close_cost:.2f}. "
        "NetCr = new premium minus close cost; positive = net credit roll."
        if roll_close_cost is not None
        else ""
    )

    prompt = f"""You are advising an options trader who sells {type_label} on stocks they own \
to collect premium, targeting LEAPS (1yr+) for long-term capital gains treatment on the premium.

Ticker: {ticker}, current price: ${spot:.2f}
{earn_text}{roll_context}

Top candidates ranked by IV excess above the fitted volatility surface:
{chr(10).join(rows_lines)}

Column notes: IV+pp = how many percentage points this option's IV exceeds the smooth surface \
fit (positive = IV-rich vs. peers — a ranking signal, not a mispricing claim). \
Ann% for calls = annualized yield vs. spot; for puts = vs. strike (capital at risk). \
Spread% = bid-ask spread as % of mid.

In 3-4 short paragraphs, cover:
1. Is there a meaningful IV outlier (significant IV+pp) or is the chain's IV roughly uniform?
2. What does the delta range say about assignment risk for the top candidates?
3. Which specific strike/expiration is the top-ranked candidate to sell, and why?
4. Key risks or caveats: earnings timing, bid-ask spread, liquidity, anything else notable.

Be direct and specific. No need to re-explain what columns mean."""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as exc:
        log.warning("Could not generate explanation: %s", exc)
        return None
