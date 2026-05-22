"""SPOT metric card helpers: day-change %, last-trade timestamp, and
the inline-colored value formatting that every tab uses for its
spot-price card.

Three coupled pieces:

- `fetch_spot_meta(ticker, data_source)` — cached fetcher (60s TTL).
  Returns pct_change, last_trade_ts, and provider identity. Reads
  Yahoo's fast_info or Schwab's get_quote depending on the data
  source the scan was captured against (not the live dropdown).
- `spot_value_html(spot, pct)` — HTML snippet with the price and a
  small green/red/grey % change pill beside it.
- `spot_help_text(meta)` — provider label + "trade {time}" (Schwab)
  or "fetched {time}" (Yahoo) for the card's help line.

Yahoo's fast_info does not reliably expose a last-trade timestamp;
Yahoo callers fall back to scan_ts (the fetch time) via session
state — handled by spot_help_text, not the fetcher.
"""

from __future__ import annotations

import streamlit as st

from options_scanner.display.scan_stamp import PROVIDER_LABELS, tz_abbr


@st.cache_data(ttl=60, show_spinner=False)
def fetch_spot_meta(ticker: str, data_source: str) -> dict:
    """Fetch day-change % and last-trade timestamp for the spot-price card.

    Returns a dict with keys:
        pct_change:    float % change or None
        last_trade_ts: timezone-aware datetime or None
        source_label: "Yahoo Finance" or "Schwab"
        source_key:   "yahoo" or "schwab"

    Yahoo's fast_info does not reliably expose a last-trade timestamp, so
    Yahoo callers fall back to scan_ts (the fetch time) — handled by the
    caller, not here.
    """
    result = {
        "pct_change":    None,
        "last_trade_ts": None,
        "source_label":  PROVIDER_LABELS.get(data_source, data_source),
        "source_key":    data_source,
    }
    try:
        if data_source == "yahoo":
            import yfinance as yf
            from stocks_shared.yahoo import normalize_ticker
            info = yf.Ticker(normalize_ticker(ticker)).fast_info
            last = info.get("lastPrice") or info.get("regularMarketPrice")
            prev = info.get("previousClose")
            if last and prev and float(prev) > 0:
                result["pct_change"] = (
                    (float(last) - float(prev)) / float(prev) * 100.0
                )
            return result
        cfg = st.session_state.get("schwab_config") or {}
        if not cfg.get("app_key"):
            return result
        from stocks_shared.schwab_live import (
            get_client, normalize_ticker_schwab,
        )
        client = get_client(
            cfg.get("app_key", ""),
            cfg.get("app_secret", ""),
            cfg.get("callback_url", "https://127.0.0.1:8182/"),
            cfg.get("token_file", "~/.config/schwab-token.json"),
        )
        sym = normalize_ticker_schwab(ticker)
        resp = client.get_quote(sym)
        resp.raise_for_status()
        quote = resp.json().get(sym, {}).get("quote", {})
        pct = quote.get("netPercentChange")
        if pct is not None:
            result["pct_change"] = float(pct)
        else:
            last = quote.get("mark") or quote.get("lastPrice")
            prev = quote.get("closePrice")
            if last and prev and float(prev) > 0:
                result["pct_change"] = (
                    (float(last) - float(prev)) / float(prev) * 100.0
                )
        # Schwab tradeTime is epoch milliseconds.
        trade_ms = quote.get("tradeTime")
        if trade_ms:
            from datetime import datetime as _dt
            try:
                result["last_trade_ts"] = (
                    _dt.fromtimestamp(int(trade_ms) / 1000).astimezone()
                )
            except (ValueError, OSError):
                pass
        return result
    except Exception:
        return result


def spot_value_html(spot: float, pct: float | None) -> str:
    """Return the spot price with an inline colored % change beside it."""
    if pct is None:
        return f"${spot:,.2f}"
    if pct > 0:
        color, arrow = "#16a34a", "▲"
    elif pct < 0:
        color, arrow = "#dc2626", "▼"
    else:
        color, arrow = "#64748b", "●"
    return (
        f"${spot:,.2f}"
        f"<span style='color:{color}; font-size:0.6em; "
        f"font-weight:500; margin-left:0.5em; vertical-align:middle;'>"
        f"{arrow} {abs(pct):.2f}%</span>"
    )


def spot_help_text(meta: dict) -> str:
    """Source label + last-trade time for the spot-price card's help line."""
    label = meta.get("source_label", "")
    ts = meta.get("last_trade_ts") or st.session_state.get("scan_ts")
    if not ts:
        return label
    today = ts.astimezone().date()
    now_date = st.session_state.get("scan_ts")
    now_date = now_date.astimezone().date() if now_date else today
    time_part = ts.strftime("%I:%M %p").lstrip("0")
    tz = tz_abbr(ts)
    if ts.date() == now_date:
        when = f"{time_part} {tz}".rstrip()
    else:
        when = f"{ts.strftime('%b')} {ts.day}, {time_part} {tz}".rstrip()
    prefix = "trade" if meta.get("source_key") == "schwab" else "fetched"
    return f"{label} · {prefix} {when}"
