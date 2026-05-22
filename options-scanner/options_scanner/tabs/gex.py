"""GEX tab: dealer-gamma exposure scanner across one or more tickers.

Multi-ticker mode renders a |Total GEX|-ranked summary and lets the
user drill into one ticker for the full GEX chart + strikes-of-
interest table. Single-ticker mode skips the summary and goes
straight to the chart.

Scope is fixed to the 0–60 DTE chain across both calls and puts —
GEX is most reliable on near-term OI; LEAPS GEX is too thin to be
trusted and is excluded.

This is diagnostic output, not a trade signal. See the README's
Gamma Exposure section for caveats.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import streamlit as st

from options_scanner.compute.gex_summary import compute_gex_summary
from options_scanner.display.gex_chart import show_gex_chart
from options_scanner.display.gex_strikes_table import (
    fmt_strike_with_dist,
    show_gex_strikes_of_interest,
)
from options_scanner.display.spot_meta import (
    fetch_spot_meta,
    spot_help_text,
    spot_value_html,
)
from options_scanner.fetch import fetch_and_enrich
from options_scanner.ui_theme import metric_card


def tab_gex() -> None:
    """GEX-only scanner: fetch near-term chains (0–60 DTE) for one or
    more tickers and surface dealer-gamma context (walls, amp zones,
    zero-gamma flip).

    Multi-ticker mode shows a summary table ranked by |Total GEX|;
    the user picks one ticker to drill into a full GEX chart and
    strikes-of-interest table.

    Diagnostic output, not a trade signal — see README's Gamma Exposure
    section for caveats.
    """
    with st.container(border=True):
        tc, sc, _ = st.columns([2, 1, 4], vertical_alignment="bottom")
        with tc:
            tickers_input = st.text_input(
                "Ticker(s) — comma-separated",
                "SPY",
                key="g_ticker",
                help=(
                    "One or more tickers, e.g. `SPY, QQQ, NVDA, AAPL`. "
                    "Multi-ticker mode adds a summary table you can "
                    "sort, then drill into one ticker for the full chart."
                ),
            )
        with sc:
            with st.container(key="gex_scan_btn_lift"):
                scanned = st.button("Scan", type="primary",
                                    use_container_width=True,
                                    key="g_scan_btn")

    st.caption(
        "Scans the **0–60 DTE** chain across both calls and puts. "
        "GEX is most reliable on near-term chains where OI is dense; "
        "LEAPS GEX is too thin to interpret and is excluded."
    )

    if scanned or st.session_state.pop("_gex_rescan_trigger", False):
        raw = tickers_input.strip().upper()
        tickers = [t.strip() for t
                   in raw.replace(";", ",").split(",")
                   if t.strip()]
        # Preserve user order, drop duplicates
        seen = set()
        tickers = [t for t in tickers
                   if not (t in seen or seen.add(t))]
        if not tickers:
            st.error("Enter one or more ticker symbols.")
            st.session_state.pop("gex_results", None)
            return

        per_ticker: dict[str, dict] = {}
        failed: list[tuple[str, str]] = []
        progress = st.progress(
            0.0, text=f"Fetching {len(tickers)} ticker(s)…"
        )
        for i, t in enumerate(tickers, 1):
            progress.progress(
                i / len(tickers),
                text=f"Fetching {t} ({i}/{len(tickers)})…",
            )
            df, earnings, err = fetch_and_enrich(
                t, "both", 0, 60,
                st.session_state.get("data_source", "yahoo"),
                st.session_state.get("schwab_config"),
            )
            if err:
                failed.append((t, err))
                continue
            if df.empty:
                failed.append((t, "no options in 0–60 DTE"))
                continue
            spot = float(df["spot"].iloc[0])
            summary = compute_gex_summary(df, spot)
            if summary is None:
                failed.append((t, "no GEX data (missing gamma/OI)"))
                continue
            per_ticker[t] = {"df": df, "spot": spot,
                             "earnings_dates": earnings, **summary}
        progress.empty()

        for t, msg in failed:
            st.warning(f"**{t}** skipped — {msg}")
        if not per_ticker:
            st.error("No tickers returned GEX data.")
            st.session_state.pop("gex_results", None)
            return

        st.session_state["scan_ts"] = datetime.now().astimezone()
        st.session_state["scan_provider"] = st.session_state.get(
            "data_source", "yahoo"
        )
        st.session_state["gex_results"] = {
            "tickers": list(per_ticker.keys()),
            "per_ticker": per_ticker,
        }

    res = st.session_state.get("gex_results")
    if not res:
        return

    per_ticker = res["per_ticker"]
    if not per_ticker:
        return

    # Build summary df sorted by |Total GEX| descending so the most
    # gamma-exposed ticker is the default drill-down pick.
    rows = []
    for t, info in per_ticker.items():
        spot = info["spot"]
        rows.append({
            "Ticker":    t,
            "Spot":      spot,
            "Total GEX": info["total_gex"],
            "Regime":    info["regime"],
            "Zero-Γ":    fmt_strike_with_dist(info["zero_gamma"], spot),
            "Top Wall":  fmt_strike_with_dist(info["top_wall"], spot),
            "Top Amp":   fmt_strike_with_dist(info["top_amp"], spot),
        })
    summary_df = pd.DataFrame(rows)
    summary_df = (summary_df
                  .assign(_abs=summary_df["Total GEX"].abs())
                  .sort_values("_abs", ascending=False)
                  .drop(columns=["_abs"])
                  .reset_index(drop=True))

    st.divider()

    n = len(per_ticker)
    rescan_label = (f"↻ Rescan {res['tickers'][0]}"
                    if n == 1 else f"↻ Rescan ({n})")
    with st.container(key="rescan_pill_gex"):
        if st.button(rescan_label, type="primary", key="g_rescan_btn"):
            st.session_state["_gex_rescan_trigger"] = True
            st.rerun()

    if n > 1:
        st.subheader("GEX summary")
        st.caption(
            "One row per ticker, sorted by absolute Total GEX (most "
            "dealer-gamma exposure first). The Zero-Γ, Top Wall, and "
            "Top Amp cells include each strike's distance from spot."
        )
        st.dataframe(
            summary_df, hide_index=True, use_container_width=False,
            column_config={
                "Ticker":    st.column_config.TextColumn(),
                "Spot":      st.column_config.NumberColumn(format="$%.2f"),
                "Total GEX": st.column_config.NumberColumn(format="%,.0f"),
                "Regime":    st.column_config.TextColumn(),
                "Zero-Γ":    st.column_config.TextColumn(),
                "Top Wall":  st.column_config.TextColumn(),
                "Top Amp":   st.column_config.TextColumn(),
            },
        )

        drill = st.selectbox(
            "Drill into ticker",
            summary_df["Ticker"].tolist(),
            index=0,
            key="g_drill",
        )
        st.divider()
    else:
        drill = res["tickers"][0]

    info = per_ticker[drill]
    df_r = info["df"]
    spot = info["spot"]

    if n == 1:
        m1, m2, m3 = st.columns(3)
        with m1:
            _meta = fetch_spot_meta(
                drill, st.session_state.get("scan_provider", "yahoo"),
            )
            metric_card("SPOT",
                        spot_value_html(spot, _meta["pct_change"]),
                        help_text=spot_help_text(_meta))
        with m2:
            metric_card("EXPIRATIONS",
                        f"{df_r['expiration'].nunique()}",
                        help_text="0–60 DTE")
        with m3:
            _earnings = info.get("earnings_dates") or []
            if _earnings:
                _earn_days = (_earnings[0] - date.today()).days
                _earn_label = _earnings[0].strftime("%b %d")
                _earn_sub   = f"in {_earn_days}d"
            else:
                _earn_label = "—"
                _earn_sub   = "no upcoming events"
            metric_card("NEXT EARNINGS", _earn_label,
                        delta=_earn_sub, delta_sign="neutral")
        st.divider()

    show_gex_chart(df_r, spot,
                    provider=st.session_state.get("scan_provider", "yahoo"),
                    ticker=drill)

    show_gex_strikes_of_interest(df_r, spot)
