"""Schwab developer API helpers — mirrors stocks_shared/yahoo.py."""

import logging
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

log = logging.getLogger(__name__)

# Schwab uses $NAME for cash-settled index options.
_SCHWAB_INDEX_TICKERS = frozenset({
    "SPX", "SPXW", "NDX", "NDXP", "RUT",
    "VIX", "DJI", "OEX", "XEO", "VXN", "RVX",
})


def normalize_ticker_schwab(ticker: str) -> str:
    """Prepend $ for index tickers that Schwab lists under $NAME.

    Trailing ! disables normalization — the bare symbol is used as-is.
    """
    t = ticker.strip().upper()
    if t.endswith("!"):
        return t[:-1]
    t = t.lstrip("^$")
    if t in _SCHWAB_INDEX_TICKERS:
        return f"${t}"
    return t

_client_cache: dict = {}


def get_client(app_key: str, app_secret: str, callback_url: str,
               token_file: str):
    """Return authenticated schwab-py client; cached per (app_key, token_file).

    First run (no token file): opens browser for OAuth login.
    Subsequent runs: silently refresh the access token via the stored
    refresh token. The refresh token itself has a fixed 7-day TTL from
    the initial OAuth — once expired, every quote/chain call returns
    None and the user must re-run schwab_auth.py.
    Raises ValueError with a user-friendly message on auth failure.
    """
    import schwab

    cache_key = (app_key, str(Path(token_file).expanduser()))
    if cache_key in _client_cache:
        return _client_cache[cache_key]

    token_path = Path(token_file).expanduser()
    try:
        client = schwab.auth.client_from_token_file(
            str(token_path), app_key, app_secret
        )
    except FileNotFoundError:
        try:
            log.info("No Schwab token found — starting OAuth login flow...")
            client = schwab.auth.client_from_login_flow(
                app_key, app_secret, callback_url, str(token_path)
            )
        except Exception as exc:
            raise ValueError(
                f"Schwab authentication failed: {exc}\n"
                "Run: uv run options-scanner/schwab_auth.py"
            ) from exc
    except Exception as exc:
        raise ValueError(
            f"Schwab token error: {exc}\n"
            "Run: uv run options-scanner/schwab_auth.py"
        ) from exc

    _client_cache[cache_key] = client
    return client


def fetch_live_price_schwab(client, ticker: str) -> float | None:
    """Return live mark price for ticker, or None on error."""
    try:
        ticker = normalize_ticker_schwab(ticker)
        resp = client.get_quote(ticker)
        resp.raise_for_status()
        data = resp.json()
        quote = data.get(ticker, {}).get("quote", {})
        price = quote.get("mark") or quote.get("lastPrice")
        return float(price) if price is not None else None
    except Exception as exc:
        log.warning("Could not fetch Schwab price for %s: %s", ticker, exc)
        return None


def fetch_option_chain_raw(client, ticker: str, min_dte: int,
                           max_dte: int | None) -> dict | None:
    """Fetch full option chain from Schwab API.

    Returns raw API response dict, or None on error.
    """
    import datetime
    from schwab.client import Client

    ticker = normalize_ticker_schwab(ticker)
    today = datetime.date.today()
    from_date = today + datetime.timedelta(days=min_dte)
    to_date = (today + datetime.timedelta(days=max_dte)
               if max_dte is not None else None)

    try:
        kwargs: dict = {
            "contract_type": Client.Options.ContractType.ALL,
            "from_date": from_date,
            "strategy": Client.Options.Strategy.SINGLE,
            "include_underlying_quote": True,
        }
        if to_date is not None:
            kwargs["to_date"] = to_date

        resp = client.get_option_chain(ticker, **kwargs)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.warning("Could not fetch Schwab option chain for %s: %s", ticker, exc)
        return None


def fetch_option_chain_schwab(client, ticker: str, exp_str: str):
    """Fetch a single expiration for roll close-cost lookup.

    Returns an object with .calls and .puts DataFrames (columns:
    strike, bid, ask, lastPrice) — same interface as
    stocks_shared.yahoo.fetch_option_chain().
    """
    import datetime
    from schwab.client import Client

    _empty = SimpleNamespace(
        calls=pd.DataFrame(columns=["strike", "bid", "ask", "lastPrice"]),
        puts=pd.DataFrame(columns=["strike", "bid", "ask", "lastPrice"]),
    )

    try:
        exp_date = datetime.date.fromisoformat(exp_str)
    except ValueError:
        return _empty

    try:
        resp = client.get_option_chain(
            ticker,
            contract_type=Client.Options.ContractType.ALL,
            from_date=exp_date,
            to_date=exp_date,
            strategy=Client.Options.Strategy.SINGLE,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("Could not fetch Schwab chain for %s %s: %s",
                    ticker, exp_str, exc)
        return None

    def _parse_side(exp_date_map: dict) -> pd.DataFrame:
        rows = []
        for key, strikes in exp_date_map.items():
            if not key.startswith(exp_str):
                continue
            for opts in strikes.values():
                for opt in opts:
                    rows.append({
                        "strike":    float(opt.get("strikePrice", 0)),
                        "bid":       float(opt.get("bid", 0) or 0),
                        "ask":       float(opt.get("ask", 0) or 0),
                        "lastPrice": float(opt.get("last", 0) or 0),
                    })
        return pd.DataFrame(rows) if rows else pd.DataFrame(
            columns=["strike", "bid", "ask", "lastPrice"])

    return SimpleNamespace(
        calls=_parse_side(data.get("callExpDateMap", {})),
        puts=_parse_side(data.get("putExpDateMap", {})),
    )
