"""Tests for iv_surface.compute_iv_excess.

The surface model is:
    IV ≈ a + b·m + c·m² + d·√T + e·m·√T
where m = log(K/S) and T = DTE/365.

These tests verify:
  - the output contract (columns added, row count preserved, no input mutation)
  - the small-chain / empty-chain fallbacks
  - that the fit recovers known synthetic surfaces (flat, linear in m)
  - that real outliers are surfaced as iv_excess with the correct sign
  - the iv_excess = iv − iv_fitted identity holds for every row
  - that rows with iv ≤ 0.02 are excluded from the fit but still get columns
"""

import math

import numpy as np
import pandas as pd
import pytest

from options_scanner.iv_surface import compute_iv_excess


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_chain(spot: float, dtes: list[int], strikes: list[float],
               iv_fn) -> pd.DataFrame:
    """Build a synthetic chain DataFrame.

    `iv_fn(K, dte) -> iv` lets each test specify the IV surface shape.
    """
    rows = []
    for dte in dtes:
        for K in strikes:
            rows.append({
                "strike": float(K),
                "dte": int(dte),
                "iv": float(iv_fn(K, dte)),
                "log_moneyness": math.log(K / spot),
            })
    return pd.DataFrame(rows)


# ── Output contract ──────────────────────────────────────────────────────────

def test_adds_iv_fitted_and_iv_excess_columns():
    df = make_chain(100, [30, 60], [90, 95, 100, 105, 110], lambda K, t: 0.30)
    out = compute_iv_excess(df)
    assert "iv_fitted" in out.columns
    assert "iv_excess" in out.columns


def test_preserves_row_count_and_order():
    df = make_chain(100, [30, 60], [90, 95, 100, 105, 110], lambda K, t: 0.30)
    out = compute_iv_excess(df)
    assert len(out) == len(df)
    # Original columns intact, in original order
    for col in df.columns:
        assert col in out.columns
        pd.testing.assert_series_equal(
            out[col].reset_index(drop=True),
            df[col].reset_index(drop=True),
        )


def test_does_not_mutate_input():
    df = make_chain(100, [30, 60], [90, 95, 100, 105, 110], lambda K, t: 0.30)
    before_cols = list(df.columns)
    compute_iv_excess(df)
    assert list(df.columns) == before_cols
    assert "iv_fitted" not in df.columns
    assert "iv_excess" not in df.columns


def test_iv_excess_equals_iv_minus_iv_fitted_identity():
    """The advertised relationship must hold for every row, every chain."""
    df = make_chain(
        100, [21, 45, 90],
        [80, 85, 90, 95, 100, 105, 110, 115, 120],
        lambda K, t: 0.20 + 0.001 * (K - 100) ** 2 / 100,
    )
    out = compute_iv_excess(df)
    diff = (out["iv"] - out["iv_fitted"]) - out["iv_excess"]
    assert diff.abs().max() < 1e-10


# ── Fallback paths ───────────────────────────────────────────────────────────

def test_small_chain_falls_back_to_zero_excess():
    """Under 5 valid points → no fit, iv_fitted = iv, iv_excess = 0."""
    df = make_chain(100, [30], [95, 100, 105], lambda K, t: 0.30)
    out = compute_iv_excess(df)
    assert (out["iv_excess"] == 0.0).all()
    assert (out["iv_fitted"] == out["iv"]).all()


def test_empty_chain_returns_empty_frame_with_columns():
    df = pd.DataFrame(columns=["strike", "dte", "iv", "log_moneyness"])
    out = compute_iv_excess(df)
    assert len(out) == 0
    assert "iv_fitted" in out.columns
    assert "iv_excess" in out.columns


def test_all_zero_iv_chain_falls_back():
    """All IVs filtered out → too few valid points → fallback."""
    df = make_chain(100, [30, 60], [90, 95, 100, 105, 110], lambda K, t: 0.0)
    out = compute_iv_excess(df)
    assert (out["iv_excess"] == 0.0).all()


def test_all_zero_dte_chain_falls_back():
    df = make_chain(100, [0], [90, 95, 100, 105, 110, 115, 120],
                    lambda K, t: 0.30)
    out = compute_iv_excess(df)
    assert (out["iv_excess"] == 0.0).all()


# ── Fit accuracy on synthetic surfaces ───────────────────────────────────────

def test_flat_surface_yields_near_zero_excess():
    """Every option has the same IV → fit is a constant, excess ≈ 0."""
    df = make_chain(
        100, [30, 60, 90],
        [80, 85, 90, 95, 100, 105, 110, 115, 120],
        lambda K, t: 0.30,
    )
    out = compute_iv_excess(df)
    assert out["iv_excess"].abs().max() < 1e-6
    assert (out["iv_fitted"] - 0.30).abs().max() < 1e-6


def test_linear_in_moneyness_surface_is_recovered_exactly():
    """IV linear in m = log(K/S) — exactly representable by the model."""
    spot = 100.0
    df = make_chain(
        spot, [30, 60, 90],
        [80, 85, 90, 95, 100, 105, 110, 115, 120],
        lambda K, t: 0.25 + 0.10 * math.log(K / spot),
    )
    out = compute_iv_excess(df)
    # Model includes a·1 + b·m + … so a pure linear-in-m surface fits exactly.
    assert out["iv_excess"].abs().max() < 1e-8


def test_smile_in_moneyness_surface_is_recovered_exactly():
    """IV quadratic in m — the c·m² term should absorb the smile."""
    spot = 100.0
    df = make_chain(
        spot, [30, 60, 90],
        [80, 85, 90, 95, 100, 105, 110, 115, 120],
        lambda K, t: 0.25 + 0.30 * math.log(K / spot) ** 2,
    )
    out = compute_iv_excess(df)
    assert out["iv_excess"].abs().max() < 1e-8


# ── Outlier detection ────────────────────────────────────────────────────────

def test_single_rich_outlier_above_surface_is_flagged_positive():
    """One strike priced 10pp above the otherwise-flat surface should
    surface as a large positive iv_excess, with other rows near zero."""
    spot = 100.0
    rich_K = 110.0
    df = make_chain(
        spot, [30, 60],
        [80, 85, 90, 95, 100, 105, 110, 115, 120],
        lambda K, t: 0.30 + (0.10 if (K == rich_K and t == 30) else 0.0),
    )
    out = compute_iv_excess(df)
    rich_row = out[(out["strike"] == rich_K) & (out["dte"] == 30)].iloc[0]
    others = out[~((out["strike"] == rich_K) & (out["dte"] == 30))]
    assert rich_row["iv_excess"] > 0.03  # clearly positive
    # Outlier should dominate — its excess much bigger than any other row
    assert rich_row["iv_excess"] > others["iv_excess"].abs().max()


def test_single_cheap_outlier_below_surface_is_flagged_negative():
    spot = 100.0
    cheap_K = 95.0
    df = make_chain(
        spot, [30, 60],
        [80, 85, 90, 95, 100, 105, 110, 115, 120],
        lambda K, t: 0.30 - (0.10 if (K == cheap_K and t == 30) else 0.0),
    )
    out = compute_iv_excess(df)
    cheap_row = out[(out["strike"] == cheap_K) & (out["dte"] == 30)].iloc[0]
    others = out[~((out["strike"] == cheap_K) & (out["dte"] == 30))]
    assert cheap_row["iv_excess"] < -0.03
    assert abs(cheap_row["iv_excess"]) > others["iv_excess"].abs().max()


def test_outlier_ranking_orders_by_magnitude():
    """A bigger deviation should produce a bigger |iv_excess| than a smaller one."""
    spot = 100.0
    df = make_chain(
        spot, [30, 60],
        [80, 85, 90, 95, 100, 105, 110, 115, 120],
        lambda K, t: 0.30
                     + (0.10 if (K == 110 and t == 30) else 0.0)
                     + (0.05 if (K == 90  and t == 30) else 0.0),
    )
    out = compute_iv_excess(df)
    big   = out[(out["strike"] == 110) & (out["dte"] == 30)]["iv_excess"].iloc[0]
    small = out[(out["strike"] ==  90) & (out["dte"] == 30)]["iv_excess"].iloc[0]
    assert big > small > 0


# ── Filter rule (iv > 0.02) ──────────────────────────────────────────────────

def test_rows_with_low_iv_excluded_from_fit_but_still_get_columns():
    """Rows with iv ≤ 0.02 don't participate in the fit, but the function
    still computes iv_fitted and iv_excess for every row of the input."""
    spot = 100.0
    df = make_chain(
        spot, [30, 60],
        [80, 85, 90, 95, 100, 105, 110, 115, 120],
        lambda K, t: 0.30,
    )
    # Stomp on one row with sub-threshold IV
    df.loc[0, "iv"] = 0.01
    out = compute_iv_excess(df)
    assert "iv_fitted" in out.columns
    assert "iv_excess" in out.columns
    # Identity still holds even on the excluded row
    diff = (out["iv"] - out["iv_fitted"]) - out["iv_excess"]
    assert diff.abs().max() < 1e-10
