"""Spread payoff diagram: dual-line Altair chart showing at-expiry and
current Black-Scholes P&L curves with breakeven and spot reference
lines.

Called from the Spreads / Directional / Neutral tabs when the user
clicks a row in the ranked spread table. The row carries strategy
metadata (legs, expiration, breakeven prices, POP) used to render
the title and breakeven rules.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from options_scanner.display.scan_stamp import scan_stamp_color, scan_stamp_text


def show_payoff_chart(row: pd.Series, spot: float) -> None:
    """Render the P&L curve for a selected spread row.

    Args:
        row: One row from the ranked spreads DataFrame; must carry
            strategy/expiration/dte/breakeven1/breakeven2/pop fields.
        spot: Current underlying spot, drawn as a dashed vertical
            reference.
    """
    # spreads.* live below run_app.py's sys.path entry — inline import
    # keeps the cold-start cheap and avoids a top-level cycle while
    # display/ is being assembled.
    from options_scanner.spreads import spread_payoff_data, build_legs_from_row

    legs = build_legs_from_row(row)
    if not legs:
        return
    T = max(int(row["dte"]), 1) / 365.0
    data = spread_payoff_data(legs, spot, T)

    # Melt to long form for Altair
    melted = data.melt("price", var_name="line", value_name="pl")
    melted["line"] = melted["line"].map(
        {"pl_expiry": "At Expiration", "pl_current": "Current Value (BS)"}
    )

    # Shaded area: green above 0, red below 0 — use two area layers
    zero_line = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
        color="#475569", strokeDash=[3, 3], size=1
    ).encode(y="y:Q")

    spot_rule = alt.Chart(pd.DataFrame({"x": [spot]})).mark_rule(
        color="#0f172a", strokeDash=[4, 4], size=1.5
    ).encode(x="x:Q")

    # Breakeven rules
    be_rules = []
    for be_col, color in [("breakeven1", "#f97316"), ("breakeven2", "#f97316")]:
        be_val = row.get(be_col)
        if be_val and not pd.isna(be_val):
            be_rules.append(
                alt.Chart(pd.DataFrame({"x": [float(be_val)]})).mark_rule(
                    color=color, strokeDash=[5, 3], size=1.5
                ).encode(x="x:Q")
            )

    color_scale = alt.Scale(
        domain=["At Expiration", "Current Value (BS)"],
        range=["#0f172a", "#94a3b8"],
    )
    dash_scale = alt.Scale(
        domain=["At Expiration", "Current Value (BS)"],
        range=[[1, 0], [6, 3]],
    )

    lines = alt.Chart(melted).mark_line(size=2).encode(
        x=alt.X("price:Q", title="Stock Price", axis=alt.Axis(format="$,.0f")),
        y=alt.Y("pl:Q", title="P&L per share ($)", axis=alt.Axis(format="$.2f")),
        color=alt.Color("line:N", scale=color_scale,
                        legend=alt.Legend(title=None, orient="top-left")),
        strokeDash=alt.StrokeDash("line:N", scale=dash_scale, legend=None),
    )

    strategy = row.get("strategy", "Spread")
    exp = row.get("expiration", "")
    pop_pct = f"{row.get('pop', 0):.0%}"
    title = f"{strategy} — {exp} — POP {pop_pct}"

    chart = (zero_line + spot_rule + lines)
    for r in be_rules:
        chart = chart + r
    chart = chart.properties(
        height=300,
        title=alt.TitleParams(
            text=title,
            subtitle=scan_stamp_text() or None,
            subtitleColor=scan_stamp_color(),
            subtitleFontSize=11,
            fontSize=14, fontWeight="bold",
            anchor="start", color="#0f172a",
        ),
    )
    st.altair_chart(chart, use_container_width=True)
    be_note = []
    be1 = row.get("breakeven1")
    be2 = row.get("breakeven2")
    if be1 and not pd.isna(be1):
        be_note.append(f"BE₁ ${float(be1):.2f}")
    if be2 and not pd.isna(be2):
        be_note.append(f"BE₂ ${float(be2):.2f}")
    if be_note:
        st.caption(f"Orange dashed lines mark breakevens: {', '.join(be_note)}. "
                   "Dashed gray = current BS value assuming constant IV.")
