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
from datetime import date, datetime
from typing import Any, Iterable, Literal

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from montecarlo import (
    Leg,
    Position,
    SimulationConfig,
    SimulationResult,
    run_simulation,
)
from ui_theme import PALETTE


# ── Position builders ──────────────────────────────────────────────────────


def _parse_expiration(value: Any) -> date:
    """Normalize an expiration date from various row formats to `date`."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, str):
        # Common formats: "2027-01-15", "Jan 26 '27 1E", "01/15/2027"
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d '%y", "%b %d %Y"):
            try:
                return datetime.strptime(value.split(" 1E")[0].strip(), fmt).date()
            except ValueError:
                continue
    raise ValueError(f"can't parse expiration: {value!r}")


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
    strike = float(row["Strike"]) if "Strike" in row else float(row["strike"])
    exp_raw = row.get("Expiration") if hasattr(row, "get") else row["Expiration"]
    expiration = _parse_expiration(exp_raw)
    mid = float(row["Mid"]) if "Mid" in row else float(row.get("mid", 0.0))
    iv_pct = float(row.get("IV%", row.get("iv_pct", 0.0)))
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
            c.earnings_jumps, c.seed)


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


def _path_chart(result: SimulationResult, position: Position) -> alt.Chart:
    """Sampled price paths with strike + breakeven + spot reference lines."""
    spot = position.spot
    n_sample = result.path_sample.shape[0]
    n_days = result.path_sample.shape[1]
    # Long-form dataframe for Altair.
    df = pd.DataFrame({
        "day":   np.tile(result.days, n_sample),
        "spot":  result.path_sample.flatten(),
        "path":  np.repeat(np.arange(n_sample), n_days),
    })
    paths_layer = (
        alt.Chart(df)
        .mark_line(opacity=0.10, color=PALETTE["primary"])
        .encode(x=alt.X("day:Q", title="Days from today"),
                y=alt.Y("spot:Q", title="Underlying ($)"),
                detail="path:N")
    )

    # Reference lines: current spot, strikes, breakeven (if computable).
    refs: list[dict] = [{"y": spot, "label": f"Spot ${spot:.2f}",
                         "color": PALETTE["text"]}]
    for leg in position.legs:
        if leg.opt_type != "stock":
            refs.append({"y": leg.strike,
                         "label": f"{leg.side[:1].upper()} {leg.opt_type[0].upper()} ${leg.strike:.0f}",
                         "color": PALETTE["muted_fg"]})
    bk_pct = result.metrics["breakeven_move_pct"]
    if bk_pct != 0.0:
        be_spot = spot * (1.0 + bk_pct / 100.0)
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
        .mark_text(align="left", dx=6, dy=-5, fontSize=11)
        .encode(y="y:Q",
                x=alt.value(8),
                text="label:N",
                color=alt.Color("color:N", scale=None, legend=None))
    )
    return (paths_layer + ref_layer + label_layer).properties(height=260)


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
         "color": PALETTE["text"]},
        {"x": 0.0,                   "label": "breakeven",
         "color": PALETTE["muted_fg"]},
    ])
    rules = (
        alt.Chart(refs)
        .mark_rule(strokeDash=[3, 3])
        .encode(x="x:Q",
                color=alt.Color("color:N", scale=None, legend=None))
    )
    labels = (
        alt.Chart(refs)
        .mark_text(align="left", dx=5, dy=-2, fontSize=10)
        .encode(x="x:Q", y=alt.value(8), text="label:N",
                color=alt.Color("color:N", scale=None, legend=None))
    )
    return (bars + rules + labels).properties(height=240)


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
            f"<div style='font-family: var(--osc-font-sans); font-weight: 600; "
            f"font-size: 0.95rem; color: var(--osc-text); margin: 0 0 0.5rem 0;'>"
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
    m1, m2, m3, m4 = st.columns(4)
    pop = result.metrics["prob_profit"]
    ev = result.metrics["expected_pnl"]
    cvar = result.metrics["cvar_5pct"]
    bk = result.metrics["breakeven_move_pct"]
    pop_tone = "▲" if pop >= 0.5 else "▼"
    ev_sign = "+" if ev >= 0 else ""
    bk_sign = "+" if bk >= 0 else ""
    with m1:
        st.metric("P(profit)", f"{pop * 100:.1f}%", delta=pop_tone,
                  delta_color="normal" if pop >= 0.5 else "inverse")
    with m2:
        st.metric("Expected P&L", f"${ev_sign}{ev:,.0f}")
    with m3:
        st.metric("CVaR (worst 5%)", f"${cvar:,.0f}")
    with m4:
        be_label = f"{bk_sign}{bk:.1f}%" if bk != 0 else "—"
        st.metric("Breakeven move", be_label,
                  help="Underlying %% move needed to hit zero P&L at horizon.")

    # ── Charts ─────────────────────────────────────────────────────────
    col_paths, col_hist = st.columns([3, 2])
    with col_paths:
        st.altair_chart(_path_chart(result, position), use_container_width=True)
    with col_hist:
        st.altair_chart(_pnl_histogram(result), use_container_width=True)

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
