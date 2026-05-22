"""Fetch and enrich option chain data."""

import logging
import math
from datetime import date, datetime

import pandas as pd

from options_scanner.chain_common import build_option_row, safe_float, safe_int
from stocks_shared.yahoo import fetch_live_price, normalize_ticker

log = logging.getLogger(__name__)

_RISK_FREE_RATE = 0.045


def _norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _bs_delta(S: float, K: float, T: float, r: float,
              sigma: float, opt_type: str) -> float:
    if T <= 0 or sigma < 0.001:
        if opt_type == "call":
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return _norm_cdf(d1) if opt_type == "call" else _norm_cdf(d1) - 1.0


def _bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma < 0.001 or S <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return _norm_pdf(d1) / (S * sigma * math.sqrt(T))


def _fetch_chain_yahoo(ticker: str, opt_type: str = "both",
                       min_dte: int = 30,
                       max_dte: int | None = 90) -> pd.DataFrame:
    import yfinance as yf

    ticker = normalize_ticker(ticker)
    spot = fetch_live_price(ticker)
    if not spot:
        raise ValueError(f"Could not fetch live price for {ticker}")

    t = yf.Ticker(ticker)
    today = date.today()

    expirations = []
    for e in t.options:
        dte = (datetime.strptime(e, "%Y-%m-%d").date() - today).days
        if dte >= min_dte and (max_dte is None or dte <= max_dte):
            expirations.append(e)
    log.info(
        "  %d expirations with DTE %s",
        len(expirations),
        f"{min_dte}–{max_dte}" if max_dte else f">= {min_dte}",
    )

    rows = []
    for exp_str in expirations:
        exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        dte = (exp_date - today).days
        T = dte / 365.0

        try:
            chain = t.option_chain(exp_str)
        except Exception as exc:
            log.warning("  Skipping %s: %s", exp_str, exc)
            continue

        sides = []
        if opt_type in ("both", "calls"):
            sides.append(("call", chain.calls))
        if opt_type in ("both", "puts"):
            sides.append(("put", chain.puts))

        for side, df in sides:
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                K = safe_float(row.get("strike"))
                if K <= 0:
                    continue  # math.log(spot / K) below blows up at K=0
                iv = safe_float(row.get("impliedVolatility"))
                delta = _bs_delta(spot, K, T, _RISK_FREE_RATE, iv, side)
                gamma = _bs_gamma(spot, K, T, _RISK_FREE_RATE, iv)
                built = build_option_row(
                    side=side, strike=K, expiration=exp_str,
                    dte=dte, spot=spot,
                    bid=safe_float(row.get("bid")),
                    ask=safe_float(row.get("ask")),
                    mid=0.0,
                    last=safe_float(row.get("lastPrice")),
                    iv=iv, delta=delta, gamma=gamma,
                    open_interest=safe_int(row.get("openInterest")),
                    volume=safe_int(row.get("volume")),
                )
                if built is not None:
                    rows.append(built)

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def fetch_chain(ticker: str, opt_type: str = "both",
                min_dte: int = 30, max_dte: int | None = 90,
                provider: str = "yahoo",
                schwab_config: dict | None = None) -> pd.DataFrame:
    """Return enriched DataFrame of options with min_dte <= DTE <= max_dte.

    opt_type: "both", "calls", or "puts"
    max_dte:  upper DTE limit; None = no limit
    provider: "yahoo" (default) or "schwab"
    schwab_config: dict with app_key, app_secret, callback_url, token_file
    """
    if provider == "schwab":
        from options_scanner.schwab_chain import fetch_chain_schwab
        return fetch_chain_schwab(ticker, opt_type, min_dte, max_dte,
                                  schwab_config)
    return _fetch_chain_yahoo(ticker, opt_type, min_dte, max_dte)
