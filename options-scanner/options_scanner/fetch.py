"""Cached chain-fetch helpers used by the scanner tabs.

Wraps `chain.fetch_chain` with the IV-surface and earnings-annotation
post-processing every tab needs before display. Both helpers are
decorated with `@st.cache_data` so repeated reruns within a scan
session (sidebar tweaks, filter changes) don't refetch.

Two flavors:

- `fetch_and_enrich` — caller picks opt_type ("calls", "puts", or
  "both") and an optional max_dte. Used by the single-ticker, GEX,
  and spreads tabs.
- `fetch_position` — calls-only, no max_dte; the portfolio tab calls
  this once per open position so the signature stays narrow.

Both return `(df, earnings_dates, error_msg | None)`.

Imports of `chain`, `iv_surface`, and `earnings` are kept inline
inside the function bodies to preserve cold-start latency — the
established convention in this codebase.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st


@st.cache_data(ttl=300, show_spinner=False)
def fetch_and_enrich(ticker: str, opt_type: str, min_dte: int,
                     max_dte: int | None, provider: str = "yahoo",
                     schwab_config: dict | None = None):
    from options_scanner.chain import fetch_chain
    from options_scanner.iv_surface import compute_iv_excess
    from options_scanner.earnings import fetch_earnings_dates, annotate_earnings
    try:
        df = fetch_chain(ticker, opt_type=opt_type, min_dte=min_dte,
                         max_dte=max_dte, provider=provider,
                         schwab_config=schwab_config)
    except ValueError as exc:
        return pd.DataFrame(), [], str(exc)
    if df.empty:
        return df, [], None
    df = compute_iv_excess(df)
    earnings = fetch_earnings_dates(ticker)
    df = annotate_earnings(df, earnings)
    return df, earnings, None


@st.cache_data(ttl=300, show_spinner=False)
def fetch_position(ticker: str, min_dte: int, provider: str = "yahoo",
                   schwab_config: dict | None = None):
    """Cached per-ticker chain fetch for portfolio tab."""
    from options_scanner.chain import fetch_chain
    from options_scanner.iv_surface import compute_iv_excess
    from options_scanner.earnings import fetch_earnings_dates, annotate_earnings
    try:
        df = fetch_chain(ticker, opt_type="calls", min_dte=min_dte,
                         provider=provider, schwab_config=schwab_config)
    except ValueError as exc:
        return pd.DataFrame(), [], str(exc)
    if df.empty:
        return df, [], None
    df = compute_iv_excess(df)
    earnings = fetch_earnings_dates(ticker)
    df = annotate_earnings(df, earnings)
    return df, earnings, None
