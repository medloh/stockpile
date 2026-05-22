"""Per-ticker Gamma Exposure (GEX) summary: total net GEX, regime,
zero-gamma flip, strongest pinning wall, and strongest amp zone.

Same math the GEX chart uses, distilled to single numbers so the
multi-ticker GEX summary table can rank tickers without re-rendering
the full chart for each one.

Convention: GEX is the dealer's net gamma exposure. Calls contribute
positive gamma (dealers long via market-making → short to hedge);
puts contribute negative gamma (dealers short via market-making →
long to hedge). Net positive GEX = "pinning" regime (price reverts
to the wall); net negative = "amplifying" regime (price runs).
"""

from __future__ import annotations

import pandas as pd


def compute_gex_summary(df: pd.DataFrame, spot: float) -> dict | None:
    """Reduce a GEX-enriched chain to one summary dict per ticker.

    Args:
        df: Chain DataFrame with `type`, `strike`, `open_interest`, and
            `gamma` columns.
        spot: Current underlying spot price.

    Returns:
        Dict with keys:
            total_gex:  Net sum of per-strike GEX across calls + puts.
            regime:     "Pinning" when total_gex >= 0, else "Amplifying".
            zero_gamma: Strike where cumulative GEX crosses zero
                        (NaN if cumulative never reaches zero).
            top_wall:   Strike with the largest positive net GEX, or
                        None when no strike has positive net GEX.
            top_amp:    Strike with the largest negative net GEX, or
                        None when no strike has negative net GEX.

        Returns None when the input is empty, lacks a gamma column, or
        has zero total absolute GEX (degenerate chain).
    """
    if df.empty or "gamma" not in df.columns:
        return None
    spot_sq = spot * spot
    calls = df[df["type"] == "call"].copy()
    puts = df[df["type"] == "put"].copy()
    calls["gex"] = calls["gamma"] * calls["open_interest"] * 100 * spot_sq
    puts["gex"] = -puts["gamma"] * puts["open_interest"] * 100 * spot_sq
    per_strike = (
        pd.concat([calls[["strike", "gex"]], puts[["strike", "gex"]]])
        .groupby("strike", as_index=False)["gex"].sum()
        .sort_values("strike")
    )
    if per_strike.empty or per_strike["gex"].abs().sum() == 0:
        return None

    total_gex = float(per_strike["gex"].sum())
    cumulative = per_strike["gex"].cumsum()
    zero_strikes = per_strike["strike"][cumulative >= 0]
    zero_gamma = (float(zero_strikes.min())
                  if not zero_strikes.empty else float("nan"))

    walls = per_strike[per_strike["gex"] > 0]
    amps = per_strike[per_strike["gex"] < 0]
    top_wall = (float(walls.loc[walls["gex"].idxmax(), "strike"])
                if not walls.empty else None)
    top_amp = (float(amps.loc[amps["gex"].idxmin(), "strike"])
               if not amps.empty else None)

    return {
        "total_gex": total_gex,
        "regime": "Pinning" if total_gex >= 0 else "Amplifying",
        "zero_gamma": zero_gamma,
        "top_wall": top_wall,
        "top_amp": top_amp,
    }
