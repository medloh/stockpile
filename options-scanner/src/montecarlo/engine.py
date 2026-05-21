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
        vol_source: Which vol to use as the GBM sigma.
            "chain_iv" — IV from the position's option legs (averaged if multi-leg).
            "historical_30d" — caller-supplied historical vol via vol_custom.
            "custom" — caller-supplied vol_custom.
        vol_custom: Annualized vol (decimal) used when vol_source != "chain_iv".
        drift: Additional drift premium above the risk-free rate (decimal/yr).
            Default 0 = risk-neutral.
        earnings_jumps: Whether to apply Merton-style jumps on position.earnings_dates.
        seed: RNG seed for reproducible output. None = non-deterministic.
    """

    n_paths: int = 10_000
    vol_source: Literal["chain_iv", "historical_30d", "custom"] = "chain_iv"
    vol_custom: float | None = None
    drift: float = 0.0
    earnings_jumps: bool = True
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
    """Pick a sigma for the GBM diffusion based on config.vol_source."""
    if config.vol_source == "chain_iv":
        ivs = [leg.iv for leg in position.legs if leg.iv is not None and leg.iv > 0]
        if not ivs:
            raise ValueError(
                "vol_source='chain_iv' requested but no leg has a positive IV. "
                "Set Leg.iv on at least one option leg or use vol_source='custom'."
            )
        return float(np.mean(ivs))
    if config.vol_source in ("historical_30d", "custom"):
        if config.vol_custom is None or config.vol_custom <= 0:
            raise ValueError(
                f"vol_source='{config.vol_source}' requires a positive vol_custom."
            )
        return float(config.vol_custom)
    raise ValueError(f"unknown vol_source: {config.vol_source!r}")


def _resolve_jump_sigma(position: Position, config: SimulationConfig) -> float:
    """Pick the per-earnings-event jump sigma.

    Heuristic: derive from average leg IV scaled by sqrt(days-to-earnings),
    bounded to [0.03, 0.20]. Falls back to 0.06 when no IV is available.
    The straddle-implied move would be a more rigorous source but requires
    pricing data we don't have at engine time.
    """
    ivs = [leg.iv for leg in position.legs if leg.iv is not None and leg.iv > 0]
    if not ivs:
        return 0.06
    avg_iv = float(np.mean(ivs))
    # Loose proxy: ~1-day vol of the IV regime, floored/capped.
    return float(max(0.03, min(0.20, avg_iv / np.sqrt(TRADING_DAYS_PER_YEAR) * 5.0)))


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
    return SimulationResult(
        n_paths=config.n_paths,
        horizon=horizon,
        terminal_spot=terminal_spot,
        terminal_pnl=terminal_pnl,
        path_sample=path_sample,
        days=days,
        metrics=metrics,
    )
