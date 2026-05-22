"""Fetch upcoming earnings dates and annotate the option chain."""

import logging
from datetime import date

import pandas as pd

from stocks_shared.yahoo import normalize_ticker

log = logging.getLogger(__name__)


def fetch_earnings_dates(ticker: str) -> list:
    """Return sorted list of upcoming earnings dates (up to 8 quarters)."""
    try:
        import yfinance as yf
        ticker = normalize_ticker(ticker)
        t = yf.Ticker(ticker)
        today = date.today()

        # Approach 1: get_earnings_dates() — most reliable in recent yfinance
        try:
            ed = t.get_earnings_dates(limit=8)
            if ed is not None and not ed.empty:
                future = []
                for idx in ed.index:
                    try:
                        d = idx.date() if hasattr(idx, "date") else idx
                        if d >= today:
                            future.append(d)
                    except Exception:
                        pass
                if future:
                    return sorted(future)
        except Exception:
            pass

        # Approach 2: calendar dict/DataFrame
        try:
            cal = t.calendar
            if cal is not None:
                # Newer yfinance: cal is a dict
                if isinstance(cal, dict):
                    raw = cal.get("Earnings Date")
                    if raw is not None:
                        dates = list(raw) if hasattr(raw, "__iter__") and not isinstance(raw, str) else [raw]
                        result = []
                        for d in dates:
                            try:
                                d2 = d.date() if hasattr(d, "date") else d
                                if d2 >= today:
                                    result.append(d2)
                            except Exception:
                                pass
                        if result:
                            return sorted(result)
                # Older yfinance: cal is a DataFrame
                elif hasattr(cal, "index") and "Earnings Date" in cal.index:
                    raw = cal.loc["Earnings Date"]
                    dates = list(raw) if hasattr(raw, "__iter__") and not isinstance(raw, str) else [raw]
                    result = []
                    for d in dates:
                        try:
                            d2 = d.date() if hasattr(d, "date") else d
                            if d2 >= today:
                                result.append(d2)
                        except Exception:
                            pass
                    if result:
                        return sorted(result)
        except Exception:
            pass

        # Approach 3: earnings_dates property fallback
        try:
            ed = t.earnings_dates
            if ed is not None and not ed.empty:
                future = []
                for idx in ed.index:
                    try:
                        d = idx.date() if hasattr(idx, "date") else idx
                        if d >= today:
                            future.append(d)
                    except Exception:
                        pass
                if future:
                    return sorted(future)[:8]
        except Exception:
            pass

    except Exception as exc:
        log.warning("Could not fetch earnings dates: %s", exc)

    return []


def annotate_earnings(df: pd.DataFrame, earnings_dates: list) -> pd.DataFrame:
    """Add earnings_count: number of earnings events before each expiration."""
    if df.empty:
        return df

    today = date.today()

    def _count(exp_str: str) -> int:
        exp = date.fromisoformat(exp_str)
        return sum(1 for d in earnings_dates if today < d <= exp)

    df = df.copy()
    df["earnings_count"] = df["expiration"].apply(_count)
    return df
