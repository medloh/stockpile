"""Path-generation models for the Monte Carlo engine.

Currently implements:
    - Geometric Brownian Motion (GBM) under the risk-neutral measure
    - Optional Merton-style earnings jumps applied at specified dates

All path generation is vectorized via NumPy — no Python loops over paths.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable

import numpy as np

# Calendar convention: ~252 trading days/year. We use 252 for vol/drift
# annualization but step paths in calendar days (so 240 DTE = 240 steps).
# This slightly overstates the variance vs a trading-day step but is the
# simpler and more common retail-trader convention.
TRADING_DAYS_PER_YEAR = 252


def generate_paths(
    spot: float,
    vol: float,
    drift: float,
    rf: float,
    n_paths: int,
    n_days: int,
    seed: int | None = None,
    earnings_day_offsets: Iterable[int] = (),
    jump_sigma: float = 0.06,
) -> np.ndarray:
    """Simulate underlying price paths under GBM with optional earnings jumps.

    Args:
        spot: Initial spot price.
        vol: Annualized implied volatility (decimal, e.g. 0.45 for 45%).
        drift: User-supplied drift premium ABOVE the risk-free rate (decimal/yr).
            For risk-neutral pricing pass drift=0; the actual drift used is
            (rf + drift - 0.5 * vol^2).
        rf: Risk-free rate (decimal/yr).
        n_paths: Number of simulated paths.
        n_days: Number of calendar-day steps (paths shape will be (n_paths, n_days+1)).
        seed: RNG seed for reproducibility. None = non-deterministic.
        earnings_day_offsets: Iterable of integer day offsets (from t=0, i.e.
            today) at which to apply a multiplicative log-normal jump. Each
            offset must satisfy 0 < d <= n_days. Empty disables jumps.
        jump_sigma: Standard deviation of the log-normal jump (decimal).
            Default 0.06 = ~6% one-sigma post-earnings move, a reasonable
            average across mid/large-cap US equities. Tune externally if you
            have a calibrated value from the ATM straddle's implied move.

    Returns:
        (n_paths, n_days+1) array of simulated spot prices. Column 0 is `spot`.

    Notes:
        Step size is 1/TRADING_DAYS_PER_YEAR. Calendar-day stepping over
        weekends slightly inflates accumulated variance vs strict
        business-day stepping — acceptable for retail-trader UX where this
        is more conservative (overstates uncertainty).
    """
    if n_paths <= 0 or n_days < 0:
        raise ValueError(f"n_paths must be > 0 and n_days >= 0 (got {n_paths}, {n_days})")
    if vol < 0:
        raise ValueError(f"vol must be non-negative (got {vol})")

    rng = np.random.default_rng(seed)
    dt = 1.0 / TRADING_DAYS_PER_YEAR
    # Risk-neutral drift in log-space: (rf + premium - 0.5*sigma^2).
    log_drift = (rf + drift - 0.5 * vol * vol) * dt
    log_sigma = vol * np.sqrt(dt)

    # Generate standard normal shocks for the diffusion component.
    shocks = rng.standard_normal(size=(n_paths, n_days)) if n_days > 0 else np.empty((n_paths, 0))
    log_steps = log_drift + log_sigma * shocks

    # Earnings jumps: add an independent shock on each earnings day.
    earnings_set = sorted({int(d) for d in earnings_day_offsets if 0 < int(d) <= n_days})
    if earnings_set and jump_sigma > 0:
        for d in earnings_set:
            # Column d-1 maps to the step that lands us on day d.
            jump_shocks = rng.standard_normal(size=n_paths) * jump_sigma
            log_steps[:, d - 1] += jump_shocks

    # Cumulative sum then exponentiate -> price paths, prepended with spot.
    log_prices = np.cumsum(log_steps, axis=1)
    paths = spot * np.exp(log_prices)
    out = np.empty((n_paths, n_days + 1), dtype=np.float64)
    out[:, 0] = spot
    if n_days > 0:
        out[:, 1:] = paths
    return out
