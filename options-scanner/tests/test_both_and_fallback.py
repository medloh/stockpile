"""Tests for --both single-fetch output and the volume-zero fallback.

Both behaviors operate on an already-fetched/enriched chain (no network),
so they're exercised here against a synthetic dataframe.
"""
from types import SimpleNamespace

import pandas as pd
import pytest

from options_scanner.main import (
    _candidates_for,
    _candidates_with_fallback,
    _build_both_json_result,
)


def _row(opt_type, strike, iv, iv_excess, delta, oi, volume, dte=45):
    return {
        "type": opt_type,
        "strike": strike,
        "expiration": "2026-08-21",
        "dte": dte,
        "bid": 1.0,
        "ask": 1.2,
        "mid": 1.1,
        "iv": iv,
        "iv_excess": iv_excess,
        "delta": delta,
        "ann_yield_pct": 25.0,
        "open_interest": oi,
        "volume": volume,
        "earnings_count": 0,
        "spot": 150.0,
    }


def _chain(volume):
    """Two calls (one IV-rich, one IV-cheap) + a put, all at the given volume."""
    return pd.DataFrame([
        _row("call", 160, 0.62, 0.05, 0.45, oi=500, volume=volume),   # rich → sell
        _row("call", 145, 0.40, -0.04, 0.55, oi=400, volume=volume),  # cheap → buy
        _row("put", 140, 0.55, 0.02, -0.40, oi=300, volume=volume),
    ])


def _args(min_oi=25, min_vol=10, top=10, buy=False, min_ivpp=None):
    return SimpleNamespace(min_oi=min_oi, min_vol=min_vol, top=top,
                           buy=buy, min_ivpp=min_ivpp)


def test_sell_ranks_iv_rich_first():
    cands = _candidates_for(_chain(volume=100), "both", iv_asc=False,
                            min_oi=25, min_vol=10, top=10, roll_close_cost=None)
    # highest iv_excess first
    assert cands[0]["iv_pp"] == 5.0


def test_buy_ranks_iv_cheap_first():
    cands = _candidates_for(_chain(volume=100), "both", iv_asc=True,
                            min_oi=25, min_vol=10, top=10, roll_close_cost=None)
    assert cands[0]["iv_pp"] == -4.0


def test_volume_floor_wipes_chain_when_market_closed():
    # volume 0 everywhere + min_vol 10 → nothing passes (no fallback path)
    cands = _candidates_for(_chain(volume=0), "both", iv_asc=False,
                            min_oi=25, min_vol=10, top=10, roll_close_cost=None)
    assert cands == []


def test_fallback_recovers_candidates_and_flags_relaxed():
    cands, relaxed = _candidates_with_fallback(
        _chain(volume=0), "both", iv_asc=False,
        args=_args(min_vol=10), roll_close_cost=None)
    assert relaxed is True
    assert len(cands) > 0


def test_no_fallback_flag_when_volume_present():
    cands, relaxed = _candidates_with_fallback(
        _chain(volume=100), "both", iv_asc=False,
        args=_args(min_vol=10), roll_close_cost=None)
    assert relaxed is False
    assert len(cands) > 0


def test_fallback_keeps_open_interest_floor():
    # all OI below the floor → even relaxing volume to 0 finds nothing,
    # and relaxed must stay False (the retry produced nothing).
    chain = pd.DataFrame([_row("call", 160, 0.62, 0.05, 0.45, oi=5, volume=0)])
    cands, relaxed = _candidates_with_fallback(
        chain, "call", iv_asc=False, args=_args(min_oi=25, min_vol=10),
        roll_close_cost=None)
    assert cands == []
    assert relaxed is False


def test_both_result_has_sell_and_buy_from_one_chain():
    res = _build_both_json_result(
        "PLTR", 150.0, _chain(volume=100), "both", "schwab",
        _args(), roll_close_cost=None)
    assert res["mode"] == "both"
    assert "sell" in res and "buy" in res
    assert res["sell"]["candidates"][0]["iv_pp"] == 5.0    # rich first
    assert res["buy"]["candidates"][0]["iv_pp"] == -4.0    # cheap first
    assert res["sell"]["volume_filter_relaxed"] is False


def test_both_per_side_relaxed_flag_independent():
    res = _build_both_json_result(
        "PLTR", 150.0, _chain(volume=0), "both", "schwab",
        _args(min_vol=10), roll_close_cost=None)
    # market closed → both sides relaxed
    assert res["sell"]["volume_filter_relaxed"] is True
    assert res["buy"]["volume_filter_relaxed"] is True
    assert len(res["sell"]["candidates"]) > 0
    assert len(res["buy"]["candidates"]) > 0
