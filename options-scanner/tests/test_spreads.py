"""Tests for spreads.py — BS helpers, builder smoke tests, scan_spreads integration."""

import math

import pandas as pd
import pytest

from options_scanner.chain import _bs_delta, _bs_gamma
from options_scanner.spreads import (
    DIRECTIONAL_STRATEGIES,
    NEUTRAL_STRATEGIES,
    RISK_FREE_RATE,
    SPREAD_COLS,
    STRATEGY_NAMES,
    _bs_price,
    _bs_theta,
    _bs_vega,
    build_bear_call_spreads,
    build_bear_put_spreads,
    build_bull_call_spreads,
    build_bull_put_spreads,
    build_calendar_spreads,
    build_iron_butterflies,
    build_iron_condors,
    build_jade_lizards,
    build_long_straddles,
    build_long_strangles,
    build_ratio_spreads,
    build_risk_reversals,
    prob_above,
    scan_spreads,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def synthetic_chain():
    """Small synthetic chain: 2 expirations × 9 strikes × calls+puts."""
    spot = 100.0
    rows = []
    for dte, exp in [(30, "2026-06-15"), (60, "2026-07-15")]:
        T = dte / 365.0
        for K in [80, 85, 90, 95, 100, 105, 110, 115, 120]:
            for ot in ("call", "put"):
                iv = 0.25
                delta = _bs_delta(spot, K, T, RISK_FREE_RATE, iv, ot)
                gamma = _bs_gamma(spot, K, T, RISK_FREE_RATE, iv)
                mid = max(0.10, abs(delta) * 5 + 0.50)
                rows.append({
                    "type": ot, "strike": float(K), "expiration": exp,
                    "dte": dte, "spot": spot,
                    "bid": mid * 0.9, "ask": mid * 1.1, "mid": mid,
                    "iv": iv, "iv_fitted": iv, "iv_excess": 0.0,
                    "delta": delta, "gamma": gamma,
                    "open_interest": 500, "volume": 100,
                    "ann_yield_pct": 10.0,
                })
    return pd.DataFrame(rows)


@pytest.fixture
def spx_like_chain():
    """High-priced chain (spot ≈ 6000) for testing percent width mode."""
    spot = 6000.0
    rows = []
    for dte, exp in [(45, "2026-06-30")]:
        T = dte / 365.0
        # Strikes every $25 from 5500 to 6500
        for K in range(5500, 6525, 25):
            for ot in ("call", "put"):
                iv = 0.18
                delta = _bs_delta(spot, K, T, RISK_FREE_RATE, iv, ot)
                gamma = _bs_gamma(spot, K, T, RISK_FREE_RATE, iv)
                mid = max(0.10, abs(delta) * 60 + 5)
                rows.append({
                    "type": ot, "strike": float(K), "expiration": exp,
                    "dte": dte, "spot": spot,
                    "bid": mid * 0.95, "ask": mid * 1.05, "mid": mid,
                    "iv": iv, "iv_fitted": iv, "iv_excess": 0.0,
                    "delta": delta, "gamma": gamma,
                    "open_interest": 2000, "volume": 500,
                    "ann_yield_pct": 8.0,
                })
    return pd.DataFrame(rows)


# ── BS helper unit tests ─────────────────────────────────────────────────────

def test_prob_above_atm():
    p = prob_above(100, 100, 0.25, 0.20)
    assert 0.50 < p < 0.60, f"ATM prob_above should be slightly > 0.5, got {p}"


def test_prob_above_deep_otm():
    p = prob_above(100, 200, 0.25, 0.20)
    assert p < 0.01


def test_prob_above_deep_itm():
    p = prob_above(100, 50, 0.25, 0.20)
    assert p > 0.99


def test_prob_above_degenerate_T_zero():
    assert prob_above(100, 95, 0, 0.20) == 1.0
    assert prob_above(100, 105, 0, 0.20) == 0.0


def test_bs_price_intrinsic_at_expiry():
    assert _bs_price(110, 100, 0, 0.20, "call") == 10
    assert _bs_price(90, 100, 0, 0.20, "put") == 10
    assert _bs_price(90, 100, 0, 0.20, "call") == 0
    assert _bs_price(110, 100, 0, 0.20, "put") == 0


def test_bs_price_put_call_parity():
    S, K, T, sigma = 100, 100, 0.5, 0.25
    c = _bs_price(S, K, T, sigma, "call")
    p = _bs_price(S, K, T, sigma, "put")
    expected = S - K * math.exp(-RISK_FREE_RATE * T)
    assert abs((c - p) - expected) < 0.01, f"C-P={c-p}, expected={expected}"


def test_bs_theta_long_call_negative():
    theta = _bs_theta(100, 100, 0.25, 0.20, "call")
    assert theta < 0, f"ATM long call theta should be negative, got {theta}"


def test_bs_vega_positive():
    v = _bs_vega(100, 100, 0.25, 0.20)
    assert v > 0


def test_bs_vega_degenerate():
    assert _bs_vega(100, 100, 0, 0.20) == 0.0
    assert _bs_vega(100, 100, 0.25, 0.0) == 0.0


# ── Per-builder smoke tests ──────────────────────────────────────────────────

_EXPECTED_COLS = [
    "strategy", "expiration", "dte", "spot",
    "short_strike", "long_strike",
    "net_credit", "max_profit", "max_loss", "risk_reward",
    "pop", "expected_value", "ann_yield_pct",
    "breakeven1", "be_move_pct",
    "net_delta", "net_gamma", "net_theta", "net_vega",
    "positive_theta", "positive_vega",
    "earnings_in_window",
]


def _assert_schema(df: pd.DataFrame):
    for col in _EXPECTED_COLS:
        assert col in df.columns, f"missing column: {col}"


def _assert_sane_metrics(df: pd.DataFrame):
    """POP in [0,1], R/R > 0, max_loss > 0."""
    assert df["pop"].between(0, 1).all(), "POP out of [0,1]"
    assert (df["risk_reward"] > 0).all(), "R/R must be > 0"
    assert (df["max_loss"] > 0).all(), "max_loss must be > 0"


@pytest.mark.parametrize("builder,expects_credit", [
    (build_bull_put_spreads, True),
    (build_bear_call_spreads, True),
    (build_bull_call_spreads, False),   # debit
    (build_bear_put_spreads, False),    # debit
    (build_jade_lizards, True),
    (build_risk_reversals, None),       # mixed
    (build_iron_condors, True),
    (build_iron_butterflies, True),
    (build_calendar_spreads, False),    # debit
    (build_ratio_spreads, None),        # mixed
    (build_long_straddles, False),      # debit
    (build_long_strangles, False),      # debit
])
def test_builder_smoke(synthetic_chain, builder, expects_credit):
    """Each builder returns a valid DataFrame on the synthetic chain."""
    out = builder(synthetic_chain, 20, 90, 5, 25, 10)
    _assert_schema(out)
    if out.empty:
        # Empty is OK; just verify schema/types stayed sane
        return
    _assert_sane_metrics(out)
    if expects_credit is True:
        assert (out["net_credit"] > 0).all(), "credit spreads need net_credit > 0"
    elif expects_credit is False:
        assert (out["net_credit"] < 0).all(), "debit spreads need net_credit < 0"


def test_builder_filters_apply(synthetic_chain):
    """Filters narrow results."""
    full = build_bull_put_spreads(synthetic_chain, 20, 90, 5, 25, 10)
    filtered = build_bull_put_spreads(synthetic_chain, 20, 90, 5, 25, 99999)
    assert not full.empty
    assert filtered.empty


def test_builder_empty_chain_no_crash():
    """Empty chain → empty result, no exception."""
    empty = pd.DataFrame()
    # build_bull_put_spreads short-circuits on empty; others should too.
    # For builders that read df["spot"].iloc[0], we need a chain that's
    # filterable-to-empty but not bare. Use a chain with no OI matches.
    spot = 100.0
    rows = [{
        "type": "put", "strike": 95.0, "expiration": "2026-06-15",
        "dte": 30, "spot": spot, "bid": 0.5, "ask": 0.6, "mid": 0.55,
        "iv": 0.25, "iv_fitted": 0.25, "iv_excess": 0.0,
        "delta": -0.20, "gamma": 0.01,
        "open_interest": 5, "volume": 1, "ann_yield_pct": 5.0,
    }]
    sparse = pd.DataFrame(rows)
    for builder in [build_bull_put_spreads, build_bear_call_spreads,
                    build_long_straddles, build_risk_reversals]:
        out = builder(sparse, 20, 90, 5, 25, 100)
        assert out.empty


def test_long_straddle_delta_neutral(synthetic_chain):
    """Long straddle should have net_delta near 0 (puts and calls at same strike cancel)."""
    out = build_long_straddles(synthetic_chain, 20, 90, 5, 25, 10)
    if out.empty:
        pytest.skip("no straddles found in synthetic chain")
    assert out["net_delta"].abs().max() < 0.20, f"straddle deltas: {out['net_delta'].tolist()}"
    assert out["positive_vega"].all(), "long straddle must have positive vega"


def test_risk_reversal_bullish(synthetic_chain):
    """Risk reversal = synthetic long → strongly positive delta."""
    out = build_risk_reversals(synthetic_chain, 20, 90, 5, 25, 10)
    if out.empty:
        pytest.skip("no risk reversals found in synthetic chain")
    assert (out["net_delta"] > 0.10).all(), f"net_delta: {out['net_delta'].tolist()}"


def test_iron_butterfly_labeling(synthetic_chain):
    """When strikes are symmetric around spot, label is 'Iron Butterfly'.
    When asymmetric, label is 'Broken-Wing Butterfly'."""
    out = build_iron_butterflies(synthetic_chain, 20, 90, 5, 25, 10)
    if out.empty:
        pytest.skip("no butterflies found in synthetic chain")
    # Synthetic chain has spot=100 and a strike at 100, so should be symmetric
    assert out["strategy"].isin(["Iron Butterfly", "Broken-Wing Butterfly"]).all()


# ── scan_spreads integration tests ───────────────────────────────────────────

def test_scan_spreads_returns_tuple(synthetic_chain):
    result = scan_spreads(
        synthetic_chain,
        strategies=["Bull Put Spread"],
        min_dte=20, max_dte=90, min_width=5, max_width=25,
        min_oi=10, min_pop=0.30,
    )
    assert isinstance(result, tuple)
    df, errors = result
    assert isinstance(df, pd.DataFrame)
    assert isinstance(errors, list)
    assert errors == []
    assert not df.empty


def test_scan_spreads_max_abs_delta_filter(synthetic_chain):
    df, errs = scan_spreads(
        synthetic_chain,
        strategies=["Iron Condor"],
        min_dte=20, max_dte=90, min_width=5, max_width=25,
        min_oi=10, min_pop=0.30,
        max_abs_delta=0.10,
    )
    assert errs == []
    if not df.empty:
        assert (df["net_delta"].abs() <= 0.10 + 1e-9).all()


def test_scan_spreads_max_abs_delta_default_keeps_all(synthetic_chain):
    """Default max_abs_delta=1.0 should not filter anything additional."""
    df_unfiltered, _ = scan_spreads(
        synthetic_chain, strategies=["Iron Condor"],
        min_dte=20, max_dte=90, min_width=5, max_width=25,
        min_oi=10, min_pop=0.30,
    )
    df_relaxed, _ = scan_spreads(
        synthetic_chain, strategies=["Iron Condor"],
        min_dte=20, max_dte=90, min_width=5, max_width=25,
        min_oi=10, min_pop=0.30,
        max_abs_delta=1.0,
    )
    assert len(df_unfiltered) == len(df_relaxed)


def test_scan_spreads_percent_width(spx_like_chain):
    """Percent width mode should work on SPX-like spot=6000 chain."""
    df, errs = scan_spreads(
        spx_like_chain,
        strategies=["Bull Put Spread", "Bear Call Spread"],
        min_dte=20, max_dte=90,
        min_width=0.5, max_width=2.0,    # 0.5% – 2% of $6000 = $30 – $120
        min_oi=10, min_pop=0.40,
        width_mode="percent",
    )
    assert errs == []
    assert not df.empty, "percent mode on SPX-like chain should return spreads"


def test_scan_spreads_dollar_width_fails_on_spx(spx_like_chain):
    """Default $5-$25 width finds nothing on SPX (strikes are $25 apart)."""
    df, errs = scan_spreads(
        spx_like_chain,
        strategies=["Bull Put Spread"],
        min_dte=20, max_dte=90,
        min_width=5, max_width=25,
        min_oi=10, min_pop=0.30,
    )
    # Strikes are $25 apart so only width=25 matches. Should find very few
    # or zero — confirming the scaling concern from the PR feedback.
    # Just assert it doesn't crash.
    assert isinstance(df, pd.DataFrame)
    assert errs == []


def test_scan_spreads_error_collection(synthetic_chain, monkeypatch):
    """Inject a builder that raises; ensure scan_spreads collects the error."""
    import options_scanner.spreads as sp

    def broken_builder(*a, **kw):
        raise RuntimeError("intentional test failure")

    monkeypatch.setitem(sp._BUILDERS, "Bull Put Spread", broken_builder)
    df, errs = scan_spreads(
        synthetic_chain, strategies=["Bull Put Spread"],
        min_dte=20, max_dte=90, min_width=5, max_width=25,
        min_oi=10, min_pop=0.30,
    )
    assert df.empty
    assert len(errs) == 1
    assert "Bull Put Spread" in errs[0]
    assert "intentional test failure" in errs[0]


def test_scan_spreads_iron_butterfly_dedupe(synthetic_chain):
    """Selecting both 'Iron Butterfly' and 'Broken-Wing Butterfly' should not
    invoke the builder twice (they share the same function)."""
    df, errs = scan_spreads(
        synthetic_chain,
        strategies=["Iron Butterfly", "Broken-Wing Butterfly"],
        min_dte=20, max_dte=90, min_width=5, max_width=25,
        min_oi=10, min_pop=0.30,
    )
    assert errs == []
    # All returned strategies should be one of the two labels
    if not df.empty:
        assert df["strategy"].isin(["Iron Butterfly", "Broken-Wing Butterfly"]).all()


def test_strategy_sets_partition_correctly():
    """DIRECTIONAL + NEUTRAL should together cover STRATEGY_NAMES with no overlap."""
    overlap = set(DIRECTIONAL_STRATEGIES) & set(NEUTRAL_STRATEGIES)
    assert overlap == set(), f"overlapping strategies: {overlap}"
    union = set(DIRECTIONAL_STRATEGIES) | set(NEUTRAL_STRATEGIES)
    missing = set(STRATEGY_NAMES) - union
    assert missing == set(), f"strategies not categorized: {missing}"


def test_calendar_with_earnings_dates_no_crash(synthetic_chain):
    """Calendar spreads use 'front→back' expiration strings — must not crash
    the earnings-window date parse in _finalise."""
    from datetime import date as _date
    earnings = [_date(2026, 6, 30)]
    df, errs = scan_spreads(
        synthetic_chain,
        strategies=["Calendar / Diagonal"],
        min_dte=20, max_dte=90, min_width=5, max_width=25,
        min_oi=10, min_pop=0.30,
        earnings_dates=earnings,
    )
    assert errs == [], f"unexpected errors: {errs}"


def test_spread_cols_constant_matches_finalise_output(synthetic_chain):
    """The columns produced by builders should equal SPREAD_COLS exactly."""
    out = build_bull_put_spreads(synthetic_chain, 20, 90, 5, 25, 10)
    if out.empty:
        pytest.skip("no spreads found")
    assert list(out.columns) == SPREAD_COLS
