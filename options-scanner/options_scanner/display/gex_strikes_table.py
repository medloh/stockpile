"""Strikes-of-interest table for the GEX tab.

Ranks the chain's strongest pinning walls and amplification zones
by absolute net dealer gamma, surfaced as a small ranked table so
the user can spot exact strike levels alongside the GEX bar chart.

Also exports `fmt_strike_with_dist`, the compact "$X.XX (+1.2%)"
formatter the multi-ticker GEX summary table uses to keep each
strike cell single-line.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st


def fmt_strike_with_dist(strike: float | None, spot: float) -> str:
    """Format a strike alongside its % distance from spot.

    Keeps multi-ticker summary table cells compact — one cell per
    concept rather than splitting strike and distance across columns.
    Returns "—" when strike is missing/NaN.
    """
    if strike is None or pd.isna(strike):
        return "—"
    dist = (strike - spot) / spot * 100.0
    return f"${strike:,.2f} ({dist:+.1f}%)"


def show_gex_strikes_of_interest(df: pd.DataFrame, spot: float) -> None:
    """Render the top pinning walls + amp zones as a ranked table.

    Same per-strike GEX aggregation as `show_gex_chart`, surfaced as
    a tabular view for closer inspection. Picks the top 3 walls (most
    positive net GEX) and top 3 amp zones (most negative net GEX).
    """
    if df.empty or "gamma" not in df.columns:
        return

    spot_sq = spot * spot
    calls = df[df["type"] == "call"].copy()
    puts = df[df["type"] == "put"].copy()
    calls["gex"] = calls["gamma"] * calls["open_interest"] * 100 * spot_sq
    puts["gex"] = -puts["gamma"] * puts["open_interest"] * 100 * spot_sq

    per_strike = (
        pd.concat([calls[["strike", "gex", "open_interest"]],
                   puts[["strike", "gex", "open_interest"]]])
        .groupby("strike", as_index=False)
        .agg({"gex": "sum", "open_interest": "sum"})
    )
    if per_strike.empty or per_strike["gex"].abs().sum() == 0:
        return

    top_n = 3
    walls = per_strike[per_strike["gex"] > 0].nlargest(top_n, "gex")
    amps = per_strike[per_strike["gex"] < 0].nsmallest(top_n, "gex")

    rows = []
    for _, r in walls.iterrows():
        rows.append(("Pinning wall", r["strike"], r["gex"], r["open_interest"]))
    for _, r in amps.iterrows():
        rows.append(("Amp zone", r["strike"], r["gex"], r["open_interest"]))
    if not rows:
        return

    out = pd.DataFrame(rows, columns=["Tag", "Strike", "Net GEX", "Total OI"])
    out["Dist %"] = (out["Strike"] - spot) / spot * 100.0
    out = out[["Tag", "Strike", "Dist %", "Net GEX", "Total OI"]]
    out = out.sort_values("Net GEX", key=lambda s: s.abs(), ascending=False)

    st.subheader("Strikes of interest")
    st.caption(
        "**Pinning wall** — large positive dealer gamma at this "
        "strike. Price tends to gravitate here (resistance for moves "
        "up, support for moves down). Favorable for covered-call "
        "strikes just below a wall.  "
        "**Amp zone** — large negative dealer gamma. Moves through "
        "this strike tend to accelerate; sellers should size cautiously."
    )
    st.dataframe(
        out, hide_index=True, use_container_width=False,
        column_config={
            "Tag":      st.column_config.TextColumn(),
            "Strike":   st.column_config.NumberColumn(format="$%.2f"),
            "Dist %":   st.column_config.NumberColumn(format="%+.2f%%"),
            "Net GEX":  st.column_config.NumberColumn(format="%,.0f"),
            "Total OI": st.column_config.NumberColumn(format="%,d"),
        },
    )
