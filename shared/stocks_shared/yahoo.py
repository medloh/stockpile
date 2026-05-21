"""Yahoo Finance helpers: live prices, option chains, historical OHLC, and BS pricing."""

import re
from math import erfc, exp, isfinite, log, sqrt

_price_cache: dict[str, float | None] = {}
_chain_cache: dict[tuple[str, str], object] = {}

# Index tickers Yahoo Finance lists under ^NAME rather than bare NAME.
_INDEX_TICKERS = frozenset({
    "SPX", "SPXW", "GSPC",   # S&P 500
    "NDX", "NDXP", "IXIC",   # Nasdaq
    "RUT",                    # Russell 2000
    "DJI", "INDU",            # Dow Jones
    "VIX", "VXN", "RVX",     # Volatility
    "OEX", "XEO",             # S&P 100
    "TNX", "TYX",             # Rates
})


def normalize_ticker(ticker: str) -> str:
    """Prepend ^ for index tickers that Yahoo Finance lists under ^NAME.

    Trailing ! disables normalization — the bare symbol is used as-is.
    """
    t = ticker.strip().upper()
    if t.endswith("!"):
        return t[:-1]
    t = t.lstrip("^$")
    if t in _INDEX_TICKERS:
        return f"^{t}"
    return t


def fetch_live_price(ticker: str) -> float | None:
    """Return the last trade or regular market price for ticker."""
    ticker = normalize_ticker(ticker)
    if ticker in _price_cache:
        return _price_cache[ticker]
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).fast_info
        price = info.get("lastPrice") or info.get("regularMarketPrice")
    except Exception:
        price = None
    _price_cache[ticker] = price
    return price


def fetch_option_chain(ticker: str, exp_yf: str):
    """Return the option chain for ticker at expiration exp_yf (YYYY-MM-DD), cached."""
    ticker = normalize_ticker(ticker)
    key = (ticker, exp_yf)
    if key in _chain_cache:
        return _chain_cache[key]
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        chain = t.option_chain(exp_yf) if exp_yf in t.options else None
    except Exception:
        chain = None
    _chain_cache[key] = chain
    return chain


def fetch_option_market_value(ticker: str, opt_type: str, expiration_str: str,
                              strike, contracts: int) -> float | None:
    """Return total market value as a negative number (short position = liability)."""
    try:
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", expiration_str or "")
        if not m:
            return None
        exp_yf = f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
        chain = fetch_option_chain(ticker, exp_yf)
        if chain is None:
            return None
        df = chain.calls if opt_type == "Call" else chain.puts
        row = df[df["strike"] == float(strike)]
        if row.empty:
            return None
        bid, ask, last = row["bid"].iloc[0], row["ask"].iloc[0], row["lastPrice"].iloc[0]
        price = (bid + ask) / 2 if bid > 0 and ask > 0 else last
        return round(-price * contracts * 100, 2)
    except Exception:
        return None


def _norm_cdf(x: float) -> float:
    return 0.5 * erfc(-x / sqrt(2))


def bs_option_price(S: float, K: float, T: float, r: float, sigma: float, opt_type: str) -> float:
    """Black-Scholes price for a European call or put."""
    if T <= 0:
        return max(0.0, S - K) if opt_type == "Call" else max(0.0, K - S)
    d1 = (log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)
    if opt_type == "Call":
        return S * _norm_cdf(d1) - K * exp(-r * T) * _norm_cdf(d2)
    return K * exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def estimate_option_history(price_history, opt_type: str, strike, expiration_str: str,
                             open_date, contracts: int, r: float = 0.045):
    """Estimate daily option value (total dollars) using Black-Scholes + 30-day historical vol.

    Returns a pandas Series of total option market value indexed by date,
    from open_date to the last date in price_history.
    """
    import numpy as np
    import pandas as pd

    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", expiration_str or "")
    if not m:
        return None
    exp_ts = pd.Timestamp(f"{m.group(3)}-{m.group(1)}-{m.group(2)}")

    ph = price_history.copy()
    if ph.index.tz is not None:
        ph.index = ph.index.tz_convert(None)
    ph.index = ph.index.normalize()

    open_ts = pd.Timestamp(open_date)
    ph_from_open = ph[ph.index >= open_ts]
    if ph_from_open.empty:
        return None

    # 30-day rolling vol on full history for a better lookback, then align
    log_ret = np.log(ph["Close"] / ph["Close"].shift(1))
    vol = log_ret.rolling(30, min_periods=5).std() * sqrt(252)
    vol = vol.reindex(ph_from_open.index).ffill().bfill().fillna(0.3)

    K = float(strike)
    rows = []
    for date in ph_from_open.index:
        S         = float(ph_from_open.loc[date, "Close"])
        T         = max(0.0, (exp_ts - date).days / 365)
        sigma     = float(vol.loc[date])
        sigma     = sigma if isfinite(sigma) and sigma > 0.01 else 0.3
        bs_price  = bs_option_price(S, K, T, r, sigma, opt_type)
        intrinsic = max(0.0, S - K) if opt_type == "Call" else max(0.0, K - S)
        time_val  = max(0.0, bs_price - intrinsic)
        rows.append({
            "total_value":         bs_price * contracts * 100,
            "intrinsic_per_share": intrinsic,
            "time_value_per_share": time_val,
        })

    return pd.DataFrame(rows, index=ph_from_open.index)


def fetch_history(ticker: str, start: str | None = None, end: str | None = None):
    """Fetch historical daily close prices as a pandas DataFrame.

    Returns DataFrame with DatetimeIndex and 'Close' column.
    start/end: 'YYYY-MM-DD' strings (passed directly to yfinance).
    """
    import pandas as pd
    import yfinance as yf

    ticker = normalize_ticker(ticker)
    df = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
    if df.empty:
        return pd.DataFrame(columns=["Close"])
    return df[["Close"]].copy()
