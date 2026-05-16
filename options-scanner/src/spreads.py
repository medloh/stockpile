"""Multi-leg spread builders, Greeks, payoff diagrams, and ranking logic."""

import math
from datetime import date, datetime

import numpy as np
import pandas as pd

from chain import _norm_cdf, _norm_pdf

RISK_FREE_RATE = 0.045

# ── Column schema ─────────────────────────────────────────────────────────────

SPREAD_COLS = [
    "strategy", "expiration", "dte",
    "short_strike", "long_strike", "short_strike2", "long_strike2",
    "short_type", "long_type",
    "short_mid", "long_mid", "short_mid2", "long_mid2",
    "short_iv", "long_iv",
    "net_credit",
    "max_profit", "max_loss", "risk_reward",
    "pop", "pot",
    "expected_value",
    "ann_yield_pct",
    "breakeven1", "breakeven2", "be_move_pct",
    "net_delta", "net_gamma", "net_theta", "net_vega",
    "positive_theta", "positive_vega",
    "earnings_in_window",
    "short_iv_excess", "short_oi", "long_oi", "spot",
]

STRATEGY_NAMES = [
    "Bull Put Spread",
    "Bear Call Spread",
    "Bull Call Spread",
    "Bear Put Spread",
    "Jade Lizard",
    "Risk Reversal",
    "Iron Condor",
    "Iron Butterfly",
    "Broken-Wing Butterfly",
    "Calendar / Diagonal",
    "Ratio Spread (1×2)",
    "Long Straddle",
    "Long Strangle",
]

DIRECTIONAL_STRATEGIES = [
    "Bull Put Spread",
    "Bear Call Spread",
    "Bull Call Spread",
    "Bear Put Spread",
    "Jade Lizard",
    "Risk Reversal",
]

NEUTRAL_STRATEGIES = [
    "Iron Condor",
    "Iron Butterfly",
    "Broken-Wing Butterfly",
    "Calendar / Diagonal",
    "Ratio Spread (1×2)",
    "Long Straddle",
    "Long Strangle",
]

# ── BS helpers ────────────────────────────────────────────────────────────────

def _d1d2(S, K, T, sigma):
    r = RISK_FREE_RATE
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return d1, d1 - sigma * math.sqrt(T)


def prob_above(S: float, K: float, T: float, sigma: float) -> float:
    """Risk-neutral P(S_T > K) = N(d2)."""
    if T <= 0 or sigma < 0.001 or S <= 0 or K <= 0:
        return 1.0 if S > K else 0.0
    _, d2 = _d1d2(S, K, T, sigma)
    return _norm_cdf(d2)


def _bs_price(S: float, K: float, T: float, sigma: float, opt_type: str) -> float:
    r = RISK_FREE_RATE
    if T <= 0 or sigma < 0.001:
        return max(0.0, S - K) if opt_type == "call" else max(0.0, K - S)
    d1, d2 = _d1d2(S, K, T, sigma)
    if opt_type == "call":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def _bs_theta(S: float, K: float, T: float, sigma: float, opt_type: str) -> float:
    """Daily theta per share (negative = loses value each day for long holders)."""
    r = RISK_FREE_RATE
    if T <= 0 or sigma < 0.001 or S <= 0:
        return 0.0
    d1, d2 = _d1d2(S, K, T, sigma)
    term1 = -(S * _norm_pdf(d1) * sigma) / (2 * math.sqrt(T))
    if opt_type == "call":
        return (term1 - r * K * math.exp(-r * T) * _norm_cdf(d2)) / 365
    return (term1 + r * K * math.exp(-r * T) * _norm_cdf(-d2)) / 365


def _bs_vega(S: float, K: float, T: float, sigma: float) -> float:
    """Vega per 1-point IV move (same for calls and puts)."""
    if T <= 0 or sigma < 0.001 or S <= 0:
        return 0.0
    d1, _ = _d1d2(S, K, T, sigma)
    return S * _norm_pdf(d1) * math.sqrt(T)


def _mid_price(row) -> float:
    b, a = float(row["bid"]), float(row["ask"])
    m = float(row["mid"])
    return (b + a) / 2 if b > 0 and a > 0 else m


def _T(dte: int) -> float:
    return max(dte, 1) / 365.0


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=SPREAD_COLS)


# ── Payoff diagram ────────────────────────────────────────────────────────────

def spread_payoff_data(legs: list[dict], spot: float, T: float) -> pd.DataFrame:
    """
    legs: list of {type, strike, qty, entry_mid, iv}
      qty = +1 long, -1 short
    Returns DataFrame: price, pl_expiry, pl_current (per share)
    """
    prices = np.linspace(spot * 0.78, spot * 1.22, 250)
    pl_expiry = np.zeros(len(prices))
    pl_current = np.zeros(len(prices))
    for leg in legs:
        qty = leg["qty"]
        K = leg["strike"]
        iv = max(leg["iv"], 0.01)
        entry = leg["entry_mid"]
        ot = leg["type"]
        for i, p in enumerate(prices):
            intrinsic = max(0.0, p - K) if ot == "call" else max(0.0, K - p)
            pl_expiry[i] += qty * (intrinsic - entry)
            bs = _bs_price(p, K, T, iv, ot)
            pl_current[i] += qty * (bs - entry)
    return pd.DataFrame({"price": prices, "pl_expiry": pl_expiry, "pl_current": pl_current})


def build_legs_from_row(row: pd.Series) -> list[dict]:
    """Reconstruct leg list from a spread schema row for payoff charting."""
    strategy = row["strategy"]
    legs = []

    def _leg(opt_type, strike, qty, mid_val, iv_val):
        return {"type": opt_type, "strike": strike, "qty": qty,
                "entry_mid": mid_val, "iv": iv_val}

    st = row["short_strike"]
    lt = row["long_strike"]
    s_type = row.get("short_type", "put")
    l_type = row.get("long_type", "put")
    s_mid = row.get("short_mid", 0.0)
    l_mid = row.get("long_mid", 0.0)
    s_iv = row.get("short_iv", 0.20)
    l_iv = row.get("long_iv", 0.20)

    if strategy in ("Bull Put Spread", "Bear Put Spread",
                    "Bull Call Spread", "Bear Call Spread"):
        legs = [_leg(s_type, st, -1, s_mid, s_iv),
                _leg(l_type, lt, +1, l_mid, l_iv)]

    elif strategy == "Long Straddle":
        # Same strike for call + put; long both
        legs = [_leg("call", lt, +1, l_mid, l_iv),
                _leg("put",  st, +1, s_mid, s_iv)]

    elif strategy == "Long Strangle":
        # short_strike = put strike (lower), long_strike = call strike (upper)
        legs = [_leg("call", lt, +1, l_mid, l_iv),
                _leg("put",  st, +1, s_mid, s_iv)]

    elif strategy == "Risk Reversal":
        # short_strike = put (sold), long_strike = call (bought)
        legs = [_leg("call", lt, +1, l_mid, l_iv),
                _leg("put",  st, -1, s_mid, s_iv)]

    elif strategy in ("Iron Condor", "Iron Butterfly", "Broken-Wing Butterfly"):
        st2 = row.get("short_strike2", float("nan"))
        lt2 = row.get("long_strike2", float("nan"))
        s_mid2 = row.get("short_mid2", 0.0)
        l_mid2 = row.get("long_mid2", 0.0)
        if not (math.isnan(st2) or math.isnan(lt2)):
            # put spread legs + call spread legs
            legs = [_leg("put", lt, +1, l_mid, l_iv),
                    _leg("put", st, -1, s_mid, s_iv),
                    _leg("call", st2, -1, s_mid2, s_iv),
                    _leg("call", lt2, +1, l_mid2, l_iv)]
        else:
            legs = [_leg(s_type, st, -1, s_mid, s_iv),
                    _leg(l_type, lt, +1, l_mid, l_iv)]

    elif strategy == "Jade Lizard":
        st2 = row.get("short_strike2", float("nan"))
        lt2 = row.get("long_strike2", float("nan"))
        s_mid2 = row.get("short_mid2", 0.0)
        l_mid2 = row.get("long_mid2", 0.0)
        legs = [_leg("put", st, -1, s_mid, s_iv)]
        if not math.isnan(st2):
            legs += [_leg("call", st2, -1, s_mid2, s_iv),
                     _leg("call", lt2, +1, l_mid2, l_iv)]

    elif strategy == "Calendar / Diagonal":
        legs = [_leg(s_type, st, -1, s_mid, s_iv),
                _leg(l_type, lt, +1, l_mid, l_iv)]

    elif strategy == "Ratio Spread (1×2)":
        legs = [_leg(l_type, lt, +1, l_mid, l_iv),
                _leg(s_type, st, -1, s_mid, s_iv),
                _leg(s_type, st, -1, s_mid, s_iv)]

    return legs


# ── Greek aggregation ─────────────────────────────────────────────────────────

def _spread_greeks(legs: list[dict], spot: float, T: float) -> dict:
    """T is the default time-to-expiry; legs may override via leg['T']."""
    nd = ng = nt = nv = 0.0
    for leg in legs:
        qty = leg["qty"]
        K, iv, ot = leg["strike"], max(leg["iv"], 0.01), leg["type"]
        leg_T = leg.get("T", T)  # per-leg override (used by calendar spreads)
        if leg_T <= 0 or iv < 0.001:
            continue
        d1, d2 = _d1d2(spot, K, leg_T, iv)
        delta = _norm_cdf(d1) if ot == "call" else _norm_cdf(d1) - 1.0
        gamma = _norm_pdf(d1) / (spot * iv * math.sqrt(leg_T))
        theta = _bs_theta(spot, K, leg_T, iv, ot)
        vega = _bs_vega(spot, K, leg_T, iv)
        nd += qty * delta
        ng += qty * gamma
        nt += qty * theta
        nv += qty * vega
    return {"net_delta": nd, "net_gamma": ng, "net_theta": nt, "net_vega": nv}


# ── Earnings flag ─────────────────────────────────────────────────────────────

def _has_earnings(expiration: str, earnings_dates: list) -> bool:
    if not earnings_dates:
        return False
    # Calendar spreads use "front→back" format; use the front (earlier) date
    exp_str = expiration.split("→")[0]
    exp = datetime.strptime(exp_str, "%Y-%m-%d").date()
    today = date.today()
    return any(today < e <= exp for e in earnings_dates)


# ── Common row finaliser ──────────────────────────────────────────────────────

def _finalise(rows: list[dict], spot: float, earnings_dates: list) -> pd.DataFrame:
    if not rows:
        return _empty()
    df = pd.DataFrame(rows)
    # Greeks
    for col in ("net_delta", "net_gamma", "net_theta", "net_vega"):
        if col not in df.columns:
            df[col] = 0.0
    df["positive_theta"] = df["net_theta"] > 0
    df["positive_vega"] = df["net_vega"] > 0
    # EV
    df["expected_value"] = (df["pop"] * df["max_profit"]
                            - (1 - df["pop"]) * df["max_loss"])
    # BE move %
    df["be_move_pct"] = (df["breakeven1"] - spot).abs() / spot * 100
    # POT (credit spreads only; else NaN)
    df["pot"] = (2 * (1 - df["pop"])).clip(0, 1)
    # Earnings
    df["earnings_in_window"] = df["expiration"].apply(
        lambda e: _has_earnings(e, earnings_dates)
    )
    # Fill optional columns
    for col in SPREAD_COLS:
        if col not in df.columns:
            df[col] = float("nan")
    return df[SPREAD_COLS].reset_index(drop=True)


# ── Builder: Bull Put Spread ──────────────────────────────────────────────────

def build_bull_put_spreads(df, min_dte, max_dte, min_width, max_width,
                            min_oi, earnings_dates=None):
    puts = df[(df["type"] == "put") &
              (df["dte"] >= min_dte) & (df["dte"] <= max_dte) &
              (df["open_interest"] >= min_oi) &
              (df["delta"].abs() >= 0.04) & (df["delta"].abs() <= 0.55)].copy()
    if puts.empty:
        return _empty()

    m = puts.merge(puts, on="expiration", suffixes=("_s", "_l"))
    m = m[m["strike_s"] > m["strike_l"]]
    w = m["strike_s"] - m["strike_l"]
    m = m[w.between(min_width, max_width)]
    if m.empty:
        return _empty()

    spot = float(df["spot"].iloc[0])
    m["net_credit"] = m["mid_s"] - m["mid_l"]
    m = m[m["net_credit"] > 0.01]
    w = m["strike_s"] - m["strike_l"]
    m["max_profit"] = m["net_credit"]
    m["max_loss"] = w - m["net_credit"]
    m = m[m["max_loss"] > 0.01]
    m["risk_reward"] = m["max_profit"] / m["max_loss"]
    m["breakeven1"] = m["strike_s"] - m["net_credit"]
    m["breakeven2"] = float("nan")
    m["ann_yield_pct"] = m["net_credit"] / m["max_loss"] * (365 / m["dte_s"]) * 100

    rows = []
    for _, r in m.iterrows():
        T = _T(int(r["dte_s"]))
        pop = prob_above(spot, r["breakeven1"], T, r["iv_s"])
        legs = [{"type": "put", "strike": r["strike_s"], "qty": -1,
                 "entry_mid": r["mid_s"], "iv": r["iv_s"]},
                {"type": "put", "strike": r["strike_l"], "qty": +1,
                 "entry_mid": r["mid_l"], "iv": r["iv_l"]}]
        g = _spread_greeks(legs, spot, T)
        rows.append({
            "strategy": "Bull Put Spread", "expiration": r["expiration"],
            "dte": int(r["dte_s"]), "spot": spot,
            "short_strike": r["strike_s"], "long_strike": r["strike_l"],
            "short_type": "put", "long_type": "put",
            "short_mid": r["mid_s"], "long_mid": r["mid_l"],
            "short_iv": r["iv_s"], "long_iv": r["iv_l"],
            "net_credit": r["net_credit"],
            "max_profit": r["max_profit"], "max_loss": r["max_loss"],
            "risk_reward": r["risk_reward"], "pop": pop,
            "ann_yield_pct": r["ann_yield_pct"],
            "breakeven1": r["breakeven1"], "breakeven2": float("nan"),
            "short_iv_excess": r.get("iv_excess_s", 0.0),
            "short_oi": int(r["open_interest_s"]),
            "long_oi": int(r["open_interest_l"]),
            **g,
        })
    return _finalise(rows, spot, earnings_dates or [])


# ── Builder: Bear Call Spread ─────────────────────────────────────────────────

def build_bear_call_spreads(df, min_dte, max_dte, min_width, max_width,
                             min_oi, earnings_dates=None):
    calls = df[(df["type"] == "call") &
               (df["dte"] >= min_dte) & (df["dte"] <= max_dte) &
               (df["open_interest"] >= min_oi) &
               (df["delta"] >= 0.04) & (df["delta"] <= 0.55)].copy()
    if calls.empty:
        return _empty()

    m = calls.merge(calls, on="expiration", suffixes=("_s", "_l"))
    m = m[m["strike_s"] < m["strike_l"]]
    w = m["strike_l"] - m["strike_s"]
    m = m[w.between(min_width, max_width)]
    if m.empty:
        return _empty()

    spot = float(df["spot"].iloc[0])
    m["net_credit"] = m["mid_s"] - m["mid_l"]
    m = m[m["net_credit"] > 0.01]
    w = m["strike_l"] - m["strike_s"]
    m["max_profit"] = m["net_credit"]
    m["max_loss"] = w - m["net_credit"]
    m = m[m["max_loss"] > 0.01]
    m["risk_reward"] = m["max_profit"] / m["max_loss"]
    m["breakeven1"] = m["strike_s"] + m["net_credit"]
    m["ann_yield_pct"] = m["net_credit"] / m["max_loss"] * (365 / m["dte_s"]) * 100

    rows = []
    for _, r in m.iterrows():
        T = _T(int(r["dte_s"]))
        pop = 1 - prob_above(spot, r["breakeven1"], T, r["iv_s"])
        legs = [{"type": "call", "strike": r["strike_s"], "qty": -1,
                 "entry_mid": r["mid_s"], "iv": r["iv_s"]},
                {"type": "call", "strike": r["strike_l"], "qty": +1,
                 "entry_mid": r["mid_l"], "iv": r["iv_l"]}]
        g = _spread_greeks(legs, spot, T)
        rows.append({
            "strategy": "Bear Call Spread", "expiration": r["expiration"],
            "dte": int(r["dte_s"]), "spot": spot,
            "short_strike": r["strike_s"], "long_strike": r["strike_l"],
            "short_type": "call", "long_type": "call",
            "short_mid": r["mid_s"], "long_mid": r["mid_l"],
            "short_iv": r["iv_s"], "long_iv": r["iv_l"],
            "net_credit": r["net_credit"],
            "max_profit": r["max_profit"], "max_loss": r["max_loss"],
            "risk_reward": r["risk_reward"], "pop": pop,
            "ann_yield_pct": r["ann_yield_pct"],
            "breakeven1": r["breakeven1"], "breakeven2": float("nan"),
            "short_iv_excess": r.get("iv_excess_s", 0.0),
            "short_oi": int(r["open_interest_s"]),
            "long_oi": int(r["open_interest_l"]),
            **g,
        })
    return _finalise(rows, spot, earnings_dates or [])


# ── Builder: Bull Call Spread ─────────────────────────────────────────────────

def build_bull_call_spreads(df, min_dte, max_dte, min_width, max_width,
                             min_oi, earnings_dates=None):
    calls = df[(df["type"] == "call") &
               (df["dte"] >= min_dte) & (df["dte"] <= max_dte) &
               (df["open_interest"] >= min_oi) &
               (df["delta"] >= 0.04) & (df["delta"] <= 0.70)].copy()
    if calls.empty:
        return _empty()

    m = calls.merge(calls, on="expiration", suffixes=("_l", "_s"))
    m = m[m["strike_l"] < m["strike_s"]]
    w = m["strike_s"] - m["strike_l"]
    m = m[w.between(min_width, max_width)]
    if m.empty:
        return _empty()

    spot = float(df["spot"].iloc[0])
    m["net_debit"] = m["mid_l"] - m["mid_s"]
    m = m[m["net_debit"] > 0.01]
    w = m["strike_s"] - m["strike_l"]
    m["max_profit"] = w - m["net_debit"]
    m["max_loss"] = m["net_debit"]
    m = m[(m["max_profit"] > 0.01) & (m["max_loss"] > 0.01)]
    m["risk_reward"] = m["max_profit"] / m["max_loss"]
    m["breakeven1"] = m["strike_l"] + m["net_debit"]
    m["ann_yield_pct"] = m["max_profit"] / m["max_loss"] * (365 / m["dte_l"]) * 100

    rows = []
    for _, r in m.iterrows():
        T = _T(int(r["dte_l"]))
        pop = prob_above(spot, r["breakeven1"], T, r["iv_l"])
        legs = [{"type": "call", "strike": r["strike_l"], "qty": +1,
                 "entry_mid": r["mid_l"], "iv": r["iv_l"]},
                {"type": "call", "strike": r["strike_s"], "qty": -1,
                 "entry_mid": r["mid_s"], "iv": r["iv_s"]}]
        g = _spread_greeks(legs, spot, T)
        rows.append({
            "strategy": "Bull Call Spread", "expiration": r["expiration"],
            "dte": int(r["dte_l"]), "spot": spot,
            "short_strike": r["strike_s"], "long_strike": r["strike_l"],
            "short_type": "call", "long_type": "call",
            "short_mid": r["mid_s"], "long_mid": r["mid_l"],
            "short_iv": r["iv_s"], "long_iv": r["iv_l"],
            "net_credit": -r["net_debit"],
            "max_profit": r["max_profit"], "max_loss": r["max_loss"],
            "risk_reward": r["risk_reward"], "pop": pop,
            "ann_yield_pct": r["ann_yield_pct"],
            "breakeven1": r["breakeven1"], "breakeven2": float("nan"),
            "short_iv_excess": r.get("iv_excess_s", 0.0),
            "short_oi": int(r["open_interest_s"]),
            "long_oi": int(r["open_interest_l"]),
            **g,
        })
    return _finalise(rows, spot, earnings_dates or [])


# ── Builder: Bear Put Spread ──────────────────────────────────────────────────

def build_bear_put_spreads(df, min_dte, max_dte, min_width, max_width,
                            min_oi, earnings_dates=None):
    puts = df[(df["type"] == "put") &
              (df["dte"] >= min_dte) & (df["dte"] <= max_dte) &
              (df["open_interest"] >= min_oi) &
              (df["delta"].abs() >= 0.04) & (df["delta"].abs() <= 0.70)].copy()
    if puts.empty:
        return _empty()

    m = puts.merge(puts, on="expiration", suffixes=("_l", "_s"))
    m = m[m["strike_l"] > m["strike_s"]]
    w = m["strike_l"] - m["strike_s"]
    m = m[w.between(min_width, max_width)]
    if m.empty:
        return _empty()

    spot = float(df["spot"].iloc[0])
    m["net_debit"] = m["mid_l"] - m["mid_s"]
    m = m[m["net_debit"] > 0.01]
    w = m["strike_l"] - m["strike_s"]
    m["max_profit"] = w - m["net_debit"]
    m["max_loss"] = m["net_debit"]
    m = m[(m["max_profit"] > 0.01) & (m["max_loss"] > 0.01)]
    m["risk_reward"] = m["max_profit"] / m["max_loss"]
    m["breakeven1"] = m["strike_l"] - m["net_debit"]
    m["ann_yield_pct"] = m["max_profit"] / m["max_loss"] * (365 / m["dte_l"]) * 100

    rows = []
    for _, r in m.iterrows():
        T = _T(int(r["dte_l"]))
        pop = 1 - prob_above(spot, r["breakeven1"], T, r["iv_l"])
        legs = [{"type": "put", "strike": r["strike_l"], "qty": +1,
                 "entry_mid": r["mid_l"], "iv": r["iv_l"]},
                {"type": "put", "strike": r["strike_s"], "qty": -1,
                 "entry_mid": r["mid_s"], "iv": r["iv_s"]}]
        g = _spread_greeks(legs, spot, T)
        rows.append({
            "strategy": "Bear Put Spread", "expiration": r["expiration"],
            "dte": int(r["dte_l"]), "spot": spot,
            "short_strike": r["strike_s"], "long_strike": r["strike_l"],
            "short_type": "put", "long_type": "put",
            "short_mid": r["mid_s"], "long_mid": r["mid_l"],
            "short_iv": r["iv_s"], "long_iv": r["iv_l"],
            "net_credit": -r["net_debit"],
            "max_profit": r["max_profit"], "max_loss": r["max_loss"],
            "risk_reward": r["risk_reward"], "pop": pop,
            "ann_yield_pct": r["ann_yield_pct"],
            "breakeven1": r["breakeven1"], "breakeven2": float("nan"),
            "short_iv_excess": r.get("iv_excess_s", 0.0),
            "short_oi": int(r["open_interest_s"]),
            "long_oi": int(r["open_interest_l"]),
            **g,
        })
    return _finalise(rows, spot, earnings_dates or [])


# ── Builder: Iron Condor ──────────────────────────────────────────────────────

def build_iron_condors(df, min_dte, max_dte, min_width, max_width,
                       min_oi, earnings_dates=None):
    spot = float(df["spot"].iloc[0])

    puts = df[(df["type"] == "put") &
              (df["dte"] >= min_dte) & (df["dte"] <= max_dte) &
              (df["open_interest"] >= min_oi) &
              (df["delta"].abs() >= 0.04) & (df["delta"].abs() <= 0.45)].copy()
    calls = df[(df["type"] == "call") &
               (df["dte"] >= min_dte) & (df["dte"] <= max_dte) &
               (df["open_interest"] >= min_oi) &
               (df["delta"] >= 0.04) & (df["delta"] <= 0.45)].copy()
    if puts.empty or calls.empty:
        return _empty()

    # Build put spreads (short higher put, long lower put)
    pm = puts.merge(puts, on="expiration", suffixes=("_sp", "_lp"))
    pm = pm[pm["strike_sp"] > pm["strike_lp"]]
    pw = pm["strike_sp"] - pm["strike_lp"]
    pm = pm[pw.between(min_width, max_width)]
    pm["put_credit"] = pm["mid_sp"] - pm["mid_lp"]
    pm = pm[pm["put_credit"] > 0.01]

    # Build call spreads (short lower call, long higher call)
    cm = calls.merge(calls, on="expiration", suffixes=("_sc", "_lc"))
    cm = cm[cm["strike_sc"] < cm["strike_lc"]]
    cw = cm["strike_lc"] - cm["strike_sc"]
    cm = cm[cw.between(min_width, max_width)]
    cm["call_credit"] = cm["mid_sc"] - cm["mid_lc"]
    cm = cm[cm["call_credit"] > 0.01]

    # Combine
    combo = pm.merge(cm, on="expiration")
    combo = combo[combo["strike_sc"] > combo["strike_sp"]]  # no overlap
    if combo.empty:
        return _empty()

    rows = []
    for _, r in combo.iterrows():
        net_credit = r["put_credit"] + r["call_credit"]
        put_width = r["strike_sp"] - r["strike_lp"]
        call_width = r["strike_lc"] - r["strike_sc"]
        max_loss = max(put_width, call_width) - net_credit
        if max_loss <= 0.01 or net_credit <= 0.01:
            continue
        be1 = r["strike_sp"] - net_credit
        be2 = r["strike_sc"] + net_credit
        T = _T(int(r["dte_sp"]))
        iv_avg = (r["iv_sp"] + r["iv_sc"]) / 2
        pop = max(0.0, prob_above(spot, be1, T, iv_avg)
                  - prob_above(spot, be2, T, iv_avg))
        legs = [
            {"type": "put",  "strike": r["strike_lp"], "qty": +1, "entry_mid": r["mid_lp"], "iv": r["iv_lp"]},
            {"type": "put",  "strike": r["strike_sp"], "qty": -1, "entry_mid": r["mid_sp"], "iv": r["iv_sp"]},
            {"type": "call", "strike": r["strike_sc"], "qty": -1, "entry_mid": r["mid_sc"], "iv": r["iv_sc"]},
            {"type": "call", "strike": r["strike_lc"], "qty": +1, "entry_mid": r["mid_lc"], "iv": r["iv_lc"]},
        ]
        g = _spread_greeks(legs, spot, T)
        rows.append({
            "strategy": "Iron Condor", "expiration": r["expiration"],
            "dte": int(r["dte_sp"]), "spot": spot,
            "short_strike": r["strike_sp"], "long_strike": r["strike_lp"],
            "short_strike2": r["strike_sc"], "long_strike2": r["strike_lc"],
            "short_type": "put", "long_type": "put",
            "short_mid": r["mid_sp"], "long_mid": r["mid_lp"],
            "short_mid2": r["mid_sc"], "long_mid2": r["mid_lc"],
            "short_iv": r["iv_sp"], "long_iv": r["iv_lp"],
            "net_credit": net_credit,
            "max_profit": net_credit, "max_loss": max_loss,
            "risk_reward": net_credit / max_loss,
            "pop": pop,
            "ann_yield_pct": net_credit / max_loss * (365 / r["dte_sp"]) * 100,
            "breakeven1": be1, "breakeven2": be2,
            "short_iv_excess": r.get("iv_excess_sp", 0.0),
            "short_oi": int(r["open_interest_sp"]),
            "long_oi": int(r["open_interest_lp"]),
            **g,
        })
    return _finalise(rows, spot, earnings_dates or [])


# ── Builder: Iron Butterfly ───────────────────────────────────────────────────

def build_iron_butterflies(df, min_dte, max_dte, min_width, max_width,
                            min_oi, earnings_dates=None):
    spot = float(df["spot"].iloc[0])
    sub = df[(df["dte"] >= min_dte) & (df["dte"] <= max_dte) &
             (df["open_interest"] >= min_oi)].copy()
    if sub.empty:
        return _empty()

    rows = []
    for exp, grp in sub.groupby("expiration"):
        dte = int(grp["dte"].iloc[0])
        T = _T(dte)
        calls = grp[grp["type"] == "call"].sort_values("strike")
        puts = grp[grp["type"] == "put"].sort_values("strike")
        if calls.empty or puts.empty:
            continue

        # ATM body: call closest to spot
        body_call = calls.iloc[(calls["strike"] - spot).abs().argsort()[:1]]
        body_put = puts.iloc[(puts["strike"] - spot).abs().argsort()[:1]]
        if body_call.empty or body_put.empty:
            continue
        Kc = float(body_call["strike"].iloc[0])
        Kp = float(body_put["strike"].iloc[0])
        K_body = (Kc + Kp) / 2

        for wing in [w for w in [min_width, (min_width + max_width) / 2, max_width]
                     if min_width <= w <= max_width]:
            K_put_wing = K_body - wing
            K_call_wing = K_body + wing

            # Find nearest available strikes for wings
            lp_candidates = puts[puts["strike"] <= K_body - min_width * 0.5]
            lc_candidates = calls[calls["strike"] >= K_body + min_width * 0.5]
            if lp_candidates.empty or lc_candidates.empty:
                continue

            lp_row = lp_candidates.iloc[(lp_candidates["strike"] - K_put_wing).abs().argsort()[:1]].iloc[0]
            lc_row = lc_candidates.iloc[(lc_candidates["strike"] - K_call_wing).abs().argsort()[:1]].iloc[0]
            sp_row = body_put.iloc[0]
            sc_row = body_call.iloc[0]

            if (sp_row["open_interest"] < min_oi or sc_row["open_interest"] < min_oi
                    or lp_row["open_interest"] < min_oi or lc_row["open_interest"] < min_oi):
                continue

            net_credit = (sp_row["mid"] + sc_row["mid"]
                          - lp_row["mid"] - lc_row["mid"])
            actual_wing = min(float(sp_row["strike"]) - float(lp_row["strike"]),
                              float(lc_row["strike"]) - float(sc_row["strike"]))
            max_loss = actual_wing - net_credit
            if net_credit <= 0.01 or max_loss <= 0.01:
                continue

            be1 = float(sp_row["strike"]) - net_credit
            be2 = float(sc_row["strike"]) + net_credit
            iv_body = (float(sp_row["iv"]) + float(sc_row["iv"])) / 2
            pop = max(0.0, prob_above(spot, be1, T, iv_body)
                      - prob_above(spot, be2, T, iv_body))

            legs = [
                {"type": "put",  "strike": float(lp_row["strike"]), "qty": +1, "entry_mid": float(lp_row["mid"]), "iv": float(lp_row["iv"])},
                {"type": "put",  "strike": float(sp_row["strike"]), "qty": -1, "entry_mid": float(sp_row["mid"]), "iv": float(sp_row["iv"])},
                {"type": "call", "strike": float(sc_row["strike"]), "qty": -1, "entry_mid": float(sc_row["mid"]), "iv": float(sc_row["iv"])},
                {"type": "call", "strike": float(lc_row["strike"]), "qty": +1, "entry_mid": float(lc_row["mid"]), "iv": float(lc_row["iv"])},
            ]
            g = _spread_greeks(legs, spot, T)

            put_wing = float(sp_row["strike"]) - float(lp_row["strike"])
            call_wing = float(lc_row["strike"]) - float(sc_row["strike"])
            strike_match = abs(float(sp_row["strike"]) - float(sc_row["strike"])) < 0.01
            wing_match = abs(put_wing - call_wing) < 0.01
            label = ("Iron Butterfly" if (strike_match and wing_match)
                     else "Broken-Wing Butterfly")

            rows.append({
                "strategy": label, "expiration": exp,
                "dte": dte, "spot": spot,
                "short_strike": float(sp_row["strike"]),
                "long_strike": float(lp_row["strike"]),
                "short_strike2": float(sc_row["strike"]),
                "long_strike2": float(lc_row["strike"]),
                "short_type": "put", "long_type": "put",
                "short_mid": float(sp_row["mid"]), "long_mid": float(lp_row["mid"]),
                "short_mid2": float(sc_row["mid"]), "long_mid2": float(lc_row["mid"]),
                "short_iv": float(sp_row["iv"]), "long_iv": float(lp_row["iv"]),
                "net_credit": net_credit,
                "max_profit": net_credit, "max_loss": max_loss,
                "risk_reward": net_credit / max_loss,
                "pop": pop,
                "ann_yield_pct": net_credit / max_loss * (365 / dte) * 100,
                "breakeven1": be1, "breakeven2": be2,
                "short_iv_excess": float(sp_row.get("iv_excess", 0.0)),
                "short_oi": int(sp_row["open_interest"]),
                "long_oi": int(lp_row["open_interest"]),
                **g,
            })
            break  # one butterfly per expiration is enough

    return _finalise(rows, spot, earnings_dates or [])


# ── Builder: Jade Lizard ──────────────────────────────────────────────────────

def build_jade_lizards(df, min_dte, max_dte, min_width, max_width,
                       min_oi, earnings_dates=None):
    spot = float(df["spot"].iloc[0])
    puts = df[(df["type"] == "put") &
              (df["dte"] >= min_dte) & (df["dte"] <= max_dte) &
              (df["open_interest"] >= min_oi) &
              (df["delta"].abs() >= 0.10) & (df["delta"].abs() <= 0.40) &
              (df["strike"] < spot)].copy()
    calls = df[(df["type"] == "call") &
               (df["dte"] >= min_dte) & (df["dte"] <= max_dte) &
               (df["open_interest"] >= min_oi) &
               (df["delta"] >= 0.04) & (df["delta"] <= 0.40) &
               (df["strike"] > spot)].copy()
    if puts.empty or calls.empty:
        return _empty()

    # Short call spread: short lower call, long higher call
    csm = calls.merge(calls, on="expiration", suffixes=("_sc", "_lc"))
    csm = csm[csm["strike_sc"] < csm["strike_lc"]]
    csw = csm["strike_lc"] - csm["strike_sc"]
    csm = csm[csw.between(min_width, max_width)]
    csm["call_credit"] = csm["mid_sc"] - csm["mid_lc"]
    csm = csm[csm["call_credit"] > 0]

    combo = puts.merge(csm, on="expiration")
    if combo.empty:
        return _empty()

    rows = []
    for _, r in combo.iterrows():
        net_credit = r["mid"] + r["call_credit"]
        call_width = r["strike_lc"] - r["strike_sc"]
        if net_credit <= call_width + 0.01:
            continue  # not a valid jade lizard
        max_profit = net_credit
        max_loss = float(r["strike"]) - net_credit  # downside: put goes to 0
        if max_loss <= 0.01:
            continue
        be1 = float(r["strike"]) - net_credit
        T = _T(int(r["dte"]))
        pop = prob_above(spot, be1, T, float(r["iv"]))
        legs = [
            {"type": "put",  "strike": float(r["strike"]),    "qty": -1, "entry_mid": float(r["mid"]),    "iv": float(r["iv"])},
            {"type": "call", "strike": float(r["strike_sc"]), "qty": -1, "entry_mid": float(r["mid_sc"]), "iv": float(r["iv_sc"])},
            {"type": "call", "strike": float(r["strike_lc"]), "qty": +1, "entry_mid": float(r["mid_lc"]), "iv": float(r["iv_lc"])},
        ]
        g = _spread_greeks(legs, spot, T)
        rows.append({
            "strategy": "Jade Lizard", "expiration": r["expiration"],
            "dte": int(r["dte"]), "spot": spot,
            "short_strike": float(r["strike"]), "long_strike": float(r["strike_lc"]),
            "short_strike2": float(r["strike_sc"]), "long_strike2": float(r["strike_lc"]),
            "short_type": "put", "long_type": "call",
            "short_mid": float(r["mid"]), "long_mid": float(r["mid_lc"]),
            "short_mid2": float(r["mid_sc"]), "long_mid2": float(r["mid_lc"]),
            "short_iv": float(r["iv"]), "long_iv": float(r["iv_lc"]),
            "net_credit": net_credit,
            "max_profit": max_profit, "max_loss": max_loss,
            "risk_reward": max_profit / max_loss,
            "pop": pop,
            "ann_yield_pct": net_credit / max_loss * (365 / r["dte"]) * 100,
            "breakeven1": be1, "breakeven2": float("nan"),
            "short_iv_excess": float(r.get("iv_excess", 0.0)),
            "short_oi": int(r["open_interest"]),
            "long_oi": int(r["open_interest_lc"]),
            **g,
        })
    return _finalise(rows, spot, earnings_dates or [])


# ── Builder: Calendar / Diagonal ─────────────────────────────────────────────

def build_calendar_spreads(df, min_dte, max_dte, min_width, max_width,
                            min_oi, earnings_dates=None):
    spot = float(df["spot"].iloc[0])
    # Near-ATM only (within 10% of spot)
    sub = df[(df["open_interest"] >= min_oi) &
             (df["strike"].between(spot * 0.90, spot * 1.10))].copy()
    if sub.empty:
        return _empty()

    exps = sorted(sub["expiration"].unique())
    if len(exps) < 2:
        return _empty()

    rows = []
    for i, exp_front in enumerate(exps):
        if not (min_dte <= sub[sub["expiration"] == exp_front]["dte"].iloc[0] <= max_dte):
            continue
        for exp_back in exps[i + 1:]:
            dte_front = int(sub[sub["expiration"] == exp_front]["dte"].iloc[0])
            dte_back = int(sub[sub["expiration"] == exp_back]["dte"].iloc[0])
            if dte_back < dte_front + 14:
                continue
            # Allow back month up to (front + max(max_dte, 365)) so
            # LEAPS-style calendars qualify on long-DTE searches
            if dte_back > dte_front + max(max_dte, 365):
                continue

            front = sub[sub["expiration"] == exp_front]
            back = sub[sub["expiration"] == exp_back]

            for opt_type in ("call", "put"):
                f = front[front["type"] == opt_type]
                b = back[back["type"] == opt_type]
                if f.empty or b.empty:
                    continue

                # Match by nearest strike
                f_strikes = set(f["strike"].tolist())
                b_strikes = set(b["strike"].tolist())
                common = f_strikes & b_strikes
                if not common:
                    # use nearest available
                    all_strikes = sorted(f_strikes | b_strikes,
                                         key=lambda k: abs(k - spot))
                    common = {all_strikes[0]}

                for K in list(common)[:3]:  # limit iterations
                    f_row = f.iloc[(f["strike"] - K).abs().argsort()[:1]].iloc[0]
                    b_row = b.iloc[(b["strike"] - K).abs().argsort()[:1]].iloc[0]

                    net_debit = float(b_row["mid"]) - float(f_row["mid"])
                    if net_debit <= 0.01:
                        continue

                    T_back_remaining = _T(dte_back - dte_front)
                    est_back_val = _bs_price(spot, float(b_row["strike"]),
                                             T_back_remaining, float(b_row["iv"]),
                                             opt_type)
                    max_profit = max(0.01, est_back_val - net_debit)
                    max_loss = net_debit
                    if max_loss <= 0.01:
                        continue

                    T = _T(dte_front)
                    if opt_type == "call":
                        pop = prob_above(spot, float(f_row["strike"]), T, float(b_row["iv"]))
                    else:
                        pop = 1 - prob_above(spot, float(f_row["strike"]), T, float(b_row["iv"]))

                    T_back = _T(dte_back)
                    legs = [
                        {"type": opt_type, "strike": float(f_row["strike"]), "qty": -1,
                         "entry_mid": float(f_row["mid"]), "iv": float(f_row["iv"]),
                         "T": T},
                        {"type": opt_type, "strike": float(b_row["strike"]), "qty": +1,
                         "entry_mid": float(b_row["mid"]), "iv": float(b_row["iv"]),
                         "T": T_back},
                    ]
                    g = _spread_greeks(legs, spot, T)
                    rows.append({
                        "strategy": "Calendar / Diagonal",
                        "expiration": f"{exp_front}→{exp_back}",
                        "dte": dte_front, "spot": spot,
                        "short_strike": float(f_row["strike"]),
                        "long_strike": float(b_row["strike"]),
                        "short_type": opt_type, "long_type": opt_type,
                        "short_mid": float(f_row["mid"]),
                        "long_mid": float(b_row["mid"]),
                        "short_iv": float(f_row["iv"]),
                        "long_iv": float(b_row["iv"]),
                        "net_credit": -net_debit,
                        "max_profit": max_profit, "max_loss": max_loss,
                        "risk_reward": max_profit / max_loss,
                        "pop": min(max(pop, 0.0), 1.0),
                        "ann_yield_pct": max_profit / max_loss * (365 / dte_front) * 100,
                        "breakeven1": float(f_row["strike"]),
                        "breakeven2": float("nan"),
                        "short_iv_excess": float(f_row.get("iv_excess", 0.0)),
                        "short_oi": int(f_row["open_interest"]),
                        "long_oi": int(b_row["open_interest"]),
                        **g,
                    })

    return _finalise(rows, spot, earnings_dates or [])


# ── Builder: Ratio Spread 1×2 ─────────────────────────────────────────────────

def build_ratio_spreads(df, min_dte, max_dte, min_width, max_width,
                        min_oi, earnings_dates=None):
    spot = float(df["spot"].iloc[0])
    rows = []
    for opt_type in ("call", "put"):
        sub = df[(df["type"] == opt_type) &
                 (df["dte"] >= min_dte) & (df["dte"] <= max_dte) &
                 (df["open_interest"] >= min_oi) &
                 (df["delta"].abs() >= 0.05) & (df["delta"].abs() <= 0.60)].copy()
        if sub.empty:
            continue

        m = sub.merge(sub, on="expiration", suffixes=("_l", "_s"))
        if opt_type == "call":
            m = m[m["strike_l"] < m["strike_s"]]
        else:
            m = m[m["strike_l"] > m["strike_s"]]
        w = (m["strike_s"] - m["strike_l"]).abs()
        m = m[w.between(min_width, max_width)]
        if m.empty:
            continue

        for _, r in m.iterrows():
            net_credit = 2 * float(r["mid_s"]) - float(r["mid_l"])
            width = abs(float(r["strike_s"]) - float(r["strike_l"]))
            max_profit = width + net_credit if net_credit >= 0 else width - abs(net_credit)
            # Cap max loss at 5× width for ranking purposes
            max_loss_cap = 5 * width
            if max_profit <= 0.01:
                continue

            T = _T(int(r["dte_l"]))
            if opt_type == "call":
                if net_credit >= 0:
                    upper_be = float(r["strike_s"]) + max_profit
                    lower_be = float(r["strike_l"]) + net_credit
                else:
                    upper_be = float(r["strike_s"]) + width - abs(net_credit)
                    lower_be = float(r["strike_l"]) - abs(net_credit)
                iv_avg = (float(r["iv_l"]) + float(r["iv_s"])) / 2
                pop = max(0.0, prob_above(spot, lower_be, T, iv_avg)
                          - prob_above(spot, upper_be, T, iv_avg))
                be1 = lower_be
            else:
                if net_credit >= 0:
                    lower_be = float(r["strike_s"]) - max_profit
                    upper_be = float(r["strike_l"]) - net_credit
                else:
                    lower_be = float(r["strike_s"]) - (width - abs(net_credit))
                    upper_be = float(r["strike_l"]) + abs(net_credit)
                iv_avg = (float(r["iv_l"]) + float(r["iv_s"])) / 2
                pop = max(0.0, prob_above(spot, lower_be, T, iv_avg)
                          - prob_above(spot, upper_be, T, iv_avg))
                be1 = upper_be

            legs = [
                {"type": opt_type, "strike": float(r["strike_l"]), "qty": +1,
                 "entry_mid": float(r["mid_l"]), "iv": float(r["iv_l"])},
                {"type": opt_type, "strike": float(r["strike_s"]), "qty": -1,
                 "entry_mid": float(r["mid_s"]), "iv": float(r["iv_s"])},
                {"type": opt_type, "strike": float(r["strike_s"]), "qty": -1,
                 "entry_mid": float(r["mid_s"]), "iv": float(r["iv_s"])},
            ]
            g = _spread_greeks(legs, spot, T)
            label = "Call" if opt_type == "call" else "Put"
            rows.append({
                "strategy": "Ratio Spread (1×2)", "expiration": r["expiration"],
                "dte": int(r["dte_l"]), "spot": spot,
                "short_strike": float(r["strike_s"]),
                "long_strike": float(r["strike_l"]),
                "short_type": opt_type, "long_type": opt_type,
                "short_mid": float(r["mid_s"]), "long_mid": float(r["mid_l"]),
                "short_iv": float(r["iv_s"]), "long_iv": float(r["iv_l"]),
                "net_credit": net_credit,
                "max_profit": max_profit, "max_loss": max_loss_cap,
                "risk_reward": max_profit / max_loss_cap,
                "pop": min(max(pop, 0.0), 1.0),
                "ann_yield_pct": max_profit / max_loss_cap * (365 / r["dte_l"]) * 100,
                "breakeven1": be1, "breakeven2": upper_be if opt_type == "call" else lower_be,
                "short_iv_excess": float(r.get("iv_excess_s", 0.0)),
                "short_oi": int(r["open_interest_s"]),
                "long_oi": int(r["open_interest_l"]),
                **g,
            })

    return _finalise(rows, spot, earnings_dates or [])


# ── Builder: Long Straddle ────────────────────────────────────────────────────

def build_long_straddles(df, min_dte, max_dte, min_width, max_width,
                          min_oi, earnings_dates=None):
    """Long call + long put at same strike. Pure long-volatility play."""
    spot = float(df["spot"].iloc[0])
    sub = df[(df["dte"] >= min_dte) & (df["dte"] <= max_dte) &
             (df["open_interest"] >= min_oi)].copy()
    # Near-ATM only (within 5% of spot)
    sub = sub[(sub["strike"] - spot).abs() / spot < 0.05]
    if sub.empty:
        return _empty()

    calls = sub[sub["type"] == "call"]
    puts = sub[sub["type"] == "put"]
    if calls.empty or puts.empty:
        return _empty()

    # Match on (expiration, strike) — same strike for both legs
    m = calls.merge(puts, on=["expiration", "strike", "dte"],
                    suffixes=("_c", "_p"))
    if m.empty:
        return _empty()

    rows = []
    for _, r in m.iterrows():
        K = float(r["strike"])
        net_debit = float(r["mid_c"]) + float(r["mid_p"])
        if net_debit <= 0.01:
            continue
        max_loss = net_debit
        # Cap max profit at 3× debit for ranking (theoretical upside is unbounded)
        max_profit = 3 * net_debit
        be_lower = K - net_debit
        be_upper = K + net_debit
        dte = int(r["dte"])
        T = _T(dte)
        iv_c = float(r["iv_c"])
        iv_p = float(r["iv_p"])
        pop = (prob_above(spot, be_upper, T, iv_c)
               + (1 - prob_above(spot, be_lower, T, iv_p)))
        pop = min(max(pop, 0.0), 1.0)

        legs = [
            {"type": "call", "strike": K, "qty": +1,
             "entry_mid": float(r["mid_c"]), "iv": iv_c},
            {"type": "put",  "strike": K, "qty": +1,
             "entry_mid": float(r["mid_p"]), "iv": iv_p},
        ]
        g = _spread_greeks(legs, spot, T)
        rows.append({
            "strategy": "Long Straddle", "expiration": r["expiration"],
            "dte": dte, "spot": spot,
            "short_strike": K, "long_strike": K,
            "short_type": "call", "long_type": "put",
            "short_mid": float(r["mid_c"]), "long_mid": float(r["mid_p"]),
            "short_iv": iv_c, "long_iv": iv_p,
            "net_credit": -net_debit,
            "max_profit": max_profit, "max_loss": max_loss,
            "risk_reward": max_profit / max_loss,
            "pop": pop,
            "ann_yield_pct": max_profit / max_loss * (365 / dte) * 100,
            "breakeven1": be_lower, "breakeven2": be_upper,
            "short_iv_excess": float(r.get("iv_excess_c", 0.0)),
            "short_oi": int(r["open_interest_c"]),
            "long_oi": int(r["open_interest_p"]),
            **g,
        })
    return _finalise(rows, spot, earnings_dates or [])


# ── Builder: Long Strangle ────────────────────────────────────────────────────

def build_long_strangles(df, min_dte, max_dte, min_width, max_width,
                          min_oi, earnings_dates=None):
    """Long OTM call + long OTM put at different strikes. Cheaper long-vol."""
    spot = float(df["spot"].iloc[0])
    calls = df[(df["type"] == "call") &
               (df["dte"] >= min_dte) & (df["dte"] <= max_dte) &
               (df["open_interest"] >= min_oi) &
               (df["delta"] >= 0.10) & (df["delta"] <= 0.40) &
               (df["strike"] > spot)].copy()
    puts = df[(df["type"] == "put") &
              (df["dte"] >= min_dte) & (df["dte"] <= max_dte) &
              (df["open_interest"] >= min_oi) &
              (df["delta"].abs() >= 0.10) & (df["delta"].abs() <= 0.40) &
              (df["strike"] < spot)].copy()
    if calls.empty or puts.empty:
        return _empty()

    m = calls.merge(puts, on=["expiration", "dte"], suffixes=("_c", "_p"))
    if m.empty:
        return _empty()
    width = m["strike_c"] - m["strike_p"]
    m = m[width.between(min_width, max_width)]
    if m.empty:
        return _empty()

    rows = []
    for _, r in m.iterrows():
        K_c = float(r["strike_c"])
        K_p = float(r["strike_p"])
        net_debit = float(r["mid_c"]) + float(r["mid_p"])
        if net_debit <= 0.01:
            continue
        max_loss = net_debit
        max_profit = 3 * net_debit
        be_upper = K_c + net_debit
        be_lower = K_p - net_debit
        dte = int(r["dte"])
        T = _T(dte)
        iv_c = float(r["iv_c"])
        iv_p = float(r["iv_p"])
        pop = (prob_above(spot, be_upper, T, iv_c)
               + (1 - prob_above(spot, be_lower, T, iv_p)))
        pop = min(max(pop, 0.0), 1.0)

        legs = [
            {"type": "call", "strike": K_c, "qty": +1,
             "entry_mid": float(r["mid_c"]), "iv": iv_c},
            {"type": "put",  "strike": K_p, "qty": +1,
             "entry_mid": float(r["mid_p"]), "iv": iv_p},
        ]
        g = _spread_greeks(legs, spot, T)
        rows.append({
            "strategy": "Long Strangle", "expiration": r["expiration"],
            "dte": dte, "spot": spot,
            "short_strike": K_p, "long_strike": K_c,
            "short_type": "put", "long_type": "call",
            "short_mid": float(r["mid_p"]), "long_mid": float(r["mid_c"]),
            "short_iv": iv_p, "long_iv": iv_c,
            "net_credit": -net_debit,
            "max_profit": max_profit, "max_loss": max_loss,
            "risk_reward": max_profit / max_loss,
            "pop": pop,
            "ann_yield_pct": max_profit / max_loss * (365 / dte) * 100,
            "breakeven1": be_lower, "breakeven2": be_upper,
            "short_iv_excess": float(r.get("iv_excess_c", 0.0)),
            "short_oi": int(r["open_interest_p"]),
            "long_oi": int(r["open_interest_c"]),
            **g,
        })
    return _finalise(rows, spot, earnings_dates or [])


# ── Builder: Risk Reversal ────────────────────────────────────────────────────

def build_risk_reversals(df, min_dte, max_dte, min_width, max_width,
                          min_oi, earnings_dates=None):
    """Long OTM call + short OTM put, same expiration. Bullish synthetic long."""
    spot = float(df["spot"].iloc[0])
    calls = df[(df["type"] == "call") &
               (df["dte"] >= min_dte) & (df["dte"] <= max_dte) &
               (df["open_interest"] >= min_oi) &
               (df["delta"] >= 0.10) & (df["delta"] <= 0.40) &
               (df["strike"] > spot)].copy()
    puts = df[(df["type"] == "put") &
              (df["dte"] >= min_dte) & (df["dte"] <= max_dte) &
              (df["open_interest"] >= min_oi) &
              (df["delta"].abs() >= 0.10) & (df["delta"].abs() <= 0.40) &
              (df["strike"] < spot)].copy()
    if calls.empty or puts.empty:
        return _empty()

    m = calls.merge(puts, on=["expiration", "dte"], suffixes=("_c", "_p"))
    if m.empty:
        return _empty()
    width = m["strike_c"] - m["strike_p"]
    m = m[width.between(min_width, max_width)]
    if m.empty:
        return _empty()

    rows = []
    for _, r in m.iterrows():
        K_c = float(r["strike_c"])   # long call
        K_p = float(r["strike_p"])   # short put
        net_credit = float(r["mid_p"]) - float(r["mid_c"])
        # max_loss: put assignment risk (capital-at-risk = K_p − net_credit)
        max_loss = max(0.01, K_p - net_credit)
        # Cap max_profit at 3× max_loss for ranking (upside is unbounded)
        max_profit = 3 * max_loss
        # Upside breakeven: where the long call covers the net debit (if any)
        if net_credit >= 0:
            be_upper = K_c - net_credit
        else:
            be_upper = K_c + abs(net_credit)
        dte = int(r["dte"])
        T = _T(dte)
        iv_c = float(r["iv_c"])
        iv_p = float(r["iv_p"])
        pop = prob_above(spot, be_upper, T, iv_c)
        pop = min(max(pop, 0.0), 1.0)

        legs = [
            {"type": "call", "strike": K_c, "qty": +1,
             "entry_mid": float(r["mid_c"]), "iv": iv_c},
            {"type": "put",  "strike": K_p, "qty": -1,
             "entry_mid": float(r["mid_p"]), "iv": iv_p},
        ]
        g = _spread_greeks(legs, spot, T)
        rows.append({
            "strategy": "Risk Reversal", "expiration": r["expiration"],
            "dte": dte, "spot": spot,
            "short_strike": K_p, "long_strike": K_c,
            "short_type": "put", "long_type": "call",
            "short_mid": float(r["mid_p"]), "long_mid": float(r["mid_c"]),
            "short_iv": iv_p, "long_iv": iv_c,
            "net_credit": net_credit,
            "max_profit": max_profit, "max_loss": max_loss,
            "risk_reward": max_profit / max_loss,
            "pop": pop,
            "ann_yield_pct": max_profit / max_loss * (365 / dte) * 100,
            "breakeven1": be_upper, "breakeven2": float("nan"),
            "short_iv_excess": float(r.get("iv_excess_p", 0.0)),
            "short_oi": int(r["open_interest_p"]),
            "long_oi": int(r["open_interest_c"]),
            **g,
        })
    return _finalise(rows, spot, earnings_dates or [])


# ── Master scan ───────────────────────────────────────────────────────────────

_BUILDERS = {
    "Bull Put Spread":        build_bull_put_spreads,
    "Bear Call Spread":       build_bear_call_spreads,
    "Bull Call Spread":       build_bull_call_spreads,
    "Bear Put Spread":        build_bear_put_spreads,
    "Jade Lizard":            build_jade_lizards,
    "Risk Reversal":          build_risk_reversals,
    "Iron Condor":            build_iron_condors,
    "Iron Butterfly":         build_iron_butterflies,
    "Broken-Wing Butterfly":  build_iron_butterflies,   # same builder, different label per row
    "Calendar / Diagonal":    build_calendar_spreads,
    "Ratio Spread (1×2)":     build_ratio_spreads,
    "Long Straddle":          build_long_straddles,
    "Long Strangle":          build_long_strangles,
}


def scan_spreads(
    df: pd.DataFrame,
    strategies: list[str],
    min_dte: int,
    max_dte: int,
    min_width: float,
    max_width: float,
    min_oi: int,
    min_pop: float,
    sort_by: str = "risk_reward",
    only_positive_theta: bool = False,
    only_positive_vega: bool = False,
    hide_earnings: bool = False,
    earnings_dates: list | None = None,
    *,
    max_abs_delta: float = 1.0,
    width_mode: str = "dollar",
) -> tuple[pd.DataFrame, list[str]]:
    """Run selected strategy builders. Returns (combined_df, errors)."""
    errors: list[str] = []

    # Width-mode conversion: percent → dollars, once
    if width_mode == "percent" and not df.empty:
        spot = float(df["spot"].iloc[0])
        min_width = min_width * spot / 100
        max_width = max_width * spot / 100

    # De-duplicate builders so "Iron Butterfly" + "Broken-Wing Butterfly"
    # don't both invoke build_iron_butterflies. We track which builder
    # callables have already run and filter rows by the selected labels
    # at the end.
    seen_builders: set = set()
    parts = []
    for name in strategies:
        fn = _BUILDERS.get(name)
        if fn is None:
            continue
        if fn in seen_builders:
            continue
        seen_builders.add(fn)
        try:
            result = fn(df, min_dte, max_dte, min_width, max_width,
                        min_oi, earnings_dates)
            if not result.empty:
                parts.append(result)
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")

    if not parts:
        return pd.DataFrame(columns=SPREAD_COLS), errors

    combined = pd.concat(parts, ignore_index=True)

    # Keep only rows whose label was actually selected (handles the
    # Iron Butterfly / Broken-Wing case where one builder emits both)
    combined = combined[combined["strategy"].isin(strategies)]

    combined = combined[combined["pop"] >= min_pop]
    combined = combined[combined["net_delta"].abs() <= max_abs_delta]

    if only_positive_theta:
        combined = combined[combined["positive_theta"]]
    if only_positive_vega:
        combined = combined[combined["positive_vega"]]
    if hide_earnings:
        combined = combined[~combined["earnings_in_window"]]

    sort_col = {
        "Risk/Reward": "risk_reward",
        "POP": "pop",
        "Expected Value": "expected_value",
        "Ann%": "ann_yield_pct",
    }.get(sort_by, "risk_reward")

    if sort_col in combined.columns:
        combined = combined.sort_values(sort_col, ascending=False)

    return combined.reset_index(drop=True), errors
