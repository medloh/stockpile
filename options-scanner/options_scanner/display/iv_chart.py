"""Per-expiration volatility-surface chart with top-N pick callouts.

Renders the chain at one chosen expiration as a smile of IV dots,
with the fitted surface drawn as a dashed line, the table's top
picks highlighted (large outlined dots with rank labels above), and
a spot reference rule. Used by Single Ticker and Portfolio tabs;
both pass in the full multi-expiration chain and the chart's own
selectbox picks the expiration to render.

The pick highlighting and ranking come from `compute.top_ranks` —
the same function the bottom table uses — so chart and table never
disagree about who's rank 1.
"""

from __future__ import annotations

from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st

from options_scanner.compute.top_ranks import compute_top_ranks
from options_scanner.display.scan_stamp import scan_stamp_color, scan_stamp_text


def show_iv_chart(df: pd.DataFrame, spot: float, mode: str,
                  min_oi: int, top_n: int, buy: bool,
                  ticker: str = "", key_prefix: str = "s",
                  min_vol: int = 0) -> None:
    """Layered chart: per-expiration smile with the table's top-N picks
    highlighted. Faded background dots are the rest of the chain at
    the selected expiration; bright outlined dots are the picks."""
    if df.empty:
        return

    chart_df = df.copy()
    if mode in ("call", "put"):
        chart_df = chart_df[chart_df["type"] == mode]
    if chart_df.empty:
        return

    top_ranks = compute_top_ranks(
        chart_df, mode, buy, min_oi, top_n, min_vol,
    )
    chart_df["is_top"] = chart_df.apply(
        lambda r: (r["type"], float(r["strike"]), r["expiration"]) in top_ranks,
        axis=1,
    )
    chart_df["rank_label"] = chart_df.apply(
        lambda r: str(top_ranks.get(
            (r["type"], float(r["strike"]), r["expiration"]), ""
        )),
        axis=1,
    )
    chart_df["IV%"]        = (chart_df["iv"] * 100).round(2)
    chart_df["FittedIV%"]  = (chart_df["iv_fitted"] * 100).round(2)
    chart_df["IV+pp"]      = (chart_df["iv_excess"] * 100).round(2)
    chart_df["Ann%"]       = chart_df["ann_yield_pct"].round(2)
    exp_dte = chart_df.groupby("expiration")["dte"].first().to_dict()
    chart_df["ExpLabel"] = chart_df["expiration"].apply(
        lambda d: f"{datetime.strptime(d, '%Y-%m-%d').strftime('%b %d \'%y')} ({exp_dte.get(d, 0)}d)"
    )

    expirations = sorted(chart_df["expiration"].unique())
    exp_labels  = {
        e: f"{datetime.strptime(e, '%Y-%m-%d').strftime('%b %d \'%y')} — {exp_dte.get(e, 0)}d"
        for e in expirations
    }
    pick_counts = {
        e: int(chart_df[(chart_df["expiration"] == e)
                        & chart_df["is_top"]].shape[0])
        for e in expirations
    }
    # Default to the expiration containing the strongest signal — the
    # pick with the highest IV+pp (or lowest, in buy mode). Falls back
    # to the first expiration if there are no picks for some reason.
    picks_df = chart_df[chart_df["is_top"]]
    if not picks_df.empty:
        extreme_idx = (picks_df["iv_excess"].idxmin() if buy
                       else picks_df["iv_excess"].idxmax())
        default_exp = picks_df.loc[extreme_idx, "expiration"]
        default_idx = expirations.index(default_exp)
    else:
        default_idx = 0

    # Header row: title on the left, expiration selector on the right
    h1, h2 = st.columns([1, 2], vertical_alignment="bottom")
    with h1:
        # Bottom margin lifts the heading 5px up relative to the
        # selectbox in the bottom-aligned column row.
        st.markdown(
            "<h5 style='margin:0 0 5px 0'>Volatility surface</h5>",
            unsafe_allow_html=True,
        )
    with h2:
        chosen_exp = st.selectbox(
            "Expiration to chart",
            options=expirations,
            index=default_idx,
            format_func=lambda d: f"{exp_labels[d]}  ({pick_counts[d]} pick"
                                  f"{'s' if pick_counts[d] != 1 else ''})",
            key=f"{key_prefix}_chart_exp",
            help="Each expiration has its own volatility smile. The number "
                 "in parentheses is how many of the table's top picks live "
                 "at that expiration.",
            label_visibility="collapsed",
        )

    sub = chart_df[chart_df["expiration"] == chosen_exp].sort_values(
        ["type", "strike"]
    )
    if sub.empty:
        return

    excess_max = max(abs(sub["IV+pp"].min()), abs(sub["IV+pp"].max()), 1.0)
    # Green = attractive (high IV+pp to sell; low IV+pp to buy); red = unattractive.
    # Flip the range in buy mode so the color always agrees with the table shading.
    if buy:
        color_range = ["#22c55e", "#cbd5e1", "#ef4444"]  # negative=green, positive=red
    else:
        color_range = ["#ef4444", "#cbd5e1", "#22c55e"]  # negative=red, positive=green
    color_scale = alt.Scale(
        domain=[-excess_max, 0, excess_max],
        range=color_range,
    )
    shape_scale = alt.Scale(domain=["call", "put"],
                            range=["circle", "square"])

    # X-domain extended so the spot line is always inside the visible range
    x_min = min(float(sub["strike"].min()), spot) * 0.97
    x_max = max(float(sub["strike"].max()), spot) * 1.03
    y_max = float(sub[["IV%", "FittedIV%"]].values.max())

    base_x = alt.X(
        "strike:Q", title="Strike",
        scale=alt.Scale(domain=[x_min, x_max]),
        axis=alt.Axis(format="$,.0f"),
    )

    tooltip_fields = [
        alt.Tooltip("strike:Q",        title="Strike", format="$,.0f"),
        alt.Tooltip("type:N",          title="Type"),
        alt.Tooltip("IV%:Q",           format=".1f"),
        alt.Tooltip("FittedIV%:Q",     title="Fitted IV%", format=".1f"),
        alt.Tooltip("IV+pp:Q",         title="IV excess (pp)", format="+.1f"),
        alt.Tooltip("delta:Q",         format=".2f"),
        alt.Tooltip("Ann%:Q",          title="Ann%", format=".1f"),
        alt.Tooltip("volume:Q",        title="Volume", format=",.0f"),
        alt.Tooltip("open_interest:Q", title="OI"),
        alt.Tooltip("bid:Q",           title="Bid",  format="$.2f"),
        alt.Tooltip("ask:Q",           title="Ask",  format="$.2f"),
    ]

    fitted_line = alt.Chart(sub).mark_line(
        color="#94a3b8", strokeDash=[4, 3], size=2,
    ).encode(
        x=base_x,
        y=alt.Y("FittedIV%:Q", title="Implied Volatility (%)"),
        detail="type:N",
    )

    background = alt.Chart(sub[~sub["is_top"]]).mark_circle(
        size=60, opacity=1.0,
    ).encode(
        x=base_x,
        y="IV%:Q",
        color=alt.Color("IV+pp:Q", scale=color_scale,
                        legend=alt.Legend(title="IV excess (pp)")),
        shape=alt.Shape("type:N", scale=shape_scale,
                        legend=alt.Legend(title="Type")),
        tooltip=tooltip_fields,
    )

    picks = alt.Chart(sub[sub["is_top"]]).mark_point(
        size=260, opacity=1.0, filled=True,
        stroke="#0f172a", strokeWidth=2,
    ).encode(
        x=base_x,
        y="IV%:Q",
        color=alt.Color("IV+pp:Q", scale=color_scale, legend=None),
        shape=alt.Shape("type:N", scale=shape_scale, legend=None),
        tooltip=tooltip_fields,
    )

    # Rank badge above each pick — shows where this option sits in
    # the top-N list per type (1 = strongest signal). Same ordering
    # as the bottom table, so the user can match chart picks to table
    # rows at a glance.
    ranks = alt.Chart(sub[sub["is_top"]]).mark_text(
        fontSize=14, dy=-20, fontWeight="bold",
        color="#0f172a",
    ).encode(
        x=base_x,
        y="IV%:Q",
        text="rank_label:N",
    )

    spot_df = pd.DataFrame({"x": [spot], "y": [y_max],
                            "label": [f"Spot ${spot:.2f}"]})
    spot_rule = alt.Chart(spot_df).mark_rule(
        color="#0f172a", strokeDash=[3, 3], size=2,
    ).encode(
        x=alt.X("x:Q", scale=alt.Scale(domain=[x_min, x_max])),
        tooltip=[alt.Tooltip("x:Q", title="Spot", format="$,.2f")],
    )
    spot_label = alt.Chart(spot_df).mark_text(
        align="left", baseline="top", dx=5, dy=2,
        color="#0f172a", fontWeight="bold", fontSize=11,
    ).encode(
        x=alt.X("x:Q", scale=alt.Scale(domain=[x_min, x_max])),
        y="y:Q",
        text="label:N",
    )

    type_word = {"call": "calls", "put": "puts", "both": "options"}[mode]
    title_text = (f"{ticker} {type_word} — {exp_labels[chosen_exp]}"
                  if ticker else f"{type_word} — {exp_labels[chosen_exp]}")
    chart = (
        fitted_line + background + picks + ranks + spot_rule + spot_label
    ).properties(
        height=380,
        title=alt.TitleParams(
            text=title_text,
            subtitle=scan_stamp_text() or None,
            subtitleColor=scan_stamp_color(),
            subtitleFontSize=11,
            fontSize=16, fontWeight="bold", anchor="start",
            color="#0f172a",
        ),
    )
    st.altair_chart(chart, use_container_width=True)
    st.caption(
        "Dashed gray line is the fitted volatility surface for this "
        "expiration. **Larger outlined dots with a number above them "
        "are the top picks — the number is the rank in the table "
        "below (1 = strongest signal, ranked per type).** Faded dots "
        "are the rest of the chain at this expiration for context. "
        "Green = attractive premium (rich to sell / cheap to buy), "
        "red = unattractive. Vertical dashed line marks the current "
        "spot price."
    )
