"""Summary metrics computed from a vector of terminal P&L outcomes."""
from __future__ import annotations

import numpy as np


def summarize(
    terminal_pnl: np.ndarray,
    terminal_spot: np.ndarray,
    spot: float,
) -> dict[str, float]:
    """Compute the metrics surfaced in the MC Analyze panel.

    Args:
        terminal_pnl: (n_paths,) $ P&L per path at horizon.
        terminal_spot: (n_paths,) underlying spot at horizon.
        spot: Spot at simulation start (t=0).

    Returns:
        Dict with the keys consumed by the UI:
            prob_profit         — fraction of paths with P&L > 0 (0..1)
            expected_pnl        — mean P&L in dollars
            cvar_5pct           — average P&L of the worst 5% of paths
                                  (negative for loss; never positive)
            breakeven_move_pct  — signed percent move in spot from t=0
                                  to the median break-even path
            pop_95_low          — 2.5th percentile of terminal spot
            pop_95_high         — 97.5th percentile of terminal spot
            median_pnl, std_pnl — additional summary stats for the histogram
    """
    n = terminal_pnl.size
    if n == 0:
        raise ValueError("empty terminal_pnl array")

    prob_profit = float(np.mean(terminal_pnl > 0))
    expected_pnl = float(np.mean(terminal_pnl))
    median_pnl = float(np.median(terminal_pnl))
    std_pnl = float(np.std(terminal_pnl))

    # CVaR at the 5% tail: mean of the worst 5% of P&L outcomes.
    k = max(1, n // 20)
    sorted_pnl = np.sort(terminal_pnl)
    cvar_5pct = float(np.mean(sorted_pnl[:k]))

    # Breakeven move: the spot move needed (in %) such that paths landing at
    # that spot produce zero P&L on average. We approximate by binning P&L
    # against spot and finding the spot bucket whose mean P&L is closest to 0.
    # Falls back to 0% if all paths are profitable or all losses.
    if terminal_pnl.min() > 0 or terminal_pnl.max() < 0:
        breakeven_move_pct = 0.0
    else:
        # Sort by spot, compute rolling mean of P&L, find first crossing of 0.
        order = np.argsort(terminal_spot)
        sorted_spot = terminal_spot[order]
        sorted_pnl_by_spot = terminal_pnl[order]
        # Smooth by quintile to suppress noise.
        n_bins = min(50, n // 20)
        if n_bins < 2:
            breakeven_move_pct = 0.0
        else:
            bin_size = n // n_bins
            bin_spot = np.array([
                sorted_spot[i * bin_size : (i + 1) * bin_size].mean()
                for i in range(n_bins)
            ])
            bin_pnl = np.array([
                sorted_pnl_by_spot[i * bin_size : (i + 1) * bin_size].mean()
                for i in range(n_bins)
            ])
            # Find sign-changes in bin_pnl; pick the spot at the first crossing.
            signs = np.sign(bin_pnl)
            crossings = np.where(np.diff(signs) != 0)[0]
            if crossings.size == 0:
                breakeven_move_pct = 0.0
            else:
                i = int(crossings[0])
                # Linear-interpolate between bins[i] and bins[i+1] for the
                # zero-crossing spot.
                p0, p1 = bin_pnl[i], bin_pnl[i + 1]
                s0, s1 = bin_spot[i], bin_spot[i + 1]
                breakeven_spot = s0 - p0 * (s1 - s0) / (p1 - p0) if p1 != p0 else s0
                breakeven_move_pct = float((breakeven_spot - spot) / spot * 100.0)

    pop_95_low = float(np.percentile(terminal_spot, 2.5))
    pop_95_high = float(np.percentile(terminal_spot, 97.5))

    # Value at Risk @ 5% — the threshold loss exceeded in 5% of paths.
    # CVaR is the *average* beyond that threshold, so VaR ≤ CVaR by construction.
    # Reference: Glasserman §9.
    var_5pct = float(sorted_pnl[k - 1])

    # Sortino ratio — Sharpe-analog that punishes downside only. The right
    # ratio for option positions because payoffs are deliberately asymmetric.
    # We compute against MAR=0 (the breakeven). Annualization is omitted
    # because the simulation horizon is fixed; this is a per-position ratio,
    # not a strategy ratio.
    downside = terminal_pnl[terminal_pnl < 0]
    downside_dev = float(np.sqrt(np.mean(downside * downside))) if downside.size > 0 else 0.0
    sortino = (expected_pnl / downside_dev) if downside_dev > 0 else float("inf") if expected_pnl > 0 else 0.0

    return {
        "prob_profit": prob_profit,
        "expected_pnl": expected_pnl,
        "cvar_5pct": cvar_5pct,
        "var_5pct": var_5pct,
        "sortino": sortino,
        "breakeven_move_pct": breakeven_move_pct,
        "pop_95_low": pop_95_low,
        "pop_95_high": pop_95_high,
        "median_pnl": median_pnl,
        "std_pnl": std_pnl,
    }
