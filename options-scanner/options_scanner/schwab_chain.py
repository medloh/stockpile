"""Fetch option chain from Schwab API and return normalized DataFrame.

Returns the same 17-column shape as chain.py:fetch_chain() so all
downstream code (iv_surface, earnings, display, report) is unchanged.
Uses Schwab's native Greeks instead of Black-Scholes estimates.
"""

import logging
import math

import pandas as pd

log = logging.getLogger(__name__)


def _safe_float(val, default: float = 0.0) -> float:
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _safe_int(val, default: int = 0) -> int:
    try:
        f = float(val)
        return int(f) if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def fetch_chain_schwab(ticker: str, opt_type: str = "both",
                       min_dte: int = 30, max_dte: int | None = 90,
                       schwab_config: dict | None = None) -> pd.DataFrame:
    """Fetch Schwab option chain and return normalized DataFrame."""
    from stocks_shared.schwab_live import (
        get_client, fetch_live_price_schwab, fetch_option_chain_raw
    )

    cfg = schwab_config or {}
    try:
        client = get_client(
            cfg.get("app_key", ""),
            cfg.get("app_secret", ""),
            cfg.get("callback_url", "https://127.0.0.1:8182/"),
            cfg.get("token_file", "~/.config/schwab-token.json"),
        )
    except ValueError:
        raise

    spot = fetch_live_price_schwab(client, ticker)
    if not spot:
        raise ValueError(
            f"Could not fetch live price for {ticker} from Schwab"
        )

    data = fetch_option_chain_raw(client, ticker, min_dte, max_dte)
    if data is None:
        raise ValueError(
            f"Could not fetch option chain for {ticker} from Schwab"
        )
    if data.get("status") != "SUCCESS":
        raise ValueError(
            f"Schwab chain request failed for {ticker}: "
            f"{data.get('status', 'unknown error')}"
        )

    sides_to_fetch = []
    if opt_type in ("both", "calls"):
        sides_to_fetch.append(("call", "callExpDateMap"))
    if opt_type in ("both", "puts"):
        sides_to_fetch.append(("put", "putExpDateMap"))

    rows = []
    for side, map_key in sides_to_fetch:
        for exp_key, strikes in data.get(map_key, {}).items():
            # exp_key format: "YYYY-MM-DD:DTE"
            exp_str = exp_key.split(":")[0]

            for opts in strikes.values():
                for opt in opts:
                    K = _safe_float(opt.get("strikePrice"))
                    bid = _safe_float(opt.get("bid"))
                    ask = _safe_float(opt.get("ask"))
                    mid = _safe_float(opt.get("mark"))
                    last = _safe_float(opt.get("last"))
                    # Schwab returns IV as a percentage (e.g., 45.5 = 45.5%)
                    iv = _safe_float(opt.get("volatility")) / 100.0
                    delta = _safe_float(opt.get("delta"))
                    gamma = _safe_float(opt.get("gamma"))
                    oi = _safe_int(opt.get("openInterest"))
                    volume = _safe_int(opt.get("totalVolume"))
                    dte = _safe_int(opt.get("daysToExpiration"))

                    if bid <= 0 and ask <= 0:
                        continue
                    if mid <= 0:
                        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else last
                    if mid <= 0 or iv < 0.01 or K <= 0 or dte <= 0:
                        continue
                    if dte < min_dte:
                        continue
                    if max_dte is not None and dte > max_dte:
                        continue

                    log_m = math.log(K / spot)
                    capital = spot if side == "call" else K
                    # 0DTE rows (same-day expiry) get clamped to 1 day
                    # for the annualization — yield is meaningless at
                    # this scale, but the row's gamma/OI are still
                    # useful (GEX).
                    ann_yield = (mid / capital) * (365.0 / max(dte, 1)) * 100.0

                    rows.append({
                        "type":          side,
                        "strike":        K,
                        "expiration":    exp_str,
                        "dte":           dte,
                        "spot":          spot,
                        "log_moneyness": log_m,
                        "bid":           bid,
                        "ask":           ask,
                        "mid":           mid,
                        "iv":            iv,
                        "iv_fitted":     iv,
                        "iv_excess":     0.0,
                        "delta":         delta,
                        "gamma":         gamma,
                        "ann_yield_pct": ann_yield,
                        "open_interest": oi,
                        "volume":        volume,
                        "earnings_count": 0,
                    })

    log.info(
        "  Schwab: %d options across %d unique expirations for %s",
        len(rows),
        len({r["expiration"] for r in rows}),
        ticker,
    )
    return pd.DataFrame(rows) if rows else pd.DataFrame()
