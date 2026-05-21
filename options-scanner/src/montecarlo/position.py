"""Position model: Leg + Position dataclasses and payoff evaluation.

A Leg is a single instrument the user holds (long/short, call/put/stock). A
Position bundles legs sharing an underlying. `evaluate_payoff` computes net
$ P&L per simulated path at a given horizon date by intrinsic-value pricing
each leg (European exercise, no time value beyond horizon) and netting against
the legs' open costs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import numpy as np


@dataclass(frozen=True)
class Leg:
    """A single instrument held within a Position.

    Attributes:
        opt_type: "call", "put", or "stock".
        strike: Strike price (ignored for stock).
        expiration: Option expiration (for stock, use position horizon).
        side: "long" or "short".
        qty: Number of contracts (options use 100x multiplier internally) or shares.
        open_cost: $ debit paid (positive) or credit received (negative) to open
            this leg, expressed as the total cost for `qty` contracts/shares,
            NOT per-contract. For options, callers should pass `price * 100 * qty`.
        iv: Implied vol (annualized, decimal). Used when vol_source="chain_iv".
            Optional for stock or when a custom/historical vol is supplied.
    """

    opt_type: Literal["call", "put", "stock"]
    strike: float
    expiration: date
    side: Literal["long", "short"]
    qty: int = 1
    open_cost: float = 0.0
    iv: float | None = None


@dataclass(frozen=True)
class Position:
    """A multi-leg position on a single underlying."""

    underlying: str
    spot: float
    legs: tuple[Leg, ...]
    risk_free_rate: float = 0.045
    earnings_dates: tuple[date, ...] = field(default_factory=tuple)


def _leg_multiplier(leg: Leg) -> int:
    """Contract multiplier: 100 for options, 1 for stock."""
    return 1 if leg.opt_type == "stock" else 100


def _leg_intrinsic(leg: Leg, spot_at_expiry: np.ndarray) -> np.ndarray:
    """Per-share intrinsic value of one leg at expiry.

    Args:
        leg: The leg.
        spot_at_expiry: (n_paths,) underlying spot on the leg's expiration.

    Returns:
        (n_paths,) per-share value (always >= 0 for options, spot for stock).
    """
    if leg.opt_type == "call":
        return np.maximum(spot_at_expiry - leg.strike, 0.0)
    if leg.opt_type == "put":
        return np.maximum(leg.strike - spot_at_expiry, 0.0)
    # stock
    return spot_at_expiry


def evaluate_payoff(
    position: Position,
    paths: np.ndarray,
    days: np.ndarray,
    horizon: date,
    today: date,
) -> np.ndarray:
    """Net $ P&L per path at `horizon`.

    For each leg, the leg's value at its expiry (or horizon, whichever is
    earlier — we use the leg's own expiration) is computed by intrinsic-value
    pricing, multiplied by qty * multiplier * side_sign, then summed across
    legs and netted against open_cost.

    Args:
        position: The position.
        paths: (n_paths, n_days+1) simulated spot prices.
        days: (n_days+1,) integer day offsets from `today`.
        horizon: The date at which to report P&L (= max expiry of any leg).
        today: Simulation start date.

    Returns:
        (n_paths,) $ P&L per path.

    Notes:
        Legs with expirations within the simulated window are settled at their
        own expiration date's spot (intrinsic). Stock legs settle at `horizon`.
    """
    n_paths = paths.shape[0]
    pnl = np.zeros(n_paths, dtype=np.float64)
    # Map day-offset -> column index for fast lookup.
    days_to_idx = {int(d): i for i, d in enumerate(days)}

    for leg in position.legs:
        if leg.opt_type == "stock":
            settle_day = (horizon - today).days
        else:
            settle_day = (leg.expiration - today).days
        # Clamp to available simulation window (defensive: should match horizon).
        if settle_day not in days_to_idx:
            # Pick nearest available column not exceeding settle_day.
            settle_day = max(d for d in days_to_idx if d <= settle_day)
        col = days_to_idx[settle_day]
        spot_at = paths[:, col]
        value_per_share = _leg_intrinsic(leg, spot_at)
        side_sign = 1 if leg.side == "long" else -1
        mult = _leg_multiplier(leg)
        leg_value = side_sign * leg.qty * mult * value_per_share
        # open_cost: debit paid (long) reduces P&L; credit received (short) is
        # already encoded by caller as a negative open_cost (i.e. you paid
        # negative dollars to open). We subtract open_cost from leg_value.
        pnl += leg_value - leg.open_cost
    return pnl
