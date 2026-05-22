"""Streamlit rendering layer for the Monte Carlo trade analyzer.

Consumes the pure-Python engine in `montecarlo/` and renders the MC Analyze
panel into the current Streamlit container. Kept separate from `run_app.py`
so the integration is easy to find and the bulk of the rendering is in one
small module.

Public entry points:
    render_mc_panel(position, key)
        Render the full panel (4 metric cards + path chart + histogram +
        tweak panel) below the current Streamlit container.

    position_from_chain_row(row, side, spot, earnings_dates, rate)
        Build a single-leg `Position` from a row of the scanner's ranking
        table (the dataframe rendered in _tab_single).

    position_from_legs(legs_spec, spot, earnings_dates, rate)
        Build a multi-leg `Position` for spreads / directional / neutral
        tabs from a list of leg specs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Literal

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from options_scanner.montecarlo import (
    Leg,
    Position,
    SimulationConfig,
    SimulationResult,
    run_simulation,
)
from options_scanner.ui_theme import PALETTE, metric_card


# ── Position builders ──────────────────────────────────────────────────────


def _parse_expiration(value: Any) -> date:
    """Normalize an expiration date from various row formats to `date`.

    Handles the scanner's display format ('Jul 17 26 1E') by stripping the
    custom suffix before delegating to pandas — which already handles ISO,
    US, and named-month formats.
    """
    if isinstance(value, str):
        value = value.split(" 1E")[0].strip()
    return pd.to_datetime(value).date()


def position_from_chain_row(
    *,
    underlying: str,
    spot: float,
    row: pd.Series | dict,
    side: Literal["long", "short"],
    opt_type: Literal["call", "put"],
    qty: int = 1,
    earnings_dates: tuple[date, ...] = (),
    risk_free_rate: float = 0.045,
) -> Position:
    """Build a single-leg Position from a scanner-table row.

    Expects the row to contain at least `Strike`, `Expiration`, `Mid`, and
    `IV%` columns (matches the dataframe rendered by _show_table). Mid is
    converted to a per-contract open_cost. IV% (in percent) is converted
    to a decimal fraction.
    """
    strike = float(row["Strike"])
    expiration = _parse_expiration(row["Expiration"])
    mid = float(row["Mid"])
    iv_pct = float(row.get("IV%", 0.0))
    iv = iv_pct / 100.0 if iv_pct > 0 else None

    # open_cost: long pays the mid (positive debit); short receives it
    # (encode as negative debit so engine's `leg_value - open_cost` is right).
    side_sign = 1 if side == "long" else -1
    open_cost = side_sign * mid * 100.0 * qty

    leg = Leg(
        opt_type=opt_type,
        strike=strike,
        expiration=expiration,
        side=side,
        qty=qty,
        open_cost=open_cost,
        iv=iv,
    )
    return Position(
        underlying=underlying,
        spot=spot,
        legs=(leg,),
        risk_free_rate=risk_free_rate,
        earnings_dates=earnings_dates,
    )


@dataclass(frozen=True)
class LegSpec:
    """Shape for `position_from_legs`. Lighter than `Leg` — qty + side default."""

    opt_type: Literal["call", "put", "stock"]
    strike: float
    expiration: date
    side: Literal["long", "short"]
    mid: float                 # per-share mid (option) or per-share price (stock)
    iv: float | None = None    # decimal
    qty: int = 1


def position_from_legs(
    *,
    underlying: str,
    spot: float,
    legs_spec: Iterable[LegSpec],
    earnings_dates: tuple[date, ...] = (),
    risk_free_rate: float = 0.045,
) -> Position:
    legs: list[Leg] = []
    for s in legs_spec:
        side_sign = 1 if s.side == "long" else -1
        mult = 1 if s.opt_type == "stock" else 100
        open_cost = side_sign * s.mid * mult * s.qty
        legs.append(Leg(
            opt_type=s.opt_type, strike=s.strike, expiration=s.expiration,
            side=s.side, qty=s.qty, open_cost=open_cost, iv=s.iv,
        ))
    return Position(
        underlying=underlying, spot=spot, legs=tuple(legs),
        risk_free_rate=risk_free_rate, earnings_dates=earnings_dates,
    )


# ── Cached simulation ─────────────────────────────────────────────────────


def _position_hash(p: Position) -> tuple:
    return (
        p.underlying, p.spot, p.risk_free_rate, p.earnings_dates,
        tuple((l.opt_type, l.strike, l.expiration.isoformat(), l.side,
               l.qty, l.open_cost, l.iv) for l in p.legs),
    )


def _config_hash(c: SimulationConfig) -> tuple:
    return (c.n_paths, c.vol_source, c.vol_custom, c.drift,
            c.earnings_jumps, c.straddle_implied_move, c.seed)


@st.cache_data(show_spinner=False, ttl=600)
def _cached_simulate(
    position_hash: tuple,
    config_hash: tuple,
    _position: Position,
    _config: SimulationConfig,
) -> SimulationResult:
    """Cache wrapper. Streamlit hashes `position_hash` + `config_hash`;
    the leading-underscore args are passed through verbatim (Streamlit
    skips them in the cache key)."""
    return run_simulation(_position, _config)


# ── Altair charts ─────────────────────────────────────────────────────────


_CHART_HEIGHT = 300  # equal-height path chart + histogram


def _path_chart(result: SimulationResult, position: Position) -> alt.Chart:
    """Sampled price paths with strike + breakeven + spot reference lines.

    Y-domain is pinned to include the spot, every strike, and the 1st/99th
    percentile of all simulated paths — so the strike-line never floats off
    the chart even when far OTM.
    """
    spot = position.spot
    n_sample = result.path_sample.shape[0]
    n_days = result.path_sample.shape[1]
    df = pd.DataFrame({
        "day":   np.tile(result.days, n_sample),
        "spot":  result.path_sample.flatten(),
        "path":  np.repeat(np.arange(n_sample), n_days),
    })

    # Y-domain: include spot, every option strike, and 1/99 path percentiles.
    strikes = [leg.strike for leg in position.legs if leg.opt_type != "stock"]
    p_lo, p_hi = float(np.percentile(result.path_sample, 1)), float(np.percentile(result.path_sample, 99))
    y_min = min([p_lo, spot] + strikes) * 0.96
    y_max = max([p_hi, spot] + strikes) * 1.04

    paths_layer = (
        alt.Chart(df)
        .mark_line(opacity=0.08, color=PALETTE["primary"])
        .encode(x=alt.X("day:Q", title="Days from today"),
                y=alt.Y("spot:Q", title="Underlying ($)",
                        scale=alt.Scale(domain=[y_min, y_max], nice=False)),
                detail="path:N")
    )

    # Reference lines: current spot, strikes, breakeven (only when meaningful).
    refs: list[dict] = [{"y": spot, "label": f"Spot ${spot:.2f}",
                         "color": PALETTE["ink_1"]}]
    for leg in position.legs:
        if leg.opt_type != "stock":
            refs.append({"y": leg.strike,
                         "label": f"{leg.side[:1].upper()} {leg.opt_type[0].upper()} ${leg.strike:.0f}",
                         "color": PALETTE["ink_3"]})
    # Suppress the breakeven line when the engine's bin-based estimate is
    # noisy (extreme one-sided distributions or near-zero values).
    bk_pct = result.metrics["breakeven_move_pct"]
    if 0.3 < abs(bk_pct) < 100.0:
        be_spot = spot * (1.0 + bk_pct / 100.0)
        # Only draw if BE is inside the visible Y-domain.
        if y_min <= be_spot <= y_max:
            refs.append({"y": be_spot, "label": f"BE ${be_spot:.2f}",
                         "color": PALETTE["accent"]})

    ref_df = pd.DataFrame(refs)
    ref_layer = (
        alt.Chart(ref_df)
        .mark_rule(strokeDash=[4, 4], opacity=0.85)
        .encode(y="y:Q",
                color=alt.Color("color:N", scale=None, legend=None))
    )
    label_layer = (
        alt.Chart(ref_df)
        .mark_text(align="left", dx=6, dy=-5, fontSize=11, fontWeight=500)
        .encode(y="y:Q",
                x=alt.value(10),
                text="label:N",
                color=alt.Color("color:N", scale=None, legend=None))
    )
    return ((paths_layer + ref_layer + label_layer)
            .properties(height=_CHART_HEIGHT, title="Simulated price paths"))


def _pnl_histogram(result: SimulationResult) -> alt.Chart:
    """P&L histogram colored by sign, with mean + median reference lines."""
    pnl = result.terminal_pnl
    df = pd.DataFrame({
        "pnl": pnl,
        "sign": np.where(pnl >= 0, "profit", "loss"),
    })
    bars = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X("pnl:Q", bin=alt.Bin(maxbins=50), title="Terminal P&L ($)"),
            y=alt.Y("count()", title="Paths"),
            color=alt.Color(
                "sign:N", legend=None,
                scale=alt.Scale(
                    domain=["loss", "profit"],
                    range=[PALETTE["destructive"], PALETTE["success"]],
                ),
            ),
        )
    )
    refs = pd.DataFrame([
        {"x": float(np.mean(pnl)),   "label": f"mean ${np.mean(pnl):+.0f}",
         "color": PALETTE["accent"]},
        {"x": float(np.median(pnl)), "label": f"median ${np.median(pnl):+.0f}",
         "color": PALETTE["ink_1"]},
        {"x": 0.0,                   "label": "breakeven",
         "color": PALETTE["ink_3"]},
    ])
    rules = (
        alt.Chart(refs)
        .mark_rule(strokeDash=[3, 3])
        .encode(x="x:Q",
                color=alt.Color("color:N", scale=None, legend=None))
    )
    labels = (
        alt.Chart(refs)
        .mark_text(align="left", dx=5, dy=-2, fontSize=10, fontWeight=500)
        .encode(x="x:Q", y=alt.value(10), text="label:N",
                color=alt.Color("color:N", scale=None, legend=None))
    )
    return ((bars + rules + labels)
            .properties(height=_CHART_HEIGHT, title="P&L distribution at horizon"))


# ── Public entry point ────────────────────────────────────────────────────


def render_mc_panel(
    position: Position,
    *,
    key: str,
    default_config: SimulationConfig | None = None,
    label: str | None = None,
) -> None:
    """Render the full MC Analyze panel into the current Streamlit container.

    Args:
        position: The position to simulate.
        key: Streamlit widget-key prefix (must be unique per panel instance).
        default_config: Override the default `SimulationConfig`.
        label: Optional title to render above the metrics row.
    """
    cfg_default = default_config or SimulationConfig()

    if label:
        st.markdown(
            f"<div style='font-family: var(--osc-font); font-weight: 600; "
            f"font-size: 0.95rem; color: var(--osc-fg); margin: 0 0 0.5rem 0;'>"
            f"🔬 {label}</div>",
            unsafe_allow_html=True,
        )

    # ── Tweak panel (collapsed) ─────────────────────────────────────────
    with st.expander("Tweak assumptions", expanded=False):
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
        with c1:
            vol_source = st.radio(
                "Vol source",
                ["chain_iv", "custom"],
                index=0 if cfg_default.vol_source == "chain_iv" else 1,
                horizontal=True,
                key=f"{key}_vol_src",
                help="Chain IV uses the option's market-implied vol. Custom lets you specify."
            )
        with c2:
            vol_custom = st.number_input(
                "Custom vol (decimal)",
                value=float(cfg_default.vol_custom or 0.50),
                min_value=0.01, max_value=3.0, step=0.05,
                key=f"{key}_vol_custom",
                disabled=(vol_source == "chain_iv"),
            )
        with c3:
            n_paths = st.selectbox(
                "Paths",
                [1_000, 5_000, 10_000, 25_000],
                index=2,
                key=f"{key}_n_paths",
            )
        with c4:
            earnings = st.checkbox(
                "Earnings jumps",
                value=cfg_default.earnings_jumps,
                key=f"{key}_earnings",
                help="Apply a log-normal jump on any earnings date inside the position window."
            )
        drift = st.slider(
            "Drift premium above risk-free (%/yr)",
            min_value=-50.0, max_value=50.0, value=cfg_default.drift * 100.0,
            step=1.0, key=f"{key}_drift",
        ) / 100.0

    config = SimulationConfig(
        n_paths=int(n_paths),
        vol_source=vol_source,  # type: ignore[arg-type]
        vol_custom=float(vol_custom) if vol_source != "chain_iv" else None,
        drift=float(drift),
        earnings_jumps=bool(earnings),
        seed=cfg_default.seed,
    )

    # ── Run simulation ──────────────────────────────────────────────────
    try:
        result = _cached_simulate(
            _position_hash(position), _config_hash(config), position, config,
        )
    except Exception as exc:  # noqa: BLE001 — UI surface for any engine error
        st.error(f"Couldn't simulate this position: {exc}")
        return

    # ── Metric cards ───────────────────────────────────────────────────
    # Two rows of 4 cards. Row 1 = headline metrics (POP, EV, CVaR, BE move).
    # Row 2 = quant-grade metrics (VaR, Sortino, MC fair value, edge vs mkt).
    pop = result.metrics["prob_profit"]
    ev = result.metrics["expected_pnl"]
    cvar = result.metrics["cvar_5pct"]
    var5 = result.metrics["var_5pct"]
    sortino = result.metrics["sortino"]
    bk = result.metrics["breakeven_move_pct"]
    fair = result.metrics["mc_fair_value"]
    edge = result.metrics["edge_vs_market"]
    fair_se = result.metrics["mc_fair_value_stderr"]

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        metric_card(
            label="P(profit)",
            value=f"{pop * 100:.1f}%",
            delta="▲ favorable" if pop >= 0.5 else "▼ unfavorable",
            delta_sign="pos" if pop >= 0.5 else "neg",
        )
    with m2:
        ev_sign = "+" if ev >= 0 else "−"
        metric_card(
            label="Expected P&L",
            value=f"{ev_sign}${abs(ev):,.0f}",
            delta_sign="pos" if ev >= 0 else "neg",
        )
    with m3:
        metric_card(
            label="CVaR (worst 5%)",
            value=f"−${abs(cvar):,.0f}" if cvar < 0 else f"+${cvar:,.0f}",
            help_text="Average P&L of the worst 5% of simulated paths.",
        )
    with m4:
        if 0.3 < abs(bk) < 100.0:
            bk_sign = "+" if bk >= 0 else ""
            be_label = f"{bk_sign}{bk:.1f}%"
        else:
            be_label = "—"
        metric_card(
            label="Breakeven move",
            value=be_label,
            help_text="Underlying % move from spot needed to hit zero P&L at horizon.",
        )

    # Row 2: quant-grade metrics
    n1, n2, n3, n4 = st.columns(4)
    with n1:
        metric_card(
            label="VaR (5%)",
            value=f"−${abs(var5):,.0f}" if var5 < 0 else f"+${var5:,.0f}",
            help_text="Threshold loss exceeded in 5% of paths. CVaR is the average beyond this; VaR ≤ CVaR by construction.",
        )
    with n2:
        if sortino == float("inf"):
            sortino_label = "∞"
        elif abs(sortino) > 99.99:
            sortino_label = "—"
        else:
            sortino_label = f"{sortino:+.2f}"
        metric_card(
            label="Sortino",
            value=sortino_label,
            delta_sign="pos" if sortino > 0 else "neg",
            help_text="Per-position Sortino: expected P&L divided by downside-only standard deviation. Asymmetric ratio appropriate for options.",
        )
    with n3:
        fair_sign = "+" if fair >= 0 else "−"
        metric_card(
            label="MC fair value",
            value=f"{fair_sign}${abs(fair):,.0f}",
            help_text=f"Discounted expected payoff (the simulation's fair price). Std error ≈ ±${fair_se:,.0f}.",
        )
    with n4:
        edge_sign = "+" if edge >= 0 else "−"
        # Compares market price to the simulation's fair value.
        # Positive: market priced this below the simulation's fair value.
        metric_card(
            label="Premium vs model",
            value=f"{edge_sign}${abs(edge):,.0f}",
            delta="▲ rich" if edge >= 0 else "▼ cheap",
            delta_sign="pos" if edge >= 0 else "neg",
            help_text="MC fair value minus your open cost. Positive = the market is pricing below the model here; negative = above. Diagnostic — the model is one calibrated view, not market truth.",
        )

    # ── Charts ─────────────────────────────────────────────────────────
    # Equal-width columns + equal heights so the two panels visually bottom-
    # align. The path chart has the wider visual surface area anyway via
    # its denser line ink; equal widths keep the page rhythm consistent.
    col_paths, col_hist = st.columns(2)
    with col_paths:
        st.altair_chart(_path_chart(result, position), width="stretch")
    with col_hist:
        st.altair_chart(_pnl_histogram(result), width="stretch")

    # ── Assumptions caption ───────────────────────────────────────────
    vol_label = f"IV {vol_custom * 100:.1f}%" if vol_source != "chain_iv" else "chain IV"
    earnings_label = "on" if config.earnings_jumps and position.earnings_dates else "off"
    horizon_str = result.horizon.strftime("%b %d '%y")
    st.caption(
        f"**Assumptions:** vol = {vol_label}  ·  drift = {config.drift * 100:+.1f}%/yr  ·  "
        f"rate = {position.risk_free_rate * 100:.1f}%  ·  "
        f"{config.n_paths:,} paths  ·  earnings jumps: {earnings_label}  ·  "
        f"horizon = {horizon_str}  ·  95% spot CI = "
        f"${result.metrics['pop_95_low']:.2f} – ${result.metrics['pop_95_high']:.2f}"
    )
