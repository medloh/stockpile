"""Ranked scan-results table renderer for the Single Ticker tab.

`show_scan_results` is the entry point — splits the input chain by
call/put, applies the OI/Vol filters, ranks each side, and delegates
the actual table render to `show_df`. `show_df` is also reused from
the Portfolio tab's per-position view.

The yellow row highlights and column tooltips come from
`display.chain_styling`; the source/timestamp caption below the
table comes from `display.scan_stamp.stamp_caption`.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from options_scanner.ui_theme import empty_state

from options_scanner.display.chain_styling import (
    BID_HELP,
    CELL_WARN,
    OI_HELP,
    VOL_HELP,
    ivpp_help_for,
    low_oi_mask,
    low_vol_mask,
    wide_spread_mask,
)
from options_scanner.display.scan_stamp import stamp_caption


def show_df(sub: pd.DataFrame, roll_close_cost: float | None = None,
            min_oi: int = 0, min_vol: int = 0,
            buy: bool = False, opt_type: str = "option") -> None:
    """Render the styled table for one option-type subset (or one
    per-position view from the Portfolio tab).

    Empty input renders an `empty_state` callout so the user knows
    the table didn't fail to load — it just has nothing to show.
    When `roll_close_cost` is supplied (roll-an-existing-position
    flow), an extra Net Credit column is appended.
    """
    if sub.empty:
        empty_state(
            "No matches in this chain",
            "Try widening the delta band, lowering min OI/Volume, or "
            "extending the DTE range.",
        )
        return

    disp = pd.DataFrame({
        "Strike": sub["strike"].apply(lambda x: f"${x:.0f}"),
        "Expiration": sub["expiration"].apply(
            lambda e: datetime.strptime(e, "%Y-%m-%d").strftime("%b %d '%y")
        ),
        "DTE":    sub["dte"].astype(int),
        "Bid":    sub["bid"].round(2),
        "Ask":    sub["ask"].round(2),
        "Mid":    sub["mid"].round(2),
        "IV%":    (sub["iv"] * 100).round(1),
        "IV+pp":  (sub["iv_excess"] * 100).round(1),
        "Delta":  sub["delta"].round(2),
        "Ann%":   sub["ann_yield_pct"].round(1),
        "OI":     sub["open_interest"],
        "Vol":    sub["volume"],
    })
    if roll_close_cost is not None:
        disp["NetCr"] = (sub["mid"] - roll_close_cost).round(2)

    wide = wide_spread_mask(sub["bid"], sub["ask"], sub["mid"])
    lo = low_oi_mask(sub["open_interest"], min_oi)
    low_vol = low_vol_mask(sub["volume"], min_vol)

    styled = (
        disp.style
        .apply(lambda _: [CELL_WARN if w else "" for w in wide],
               subset=["Bid", "Ask"])
        .apply(lambda _: [CELL_WARN if l else "" for l in lo],
               subset=["OI"])
        .apply(lambda _: [CELL_WARN if v else "" for v in low_vol],
               subset=["Vol"])
    )

    col_cfg = {
        "Strike":     st.column_config.TextColumn("Strike", width=75),
        "Expiration": st.column_config.TextColumn("Expiration", width=105),
        "DTE":   st.column_config.NumberColumn("DTE", format="%d", width=55),
        "Bid":   st.column_config.NumberColumn("Bid", format="$%.2f",
                                               width=70, help=BID_HELP),
        "Ask":   st.column_config.NumberColumn("Ask", format="$%.2f",
                                               width=70, help=BID_HELP),
        "Mid":   st.column_config.NumberColumn("Mid", format="$%.2f",
                                               width=70),
        "IV%":   st.column_config.NumberColumn("IV%", format="%.1f%%",
                                               width=70),
        "IV+pp": st.column_config.NumberColumn("IV+pp", format="%+.1f pp",
                                               width=75,
                                               help=ivpp_help_for(buy, opt_type)),
        "Delta": st.column_config.NumberColumn("Delta", format="%.2f",
                                               width=60),
        "Ann%":  st.column_config.NumberColumn("Ann%", format="%.1f%%",
                                               width=65),
        "OI":    st.column_config.NumberColumn("OI", format="%d",
                                               width=65, help=OI_HELP),
        "Vol":   st.column_config.NumberColumn("Vol", format="%d",
                                               width=65, help=VOL_HELP),
    }
    if roll_close_cost is not None:
        col_cfg["NetCr"] = st.column_config.NumberColumn("Net Credit",
                                                         format="$%+.2f",
                                                         width=85)

    st.dataframe(styled, column_config=col_cfg, hide_index=True,
                 width="stretch")
    stamp_caption()


def show_scan_results(df: pd.DataFrame, mode: str, buy: bool,
                      roll_close_cost: float | None,
                      min_oi: int, top_n: int,
                      min_vol: int = 0) -> None:
    """Filter, rank, and render the top-N per option type.

    Splits the chain by `mode` ("call", "put", or "both"), sorts by
    iv_excess (descending for sell mode, ascending for buy mode),
    applies the OI/Vol floors, takes the top N, and delegates to
    `show_df`. Adds a subheader when rendering both sides so the
    user knows which table is which.
    """
    iv_asc = buy
    type_labels = {"call": "Calls", "put": "Puts"}
    to_show = [mode] if mode in type_labels else list(type_labels.keys())

    for opt_type in to_show:
        sub = (
            df[df["type"] == opt_type]
            .sort_values(["iv_excess", "open_interest"], ascending=[iv_asc, False])
        )
        sub = sub[(sub["open_interest"] >= min_oi)
                  & (sub["volume"] >= min_vol)].head(top_n)
        if len(to_show) > 1:
            st.subheader(type_labels[opt_type])
        show_df(sub, roll_close_cost, min_oi, min_vol,
                buy=buy, opt_type=opt_type)
