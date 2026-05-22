"""Fetch option chain from Schwab API and return normalized DataFrame.

Returns the same 17-column shape as chain.py:fetch_chain() so all
downstream code (iv_surface, earnings, display, report) is unchanged.
Uses Schwab's native Greeks instead of Black-Scholes estimates.
"""

import logging

import pandas as pd

from options_scanner.chain_common import build_option_row, safe_float, safe_int

log = logging.getLogger(__name__)


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
                    dte = safe_int(opt.get("daysToExpiration"))
                    # Schwab returns 0DTE rows even for past expirations
                    # and rows outside the requested DTE window; filter
                    # them here since the API doesn't honor the bounds
                    # strictly.
                    if dte <= 0 or dte < min_dte:
                        continue
                    if max_dte is not None and dte > max_dte:
                        continue
                    built = build_option_row(
                        side=side,
                        strike=safe_float(opt.get("strikePrice")),
                        expiration=exp_str,
                        dte=dte,
                        spot=spot,
                        bid=safe_float(opt.get("bid")),
                        ask=safe_float(opt.get("ask")),
                        mid=safe_float(opt.get("mark")),
                        last=safe_float(opt.get("last")),
                        # Schwab returns IV as a percentage (45.5 = 45.5%)
                        iv=safe_float(opt.get("volatility")) / 100.0,
                        delta=safe_float(opt.get("delta")),
                        gamma=safe_float(opt.get("gamma")),
                        open_interest=safe_int(opt.get("openInterest")),
                        volume=safe_int(opt.get("totalVolume")),
                    )
                    if built is not None:
                        rows.append(built)

    log.info(
        "  Schwab: %d options across %d unique expirations for %s",
        len(rows),
        len({r["expiration"] for r in rows}),
        ticker,
    )
    return pd.DataFrame(rows) if rows else pd.DataFrame()
