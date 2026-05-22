"""Tests for `fetch_chain_schwab` in schwab_chain.py.

Mirrors `test_chain.py`'s coverage on the Schwab data path, mocking
`get_client`, `fetch_live_price_schwab`, and `fetch_option_chain_raw`
so tests run without network or auth.

Coverage focus:
  - Annualization math is correct for normal DTEs (call vs spot,
    put vs strike)
  - Quote-quality filters (zero bid+ask, sub-threshold IV) drop rows
  - DTE windowing filters work on the application side
  - 0DTE rows are explicitly dropped (note: differs from the Yahoo
    path which keeps them — Schwab's `daysToExpiration` filter
    rejects same-day expirations before annualization)
  - Failure cases (empty chain, missing spot, FAILED status) raise
    or return empty
"""

import pandas as pd
import pytest
import stocks_shared.schwab_live as schwab_live

import options_scanner.schwab_chain as schwab_chain


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_option(strike: float, dte: int, **overrides) -> dict:
    """Build a single option dict in Schwab's raw-chain shape.

    Defaults are a generic valid quote so each test only specifies what
    it cares about. Note Schwab returns IV as a percentage (e.g. 25.0
    means 25%), which `_safe_float(...) / 100.0` normalizes downstream.
    """
    base = {
        "strikePrice": strike,
        "bid": 1.00,
        "ask": 1.10,
        "mark": 1.05,
        "last": 1.05,
        "volatility": 25.0,     # 25% IV
        "delta": 0.50,
        "gamma": 0.02,
        "openInterest": 500,
        "totalVolume": 100,
        "daysToExpiration": dte,
    }
    base.update(overrides)
    return base


def _make_raw_chain(calls: dict | None = None,
                    puts: dict | None = None,
                    status: str = "SUCCESS") -> dict:
    """Build the dict returned by `fetch_option_chain_raw`.

    `calls` / `puts` map "YYYY-MM-DD:DTE" → {strike_str: [option, ...]}.
    """
    return {
        "status": status,
        "callExpDateMap": calls or {},
        "putExpDateMap":  puts or {},
    }


@pytest.fixture
def patch_schwab(monkeypatch):
    """Install all the Schwab patches needed for `fetch_chain_schwab`.

    Returns a function the test calls with the raw chain dict and an
    optional spot override.
    """
    def _setup(raw_chain: dict, spot: float = 100.0):
        monkeypatch.setattr(schwab_live, "get_client",
                            lambda *a, **kw: object())
        monkeypatch.setattr(schwab_live, "fetch_live_price_schwab",
                            lambda client, ticker: spot)
        monkeypatch.setattr(schwab_live, "fetch_option_chain_raw",
                            lambda client, ticker, min_dte, max_dte: raw_chain)

    return _setup


# ── Normal DTE math ──────────────────────────────────────────────────────────

def test_normal_dte_ann_yield_math(patch_schwab):
    """For a 30-DTE $100 call quoted at mark $1.05 with spot $100,
    annualized yield is (1.05 / 100) * (365 / 30) * 100 ≈ 12.775%."""
    raw = _make_raw_chain(calls={
        "2026-06-18:30": {
            "100.0": [_make_option(strike=100.0, dte=30, mark=1.05)],
        },
    })
    patch_schwab(raw)

    out = schwab_chain.fetch_chain_schwab(
        "AAPL", opt_type="calls", min_dte=0, max_dte=60,
        schwab_config={},
    )

    assert len(out) == 1
    expected = (1.05 / 100.0) * (365.0 / 30.0) * 100.0
    assert abs(out.iloc[0]["ann_yield_pct"] - expected) < 1e-9


def test_put_ann_yield_uses_strike_as_capital(patch_schwab):
    """Put yield is annualized against strike (capital at risk for a
    cash-secured put), not spot."""
    raw = _make_raw_chain(puts={
        "2026-06-18:30": {
            "90.0": [_make_option(strike=90.0, dte=30, mark=1.05)],
        },
    })
    patch_schwab(raw)

    out = schwab_chain.fetch_chain_schwab(
        "AAPL", opt_type="puts", min_dte=0, max_dte=60,
        schwab_config={},
    )

    assert len(out) == 1
    expected = (1.05 / 90.0) * (365.0 / 30.0) * 100.0
    assert abs(out.iloc[0]["ann_yield_pct"] - expected) < 1e-9


def test_iv_is_normalized_from_percent(patch_schwab):
    """Schwab returns volatility as a percentage (45.5 means 45.5%);
    `_fetch_chain_schwab` must divide by 100 to match downstream
    expectations (IV in [0, ~3])."""
    raw = _make_raw_chain(calls={
        "2026-06-18:30": {
            "100.0": [_make_option(strike=100.0, dte=30, volatility=45.5)],
        },
    })
    patch_schwab(raw)

    out = schwab_chain.fetch_chain_schwab(
        "AAPL", opt_type="calls", min_dte=0, max_dte=60,
        schwab_config={},
    )

    assert len(out) == 1
    assert abs(out.iloc[0]["iv"] - 0.455) < 1e-9


# ── Quote-quality filters ────────────────────────────────────────────────────

def test_zero_bid_zero_ask_rows_are_filtered(patch_schwab):
    """Rows with bid=0 AND ask=0 carry no usable price and must be dropped."""
    raw = _make_raw_chain(calls={
        "2026-06-18:30": {
            "95.0":  [_make_option(strike=95.0,  dte=30, bid=0.0, ask=0.0)],
            "100.0": [_make_option(strike=100.0, dte=30)],
        },
    })
    patch_schwab(raw)

    out = schwab_chain.fetch_chain_schwab(
        "AAPL", opt_type="calls", min_dte=0, max_dte=60,
        schwab_config={},
    )

    assert len(out) == 1
    assert out.iloc[0]["strike"] == 100.0


def test_sub_threshold_iv_rows_are_filtered(patch_schwab):
    """IV below 0.01 (1%) after percent-normalization is treated as bad
    data and the row dropped."""
    raw = _make_raw_chain(calls={
        "2026-06-18:30": {
            "95.0":  [_make_option(strike=95.0,  dte=30, volatility=0.5)],  # 0.005 → drop
            "100.0": [_make_option(strike=100.0, dte=30, volatility=25.0)], # 0.25 → keep
        },
    })
    patch_schwab(raw)

    out = schwab_chain.fetch_chain_schwab(
        "AAPL", opt_type="calls", min_dte=0, max_dte=60,
        schwab_config={},
    )

    assert len(out) == 1
    assert out.iloc[0]["strike"] == 100.0


# ── DTE windowing ────────────────────────────────────────────────────────────

def test_0dte_rows_are_dropped(patch_schwab):
    """Schwab path filters out `daysToExpiration <= 0` before the
    annualization step (line `if mid <= 0 or iv < 0.01 or K <= 0 or
    dte <= 0`). The `max(dte, 1)` guard at the annualization is purely
    defensive; this test documents the actual drop behavior.

    Note: this differs from the Yahoo path, which keeps 0DTE rows. If
    that inconsistency becomes a GEX-coverage problem, it's a real
    behavioral change — flag it before flipping the filter.
    """
    raw = _make_raw_chain(calls={
        "2026-05-19:0":  {
            "100.0": [_make_option(strike=100.0, dte=0)],
        },
        "2026-06-18:30": {
            "100.0": [_make_option(strike=100.0, dte=30)],
        },
    })
    patch_schwab(raw)

    out = schwab_chain.fetch_chain_schwab(
        "AAPL", opt_type="calls", min_dte=0, max_dte=60,
        schwab_config={},
    )

    assert len(out) == 1
    assert out.iloc[0]["dte"] == 30


def test_min_dte_filter_excludes_short_expirations(patch_schwab):
    """Expirations with DTE < min_dte must be dropped application-side
    even when the raw chain includes them."""
    raw = _make_raw_chain(calls={
        "2026-05-24:5":  {
            "100.0": [_make_option(strike=100.0, dte=5)],
        },
        "2026-07-03:45": {
            "100.0": [_make_option(strike=100.0, dte=45)],
        },
    })
    patch_schwab(raw)

    out = schwab_chain.fetch_chain_schwab(
        "AAPL", opt_type="calls", min_dte=30, max_dte=60,
        schwab_config={},
    )

    assert len(out) == 1
    assert out.iloc[0]["dte"] == 45


def test_max_dte_filter_excludes_far_expirations(patch_schwab):
    """Expirations with DTE > max_dte must be dropped application-side."""
    raw = _make_raw_chain(calls={
        "2026-06-18:30":  {
            "100.0": [_make_option(strike=100.0, dte=30)],
        },
        "2027-06-24:400": {
            "100.0": [_make_option(strike=100.0, dte=400)],
        },
    })
    patch_schwab(raw)

    out = schwab_chain.fetch_chain_schwab(
        "AAPL", opt_type="calls", min_dte=0, max_dte=60,
        schwab_config={},
    )

    assert len(out) == 1
    assert out.iloc[0]["dte"] == 30


# ── Empty / degenerate inputs ────────────────────────────────────────────────

def test_empty_chain_returns_empty_dataframe(patch_schwab):
    """A chain with no expirations yields an empty result."""
    patch_schwab(_make_raw_chain())
    out = schwab_chain.fetch_chain_schwab(
        "AAPL", min_dte=0, max_dte=60, schwab_config={},
    )
    assert out.empty


def test_no_valid_rows_returns_empty_dataframe(patch_schwab):
    """If every row is filtered out, the result is an empty DataFrame."""
    raw = _make_raw_chain(calls={
        "2026-06-18:30": {
            "100.0": [_make_option(strike=100.0, dte=30, bid=0.0, ask=0.0)],
        },
    })
    patch_schwab(raw)

    out = schwab_chain.fetch_chain_schwab(
        "AAPL", min_dte=0, max_dte=60, schwab_config={},
    )
    assert out.empty


def test_missing_spot_raises(monkeypatch):
    """If `fetch_live_price_schwab` returns falsy, we raise rather than
    silently producing nonsense rows."""
    monkeypatch.setattr(schwab_live, "get_client",
                        lambda *a, **kw: object())
    monkeypatch.setattr(schwab_live, "fetch_live_price_schwab",
                        lambda client, ticker: None)
    monkeypatch.setattr(schwab_live, "fetch_option_chain_raw",
                        lambda client, ticker, min_dte, max_dte: _make_raw_chain())

    with pytest.raises(ValueError, match="live price"):
        schwab_chain.fetch_chain_schwab(
            "AAPL", min_dte=0, max_dte=60, schwab_config={},
        )


def test_failed_status_raises(patch_schwab):
    """Schwab returning status != SUCCESS must surface as a ValueError
    rather than silently producing an empty DataFrame."""
    patch_schwab(_make_raw_chain(status="FAILED"))
    with pytest.raises(ValueError, match="Schwab chain request failed"):
        schwab_chain.fetch_chain_schwab(
            "AAPL", min_dte=0, max_dte=60, schwab_config={},
        )
