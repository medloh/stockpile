"""Tests for `_fetch_chain_yahoo` in chain.py.

The function builds an option-chain DataFrame from yfinance's per-side
DataFrames, computing per-row deltas, gammas, and annualized yields.
Tests mock both `yfinance.Ticker` and `fetch_live_price` so they run
without network access.

Coverage focus (room to grow over time):
  - 0DTE rows survive the annualization without ZeroDivisionError
    (regression — the bug that triggered building these tests)
  - Annualization math is correct for normal DTEs
  - Quote-quality filters (zero bid+ask, sub-threshold IV) drop rows
  - DTE windowing filters work
  - Empty input is handled cleanly
"""

from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

import options_scanner.chain as chain


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_side_df(rows: list[dict]) -> pd.DataFrame:
    """Build a DataFrame matching yfinance's option_chain().calls/.puts shape.

    Each row dict can omit fields; missing values default to a generic
    valid quote so tests only have to set the field they care about.
    """
    defaults = {
        "strike": 100.0,
        "bid": 1.00,
        "ask": 1.10,
        "lastPrice": 1.05,
        "impliedVolatility": 0.25,
        "openInterest": 500,
        "volume": 100,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def _mock_yf_ticker(exp_to_chain: dict):
    """Return a stand-in for yfinance.Ticker with the given expirations.

    `exp_to_chain` maps "YYYY-MM-DD" → SimpleNamespace(calls=df, puts=df).
    """
    class _MockTicker:
        options = list(exp_to_chain.keys())

        def option_chain(self, exp):
            return exp_to_chain[exp]

    return lambda _ticker_arg: _MockTicker()


@pytest.fixture
def patch_yahoo(monkeypatch):
    """Install all the yfinance/spot patches needed for `_fetch_chain_yahoo`.

    Returns a function the test can call with a dict of
    {expiration_str: SimpleNamespace(calls, puts)} to wire up the mock
    chain. Spot is fixed at $100 unless overridden.
    """
    def _setup(exp_to_chain: dict, spot: float = 100.0):
        import yfinance as yf
        monkeypatch.setattr(chain, "fetch_live_price", lambda t: spot)
        monkeypatch.setattr(chain, "normalize_ticker", lambda t: t)
        monkeypatch.setattr(yf, "Ticker", _mock_yf_ticker(exp_to_chain))

    return _setup


# ── 0DTE regression ──────────────────────────────────────────────────────────

def test_0dte_chain_does_not_crash(patch_yahoo):
    """Same-day expiration must not raise ZeroDivisionError on (365/dte).

    Regression for the crash hit when scanning the 0–60 DTE window on
    tickers Yahoo lists with same-day expirations (e.g. SPY weeklies).
    """
    today = date.today()
    exp_str = today.strftime("%Y-%m-%d")  # 0 DTE
    patch_yahoo({
        exp_str: SimpleNamespace(
            calls=_make_side_df([{"strike": 100.0}]),
            puts=_make_side_df([]),
        ),
    })

    out = chain._fetch_chain_yahoo("AAPL", min_dte=0, max_dte=0)

    assert not out.empty
    assert (out["dte"] == 0).all()
    # ann_yield must be a finite number, not inf/NaN
    assert out["ann_yield_pct"].apply(lambda v: pd.notna(v) and v != float("inf")).all()


# ── Normal DTE math ──────────────────────────────────────────────────────────

def test_normal_dte_ann_yield_math(patch_yahoo):
    """For a 30-DTE $100 call quoted at mid $1.05 with spot $100, the
    annualized yield is (1.05 / 100) * (365 / 30) * 100 ≈ 12.775%."""
    today = date.today()
    exp_str = (today + timedelta(days=30)).strftime("%Y-%m-%d")
    patch_yahoo({
        exp_str: SimpleNamespace(
            calls=_make_side_df([
                {"strike": 100.0, "bid": 1.00, "ask": 1.10,
                 "lastPrice": 1.05},
            ]),
            puts=_make_side_df([]),
        ),
    })

    out = chain._fetch_chain_yahoo("AAPL", min_dte=0, max_dte=60)

    assert len(out) == 1
    expected = (1.05 / 100.0) * (365.0 / 30.0) * 100.0
    assert abs(out.iloc[0]["ann_yield_pct"] - expected) < 1e-9


def test_put_ann_yield_uses_strike_as_capital(patch_yahoo):
    """Put yield is annualized against strike (capital at risk for a
    cash-secured put), not spot like calls."""
    today = date.today()
    exp_str = (today + timedelta(days=30)).strftime("%Y-%m-%d")
    patch_yahoo({
        exp_str: SimpleNamespace(
            calls=_make_side_df([]),
            puts=_make_side_df([
                {"strike": 90.0, "bid": 1.00, "ask": 1.10,
                 "lastPrice": 1.05},
            ]),
        ),
    })

    out = chain._fetch_chain_yahoo("AAPL", min_dte=0, max_dte=60)

    assert len(out) == 1
    expected = (1.05 / 90.0) * (365.0 / 30.0) * 100.0  # K=90, not spot=100
    assert abs(out.iloc[0]["ann_yield_pct"] - expected) < 1e-9


# ── Quote-quality filters ────────────────────────────────────────────────────

def test_zero_bid_zero_ask_rows_are_filtered(patch_yahoo):
    """Rows with bid=0 AND ask=0 carry no usable price and must be dropped."""
    today = date.today()
    exp_str = (today + timedelta(days=30)).strftime("%Y-%m-%d")
    patch_yahoo({
        exp_str: SimpleNamespace(
            calls=_make_side_df([
                {"strike":  95.0, "bid": 0.0, "ask": 0.0},  # dropped
                {"strike": 100.0, "bid": 1.0, "ask": 1.1},  # kept
            ]),
            puts=_make_side_df([]),
        ),
    })

    out = chain._fetch_chain_yahoo("AAPL", min_dte=0, max_dte=60)

    assert len(out) == 1
    assert out.iloc[0]["strike"] == 100.0


def test_sub_threshold_iv_rows_are_filtered(patch_yahoo):
    """IV below 0.01 (1%) is treated as bad data and the row dropped."""
    today = date.today()
    exp_str = (today + timedelta(days=30)).strftime("%Y-%m-%d")
    patch_yahoo({
        exp_str: SimpleNamespace(
            calls=_make_side_df([
                {"strike":  95.0, "impliedVolatility": 0.005},  # dropped
                {"strike": 100.0, "impliedVolatility": 0.25},   # kept
            ]),
            puts=_make_side_df([]),
        ),
    })

    out = chain._fetch_chain_yahoo("AAPL", min_dte=0, max_dte=60)

    assert len(out) == 1
    assert out.iloc[0]["strike"] == 100.0


# ── DTE windowing ────────────────────────────────────────────────────────────

def test_min_dte_filter_excludes_short_expirations(patch_yahoo):
    """Expirations with DTE < min_dte must be skipped entirely."""
    today = date.today()
    exp_short = (today + timedelta(days=5)).strftime("%Y-%m-%d")
    exp_long  = (today + timedelta(days=45)).strftime("%Y-%m-%d")
    patch_yahoo({
        exp_short: SimpleNamespace(
            calls=_make_side_df([{"strike": 100.0}]),
            puts=_make_side_df([]),
        ),
        exp_long: SimpleNamespace(
            calls=_make_side_df([{"strike": 100.0}]),
            puts=_make_side_df([]),
        ),
    })

    out = chain._fetch_chain_yahoo("AAPL", min_dte=30, max_dte=60)

    assert len(out) == 1
    assert out.iloc[0]["expiration"] == exp_long


def test_max_dte_filter_excludes_far_expirations(patch_yahoo):
    """Expirations with DTE > max_dte must be skipped."""
    today = date.today()
    exp_near = (today + timedelta(days=30)).strftime("%Y-%m-%d")
    exp_far  = (today + timedelta(days=400)).strftime("%Y-%m-%d")
    patch_yahoo({
        exp_near: SimpleNamespace(
            calls=_make_side_df([{"strike": 100.0}]),
            puts=_make_side_df([]),
        ),
        exp_far: SimpleNamespace(
            calls=_make_side_df([{"strike": 100.0}]),
            puts=_make_side_df([]),
        ),
    })

    out = chain._fetch_chain_yahoo("AAPL", min_dte=0, max_dte=60)

    assert len(out) == 1
    assert out.iloc[0]["expiration"] == exp_near


# ── Empty / degenerate inputs ────────────────────────────────────────────────

def test_no_expirations_returns_empty_dataframe(patch_yahoo):
    """A ticker with no listed expirations yields an empty result."""
    patch_yahoo({})
    out = chain._fetch_chain_yahoo("AAPL", min_dte=0, max_dte=60)
    assert out.empty


def test_no_valid_rows_returns_empty_dataframe(patch_yahoo):
    """If every row is filtered out, the result is an empty DataFrame."""
    today = date.today()
    exp_str = (today + timedelta(days=30)).strftime("%Y-%m-%d")
    patch_yahoo({
        exp_str: SimpleNamespace(
            calls=_make_side_df([
                {"strike": 100.0, "bid": 0.0, "ask": 0.0},  # bad quote
            ]),
            puts=_make_side_df([]),
        ),
    })
    out = chain._fetch_chain_yahoo("AAPL", min_dte=0, max_dte=60)
    assert out.empty


def test_missing_spot_raises(patch_yahoo, monkeypatch):
    """If `fetch_live_price` returns falsy, we raise rather than silently
    producing nonsense rows. (Patch override here — the fixture's spot
    default is fine for other tests.)"""
    monkeypatch.setattr(chain, "fetch_live_price", lambda t: None)
    monkeypatch.setattr(chain, "normalize_ticker", lambda t: t)
    with pytest.raises(ValueError):
        chain._fetch_chain_yahoo("AAPL", min_dte=0, max_dte=60)
