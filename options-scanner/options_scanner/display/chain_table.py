"""Per-expiration chain table.

Renders every option at one expiration, sorted by strike, with row
background shading driven by IV+pp signal strength. The user clicks
through expirations in the Single Ticker tab's chain accordion;
each accordion body calls this once with the expiration's slice of
the chain.

Shading semantics:
- IV+pp at or above the +3 pp signal threshold → green tinted by
  intensity relative to the table's max positive signal.
- IV+pp at or below the −3 pp threshold → red tinted similarly.
- All-noise tables (every row within ±3 pp) get a faint neutral
  background plus a single best/worst row called out — gives the
  user a relative comparison even when nothing is "signal".
- Cell-level overrides from the row-highlight masks (wide spread,
  low OI, low volume) layer on top of the row shading.
"""

from __future__ import annotations

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


def show_chain_table(df_exp: pd.DataFrame, buy: bool, mode: str,
                     roll_close_cost: float | None = None,
                     min_oi: int = 0, min_vol: int = 0,
                     top_ranks: dict[tuple[str, float, str], int]
                                | None = None,
                     ) -> None:
    """All options for one expiration, sorted by strike, rows shaded by IV+pp."""
    if df_exp.empty:
        empty_state(
            "No options for this expiration",
            "Filters removed every contract at this date. Lower min OI "
            "or relax the delta band to surface more rows.",
        )
        return

    df_s = df_exp.sort_values(["strike", "type"]).reset_index(drop=True)

    tr = top_ranks or {}
    rank_col = [
        str(tr.get((r["type"], float(r["strike"]), r["expiration"]), ""))
        for _, r in df_s.iterrows()
    ]

    cols: dict = {"Top": rank_col}
    if mode == "both":
        cols["Type"] = df_s["type"].str.capitalize()
    cols.update({
        "Strike": df_s["strike"].apply(lambda x: f"${x:.0f}"),
        "DTE":    df_s["dte"].astype(int),
        "Bid":    df_s["bid"].round(2),
        "Ask":    df_s["ask"].round(2),
        "Mid":    df_s["mid"].round(2),
        "IV%":    (df_s["iv"] * 100).round(1),
        "IV+pp":  (df_s["iv_excess"] * 100).round(1),
        "Delta":  df_s["delta"].round(2),
        "Ann%":   df_s["ann_yield_pct"].round(1),
        "OI":     df_s["open_interest"],
        "Vol":    df_s["volume"],
    })
    if roll_close_cost is not None:
        cols["NetCr"] = (df_s["mid"] - roll_close_cost).round(2)
    disp = pd.DataFrame(cols)

    # Row background: IV+pp signal vs 3pp noise floor.
    _NOISE = 0.03
    iv_vals = df_s["iv_excess"].tolist()
    signals = [-v if buy else v for v in iv_vals]

    all_noise = all(abs(v) < _NOISE for v in iv_vals)
    if all_noise:
        best_i  = signals.index(max(signals))
        worst_i = signals.index(min(signals))

    max_pos = max((s for s in signals if s >= _NOISE), default=_NOISE)
    max_neg = max((abs(s) for s in signals if s <= -_NOISE), default=_NOISE)

    def _row_bg(row: pd.Series) -> list[str]:
        i = int(row.name)
        s = signals[i]
        if all_noise:
            if i == best_i:
                bg = "background-color: rgba(34,197,94,0.40)"
            elif i == worst_i:
                bg = "background-color: rgba(239,68,68,0.40)"
            else:
                bg = "background-color: rgba(100,116,139,0.18)"
        elif s >= _NOISE:
            bg = f"background-color: rgba(34,197,94,{s/max_pos*0.50:.2f})"
        elif s <= -_NOISE:
            bg = f"background-color: rgba(239,68,68,{abs(s)/max_neg*0.45:.2f})"
        else:
            bg = "background-color: rgba(100,116,139,0.18)"
        return [bg] * len(row)

    # Cell-level overrides for spread, OI, and vol (applied after row bg).
    wide    = wide_spread_mask(df_s["bid"], df_s["ask"], df_s["mid"])
    lo      = low_oi_mask(df_s["open_interest"], min_oi)
    low_vol = low_vol_mask(df_s["volume"], min_vol)

    styled = (
        disp.style
        .apply(_row_bg, axis=1)
        .apply(lambda _: [CELL_WARN if w else "" for w in wide],
               subset=["Bid", "Ask"])
        .apply(lambda _: [CELL_WARN if l else "" for l in lo],
               subset=["OI"])
        .apply(lambda _: [CELL_WARN if v else "" for v in low_vol],
               subset=["Vol"])
    )

    col_cfg = {
        "Top":   st.column_config.TextColumn(
            "Top", width=50,
            help="Rank in the top candidates table below "
                 "(1 = strongest signal). Ranked per option type "
                 "after OI/Vol filters. Blank = not in top N.",
        ),
        "Type":  st.column_config.TextColumn("Type", width=60),
        "Strike": st.column_config.TextColumn("Strike", width=75),
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
                                               help=ivpp_help_for(buy, mode)),
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
