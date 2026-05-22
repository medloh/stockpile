"""Monte Carlo simulation orchestrator.

`run_simulation(position, config)` is the only public entry point. Pure
function; safe to wrap in `@st.cache_data` upstream.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import numpy as np

from .model import generate_paths, TRADING_DAYS_PER_YEAR
from .position import Position, evaluate_payoff
from .metrics import summarize


@dataclass(frozen=True)
class SimulationConfig:
    """User-tweakable knobs for the MC engine.

    Attributes:
        n_paths: Number of simulated paths. 10k is the default sweet spot for
            stable metrics (~1% std error on prob_profit) at sub-second runtime.
            Antithetic variates roughly halve this for smooth payoffs.
        vol_source: Which vol to use as the GBM sigma.
            "chain_iv" — qty-weighted IV from the position's option legs.
            "historical_30d" — caller-supplied historical vol via vol_custom.
            "custom" — caller-supplied vol_custom.
        vol_custom: Annualized vol (decimal) used when vol_source != "chain_iv".
        drift: Additional drift premium above the risk-free rate (decimal/yr).
            Default 0 = risk-neutral.
        earnings_jumps: Whether to apply Merton-style jumps on position.earnings_dates.
        straddle_implied_move: Optional ATM-straddle-implied % move (decimal,
            e.g. 0.08 = 8% expected move). When provided AND earnings_jumps
            is True, the jump sigma is calibrated to match this implied move
            instead of the heuristic fallback. Far more accurate when you
            can pass it; safe to omit when you can't.
        seed: RNG seed for reproducible output. None = non-deterministic.
    """

    n_paths: int = 10_000
    vol_source: Literal["chain_iv", "historical_30d", "custom"] = "chain_iv"
    vol_custom: float | None = None
    drift: float = 0.0
    earnings_jumps: bool = True
    straddle_implied_move: float | None = None
    seed: int | None = None


@dataclass(frozen=True)
class SimulationResult:
    """Outputs of a single MC run.

    Attributes:
        n_paths: Confirmed number of paths simulated.
        horizon: Date P&L is reported at (max leg expiry).
        terminal_spot: (n_paths,) underlying spot at horizon.
        terminal_pnl: (n_paths,) dollar P&L per path.
        path_sample: (200, n_days+1) up to 200 sampled paths for plotting.
        days: (n_days+1,) integer day offsets from today (column 0 = today).
        metrics: dict of summary metrics; see metrics.summarize().
    """

    n_paths: int
    horizon: date
    terminal_spot: np.ndarray
    terminal_pnl: np.ndarray
    path_sample: np.ndarray
    days: np.ndarray
    metrics: dict[str, float]


def _resolve_vol(position: Position, config: SimulationConfig) -> float:
    """Pick a sigma for the GBM diffusion based on config.vol_source.

    For multi-leg positions with chain IV we weight by abs(qty) to reflect
    each leg's risk contribution rather than a naive average. A leg with
    10 contracts shouldn't be down-weighted by leg-count when paired with
    a single hedge contract. This makes a noticeable difference for PMCC
    (long stock = qty 100 mapped to 1 contract-equivalent + 1 short call).
    """
    if config.vol_source == "chain_iv":
        items = [(leg.iv, abs(leg.qty)) for leg in position.legs
                 if leg.iv is not None and leg.iv > 0 and leg.opt_type != "stock"]
        if not items:
            raise ValueError(
                "vol_source='chain_iv' requested but no option leg has a "
                "positive IV. Set Leg.iv on at least one option leg or use "
                "vol_source='custom'."
            )
        ivs, weights = zip(*items)
        return float(np.average(ivs, weights=weights))
    if config.vol_source in ("historical_30d", "custom"):
        if config.vol_custom is None or config.vol_custom <= 0:
            raise ValueError(
                f"vol_source='{config.vol_source}' requires a positive vol_custom."
            )
        return float(config.vol_custom)
    raise ValueError(f"unknown vol_source: {config.vol_source!r}")


def _resolve_jump_sigma(position: Position, config: SimulationConfig) -> float:
    """Pick the per-earnings-event jump sigma.

    Calibration cascade, best to worst:
      1. If `config.straddle_implied_move` is set, use it directly. The
         ATM straddle's implied % move is the market's own estimate of
         the post-earnings one-sigma move, so jump_sigma = straddle_move.
         This is the standard quant calibration — see Resonanz Capital's
         "Options Straddles And Earnings Move Estimates" and Merton (1976).
      2. Otherwise, derive a rough proxy from the position's average IV.
         An IV regime of 60% implies a ~3.8%/day vol; we use ~3-4x that
         for the binary-event scale, clamped to [3%, 25%]. Crude but
         non-zero.
      3. If no IV is available, fall back to 6% — close to the historical
         average post-earnings move for mid/large-cap US equities.
    """
    if config.straddle_implied_move is not None and config.straddle_implied_move > 0:
        # Clamp to a sane range — very illiquid tickers can imply pathological
        # moves; very liquid ones can imply near-zero (which would suppress
        # the jump entirely, which is wrong).
        return float(max(0.02, min(0.40, config.straddle_implied_move)))
    ivs = [leg.iv for leg in position.legs if leg.iv is not None and leg.iv > 0]
    if not ivs:
        return 0.06
    avg_iv = float(np.mean(ivs))
    return float(max(0.03, min(0.25, avg_iv / np.sqrt(TRADING_DAYS_PER_YEAR) * 4.0)))


def run_simulation(
    position: Position,
    config: SimulationConfig = SimulationConfig(),
    today: date | None = None,
) -> SimulationResult:
    """Run the Monte Carlo simulation for the given multi-leg position.

    Args:
        position: The position to simulate.
        config: Engine knobs. Defaults are sensible for retail trader UX.
        today: Simulation start date. Defaults to `date.today()`. Useful to
            inject in tests for deterministic horizon computation.

    Returns:
        SimulationResult with terminal P&L, sampled paths, and summary metrics.

    Raises:
        ValueError: When position has no legs, or vol cannot be resolved.
    """
    if not position.legs:
        raise ValueError("position has no legs")
    today = today or date.today()

    # Horizon = the latest expiry across legs. Stock legs use horizon.
    option_expiries = [leg.expiration for leg in position.legs if leg.opt_type != "stock"]
    horizon = max(option_expiries) if option_expiries else today
    if horizon <= today:
        raise ValueError(
            f"horizon {horizon} is not in the future relative to {today}. "
            "Pass option legs with expiration > today."
        )
    n_days = (horizon - today).days

    vol = _resolve_vol(position, config)
    earnings_offsets: list[int] = []
    if config.earnings_jumps and position.earnings_dates:
        for ed in position.earnings_dates:
            off = (ed - today).days
            if 0 < off <= n_days:
                earnings_offsets.append(off)
    jump_sigma = _resolve_jump_sigma(position, config) if earnings_offsets else 0.0

    paths = generate_paths(
        spot=position.spot,
        vol=vol,
        drift=config.drift,
        rf=position.risk_free_rate,
        n_paths=config.n_paths,
        n_days=n_days,
        seed=config.seed,
        earnings_day_offsets=earnings_offsets,
        jump_sigma=jump_sigma,
    )
    days = np.arange(n_days + 1, dtype=np.int64)
    terminal_pnl = evaluate_payoff(position, paths, days, horizon, today)
    terminal_spot = paths[:, -1]

    # Sample up to 200 paths for plotting (deterministic given config.seed).
    n_sample = min(200, paths.shape[0])
    rng = np.random.default_rng(config.seed if config.seed is not None else 0)
    if paths.shape[0] > n_sample:
        idx = rng.choice(paths.shape[0], size=n_sample, replace=False)
    else:
        idx = np.arange(n_sample)
    path_sample = paths[idx]

    metrics = summarize(terminal_pnl, terminal_spot, position.spot)
    # ── MC fair value & premium vs model ─────────────────────────────────
    # MC fair value = mean of discounted terminal payoffs (excluding the
    # open-cost offset). Premium vs model = how the market price differs
    # from this model value, scaled to per-contract dollars. Diagnostic —
    # the model is one calibrated view, not arbitrage-free truth. Positive
    # values mean the position was opened below the model's fair value.
    total_open_cost = sum(leg.open_cost for leg in position.legs)
    discount = float(np.exp(-position.risk_free_rate * (n_days / 365.0)))
    # Sum of terminal payoffs (positive long, negative short, sign already
    # in evaluate_payoff because we subtract open_cost there). To recover
    # the gross payoff for fair-value comparison, add open_cost back.
    gross_payoff_per_path = terminal_pnl + total_open_cost
    mc_fair_value = float(np.mean(gross_payoff_per_path) * discount)
    edge_vs_market = mc_fair_value - total_open_cost
    # Confidence interval on the MC fair value (std error scales 1/√n).
    mc_fair_value_stderr = float(
        np.std(gross_payoff_per_path) * discount / np.sqrt(config.n_paths)
    )
    metrics["mc_fair_value"] = mc_fair_value
    metrics["edge_vs_market"] = edge_vs_market
    metrics["mc_fair_value_stderr"] = mc_fair_value_stderr

    return SimulationResult(
        n_paths=config.n_paths,
        horizon=horizon,
        terminal_spot=terminal_spot,
        terminal_pnl=terminal_pnl,
        path_sample=path_sample,
        days=days,
        metrics=metrics,
    )
