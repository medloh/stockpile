"""Rank options by IV+pp (or IV−pp in buy mode) and return position
labels for the top N picks.

Used by the IV-surface chart (to label dots), the per-expiration
chain table (to populate the Top column), and the scan-results
table — they all need the same ranking convention so a "1" in the
chart and a "1" in the table refer to the same contract.
"""

from __future__ import annotations

import pandas as pd


def compute_top_ranks(df: pd.DataFrame, mode: str, buy: bool,
                      min_oi: int, top_n: int,
                      min_vol: int = 0,
                      ) -> dict[tuple[str, float, str], int]:
    """Return {(type, strike, expiration): rank} for top-N candidates.

    Rank is 1-indexed per option type. Selling ranks by descending
    iv_excess (richest first); buying ranks by ascending iv_excess
    (cheapest first). open_interest is the tiebreaker.

    Args:
        df: Chain DataFrame with columns `type`, `strike`, `expiration`,
            `iv_excess`, `open_interest`, `volume`.
        mode: `"call"`, `"put"`, or `"both"`.
        buy: If True, sort ascending (cheap first); else descending.
        min_oi: Drop rows with open_interest below this floor.
        top_n: Keep at most this many per type.
        min_vol: Drop rows with today's volume below this floor.

    Returns:
        Empty dict when df is empty. Otherwise maps each top pick's
        identity tuple to its 1-indexed rank within its type.
    """
    if df.empty:
        return {}
    iv_asc = buy
    pick_types = ["call", "put"] if mode == "both" else [mode]
    ranks: dict[tuple[str, float, str], int] = {}
    for t in pick_types:
        ranked = (
            df[(df["type"] == t)
               & (df["open_interest"] >= min_oi)
               & (df["volume"] >= min_vol)]
            .sort_values(["iv_excess", "open_interest"],
                         ascending=[iv_asc, False])
            .head(top_n)
            .reset_index(drop=True)
        )
        for i, r in ranked.iterrows():
            ranks[(r["type"], float(r["strike"]), r["expiration"])] = i + 1
    return ranks
