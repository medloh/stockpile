"""Gamma Exposure (GEX) bar chart by strike.

Aggregates per-strike dealer gamma across all expirations in the
current scan and renders it as a colored bar chart with the spot
overlay, summary metrics row (Total GEX / Regime / Zero-gamma level),
and a DTE-scope footnote so screenshots stay self-documenting.

Convention matches `compute.gex_summary`: calls contribute positive
gamma (pinning), puts negative (amplifying). Net positive total =
mean-reverting regime; net negative = trending regime.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from options_scanner.display.scan_stamp import scan_stamp_color, scan_stamp_text


def show_gex_chart(df: pd.DataFrame, spot: float,
                   provider: str = "yahoo",
                   ticker: str = "") -> None:
    """Render the GEX bar chart + summary metrics for the supplied chain.

    Args:
        df: Chain DataFrame with `type`, `strike`, `open_interest`,
            `gamma`, and (optionally) `dte` / `expiration` columns.
        spot: Current underlying spot — drawn as a dashed vertical
            reference line and used to zoom the x-axis to the
            meaningful strike range.
        provider: "yahoo" or "schwab" — surfaces the right caveat
            footnote about IV freshness.
        ticker: Optional ticker symbol prepended to the chart title
            for screenshot-friendly context. Falls back to the bare
            chart name when omitted.
    """
    if df.empty or "gamma" not in df.columns:
        return

    spot_sq = spot * spot

    calls = df[df["type"] == "call"].copy()
    puts = df[df["type"] == "put"].copy()

    calls["gex"] = calls["gamma"] * calls["open_interest"] * 100 * spot_sq
    puts["gex"] = -puts["gamma"] * puts["open_interest"] * 100 * spot_sq

    gex = (
        pd.concat([calls[["strike", "gex"]], puts[["strike", "gex"]]])
        .groupby("strike", as_index=False)["gex"]
        .sum()
        .sort_values("strike")
    )

    if gex.empty or gex["gex"].abs().sum() == 0:
        return

    total_gex = gex["gex"].sum()
    gex["color"] = gex["gex"].apply(lambda v: "Pinning" if v >= 0 else "Amplifying")

    # Zero-gamma level: strike where cumulative GEX crosses zero
    gex_sorted = gex.sort_values("strike")
    cumulative = gex_sorted["gex"].cumsum()
    zero_cross = gex_sorted["strike"][cumulative >= 0].min()

    g1, g2, g3 = st.columns(3)
    regime = "Pinning (mean-reverting)" if total_gex >= 0 else "Amplifying (trending)"
    g1.metric("Total GEX", f"{total_gex:,.0f}", help=(
        "Positive = dealers net long gamma across this chain — price "
        "tends to mean-revert. Negative = dealers net short gamma — "
        "moves tend to be amplified."
    ))
    g2.metric("Regime", regime)
    if not pd.isna(zero_cross):
        g3.metric("Zero-gamma level", f"${zero_cross:,.2f}", help=(
            "Strike where cumulative dealer gamma flips sign. "
            "Price above this level tends to be more volatile."
        ))

    # Zoom the x-axis to the strikes that actually carry GEX. Chains
    # often include far-OTM strikes with near-zero gamma — leaving them
    # in shrinks the meaningful bars to a sliver in the middle. Take
    # the smallest contiguous strike range that holds ~99% of total
    # |GEX|, ensure spot is included, then pad ~3% on each side.
    gex_sorted_abs = gex.assign(abs_gex=gex["gex"].abs()) \
                        .sort_values("abs_gex", ascending=False)
    total_abs = float(gex_sorted_abs["abs_gex"].sum())
    if total_abs > 0:
        cum = gex_sorted_abs["abs_gex"].cumsum() / total_abs
        core = gex_sorted_abs[cum <= 0.99]
        if core.empty:
            core = gex_sorted_abs.head(1)
        core_lo = float(core["strike"].min())
        core_hi = float(core["strike"].max())
    else:
        core_lo = float(gex["strike"].min())
        core_hi = float(gex["strike"].max())

    x_min = min(core_lo, spot) * 0.97
    x_max = max(core_hi, spot) * 1.03

    # Trim the dataframe to the zoom range so bars rescale to fill the
    # chart width (Altair's `scale=domain=` alone just clips off-screen
    # bars without expanding the in-range ones).
    gex_zoomed = gex[(gex["strike"] >= x_min) & (gex["strike"] <= x_max)]
    if gex_zoomed.empty:
        gex_zoomed = gex
    y_max_gex = float(gex_zoomed["gex"].max())

    bars = alt.Chart(gex_zoomed).mark_bar(opacity=0.85).encode(
        x=alt.X("strike:Q", title="Strike",
                scale=alt.Scale(domain=[x_min, x_max]),
                axis=alt.Axis(format="$,.0f")),
        y=alt.Y("gex:Q", title="Net GEX ($)"),
        color=alt.Color("color:N",
                        scale=alt.Scale(
                            domain=["Pinning", "Amplifying"],
                            range=["#22c55e", "#ef4444"],
                        ),
                        legend=alt.Legend(title=None)),
        tooltip=[
            alt.Tooltip("strike:Q",  title="Strike",  format="$,.0f"),
            alt.Tooltip("gex:Q",     title="Net GEX", format=",.0f"),
            alt.Tooltip("color:N",   title="Effect"),
        ],
    )

    spot_df = pd.DataFrame({"x": [spot], "y": [y_max_gex],
                            "label": [f"Spot ${spot:.2f}"]})
    spot_rule = alt.Chart(spot_df).mark_rule(
        color="#0f172a", strokeDash=[3, 3], strokeWidth=1.5,
    ).encode(
        x=alt.X("x:Q", scale=alt.Scale(domain=[x_min, x_max])),
    )
    spot_label = alt.Chart(spot_df).mark_text(
        align="left", baseline="top", dx=5, dy=2,
        color="#0f172a", fontWeight="bold", fontSize=11,
    ).encode(
        x=alt.X("x:Q", scale=alt.Scale(domain=[x_min, x_max])),
        y="y:Q",
        text="label:N",
    )

    # Build a screenshot-friendly title: ticker first, then chart type.
    # Falls back to just the chart name if no ticker is passed.
    title_text = (f"{ticker} — Gamma Exposure (GEX) by strike"
                  if ticker else "Gamma Exposure (GEX) by strike")

    # DTE scope footnote so screenshots taken days later still convey
    # which slice of the chain the bars are summed over.
    if "dte" in df.columns and not df["dte"].empty:
        dte_lo = int(df["dte"].min())
        dte_hi = int(df["dte"].max())
        n_exp = int(df["expiration"].nunique())
        dte_note = (f"Aggregated across {n_exp} expiration"
                    f"{'s' if n_exp != 1 else ''} "
                    f"({dte_lo}–{dte_hi} DTE).")
    else:
        dte_note = "Aggregated across all expirations in the current scan."

    st.altair_chart(
        (bars + spot_rule + spot_label).properties(
            height=240,
            title=alt.TitleParams(
                text=title_text,
                subtitle=scan_stamp_text() or None,
                subtitleColor=scan_stamp_color(),
                subtitleFontSize=11,
                fontSize=14, fontWeight="bold", anchor="start",
                color="#0f172a",
            ),
        ).configure_view(strokeWidth=0),
        use_container_width=True,
    )

    provider_caveat = (
        "GEX estimated from Black-Scholes gamma (Yahoo IV may be stale on LEAPS)."
        if provider == "yahoo"
        else "GEX computed from Schwab's native gamma values."
    )
    st.caption(f"{dte_note} {provider_caveat}")
