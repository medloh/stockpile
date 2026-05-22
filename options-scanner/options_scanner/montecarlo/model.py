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

    # Antithetic variates: generate n_paths/2 random shocks, then pair each
    # with its negation. The averaged estimate has 30-50% lower variance for
    # smooth payoffs (calls, puts, verticals). For odd n_paths the last path
    # is independent. Reference: Glasserman, "Monte Carlo Methods in
    # Financial Engineering", §4.2.
    if n_days > 0:
        half = n_paths // 2
        z_half = rng.standard_normal(size=(half, n_days))
        if 2 * half == n_paths:
            shocks = np.vstack([z_half, -z_half])
        else:
            z_extra = rng.standard_normal(size=(1, n_days))
            shocks = np.vstack([z_half, -z_half, z_extra])
    else:
        shocks = np.empty((n_paths, 0))
    log_steps = log_drift + log_sigma * shocks

    # Earnings jumps: add an independent shock on each earnings day. We
    # also apply antithetic variates to the jump component so the
    # variance-reduction property propagates through earnings days.
    earnings_set = sorted({int(d) for d in earnings_day_offsets if 0 < int(d) <= n_days})
    if earnings_set and jump_sigma > 0:
        half = n_paths // 2
        for d in earnings_set:
            z_jump = rng.standard_normal(size=half) * jump_sigma
            if 2 * half == n_paths:
                jump_shocks = np.concatenate([z_jump, -z_jump])
            else:
                jump_extra = rng.standard_normal(size=1) * jump_sigma
                jump_shocks = np.concatenate([z_jump, -z_jump, jump_extra])
            log_steps[:, d - 1] += jump_shocks

    # Cumulative sum then exponentiate -> price paths, prepended with spot.
    log_prices = np.cumsum(log_steps, axis=1)
    paths = spot * np.exp(log_prices)
    out = np.empty((n_paths, n_days + 1), dtype=np.float64)
    out[:, 0] = spot
    if n_days > 0:
        out[:, 1:] = paths
    return out
