#!/usr/bin/env python3
"""Generate cost basis vs. price charts for all symbols in a brokerage CSV."""

import argparse
import logging
import sys
from pathlib import Path

from stocks_shared.yahoo import fetch_history, estimate_option_history
import config
from cost_basis import compute_cost_basis_series
from charts import create_cost_basis_chart

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)


def _get_parser(brokerage: str):
    b = brokerage.lower()
    if b == "schwab":
        from stocks_shared.parsers.schwab import parse_all_transactions
    elif b == "robinhood":
        from stocks_shared.parsers.robinhood import parse_all_transactions
    elif b == "fidelity":
        from stocks_shared.parsers.fidelity import parse_all_transactions
    elif b == "merrill":
        from stocks_shared.parsers.merrill import parse_all_transactions
    elif b == "stockpile":
        from stocks_shared.parsers.stockpile import parse_all_transactions
    else:
        sys.exit(f"Unknown brokerage '{brokerage}'. Supported: schwab, robinhood, fidelity, merrill, stockpile")
    return parse_all_transactions


def main():
    parser = argparse.ArgumentParser(description="Generate cost basis charts from a brokerage CSV")
    parser.add_argument("--csv", metavar="FILE", help="Override the CSV path from config.toml")
    parser.add_argument("--brokerage", metavar="NAME", choices=["schwab", "robinhood", "fidelity", "merrill", "stockpile"],
                        help="Only run accounts for this brokerage")
    parser.add_argument("--symbol", metavar="TICKER",
                        help="Chart only this symbol (overrides config.toml symbols list)")
    parser.add_argument("--output-dir", metavar="DIR", help="Override output directory from config.toml")
    parser.add_argument("--png", action="store_true", help="Also save a static PNG alongside each HTML chart")
    args = parser.parse_args()

    output_dir = Path(args.output_dir) if args.output_dir else config.OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    accounts = config.get_all_accounts(brokerage_filter=args.brokerage)
    if not accounts:
        sys.exit("No accounts configured. Copy config.toml.example to config.toml and fill it in.")

    for acct in accounts:
        csv_path = args.csv or acct.csv
        if not csv_path:
            log.warning("No CSV path for %s account, skipping.", acct.brokerage)
            continue

        log.info("Parsing %s (%s)...", csv_path, acct.brokerage)
        parse_all = _get_parser(acct.brokerage)
        ticker_transactions, _ = parse_all(csv_path)

        # Priority: --symbol CLI arg > config symbols list > all symbols in CSV
        if args.symbol:
            symbols = [args.symbol]
        elif acct.symbols:
            symbols = acct.symbols
        else:
            symbols = sorted(ticker_transactions)

        for symbol in symbols:
            txns = ticker_transactions.get(symbol)
            if not txns:
                log.warning("No transactions found for %s", symbol)
                continue

            log.info("Processing %s (%d transactions)...", symbol, len(txns))
            series, open_options = compute_cost_basis_series(txns)
            if not series:
                log.info("  No stock transactions for %s, skipping.", symbol)
                continue

            start_date = series[0]["date"].strftime("%Y-%m-%d")
            log.info("  Fetching Yahoo Finance history from %s...", start_date)
            price_history = fetch_history(symbol, start=start_date)
            if price_history.empty:
                log.warning("  No price data from Yahoo Finance for %s, skipping.", symbol)
                continue

            # Build live adjusted cost time series using Black-Scholes for open options
            live_cost_series = None
            if open_options:
                import pandas as pd
                cb_df = pd.DataFrame(series)
                cb_df["date"] = pd.to_datetime(cb_df["date"])
                cb_df = cb_df.set_index("date").sort_index()
                cb_df = cb_df[~cb_df.index.duplicated(keep="last")]
                today = pd.Timestamp.today().normalize()
                all_bdays = pd.date_range(start=cb_df.index.min(), end=today, freq="B")
                cb_ffill = cb_df[["adjusted_cost", "shares"]].reindex(all_bdays).ffill().dropna()

                ph = price_history.copy()
                if ph.index.tz is not None:
                    ph.index = ph.index.tz_convert(None)
                ph.index = ph.index.normalize()

                earliest_open = min(pd.Timestamp(opt["open_date"]) for opt in open_options)
                live_dates = cb_ffill.index[(cb_ffill.index >= earliest_open) & (cb_ffill.index <= today)]
                live_dates = live_dates.intersection(ph.index.append(pd.DatetimeIndex([today])))

                total_liability   = pd.Series(0.0, index=live_dates)
                intrinsic_series  = pd.Series(0.0, index=live_dates)
                time_value_series = pd.Series(0.0, index=live_dates)

                adj  = cb_ffill["adjusted_cost"].reindex(live_dates, method="ffill")
                shrs = cb_ffill["shares"].reindex(live_dates, method="ffill")

                for opt in open_options:
                    opt_df = estimate_option_history(
                        ph, opt["opt_type"], opt["strike"],
                        opt["expiration"], opt["open_date"], opt["qty"],
                    )
                    if opt_df is None:
                        continue
                    factor = opt["qty"] * 100
                    total_liability   += opt_df["total_value"].reindex(live_dates).fillna(0)
                    intrinsic_series  += (opt_df["intrinsic_per_share"].reindex(live_dates).fillna(0) * factor / shrs)
                    time_value_series += (opt_df["time_value_per_share"].reindex(live_dates).fillna(0) * factor / shrs)

                live_cost_series  = (adj + total_liability / shrs).dropna().round(4)
                intrinsic_series  = intrinsic_series.dropna().round(4)
                time_value_series = time_value_series.dropna().round(4)
                log.info("  Live adj. cost series: %d points, last=$%.4f",
                         len(live_cost_series), live_cost_series.iloc[-1] if not live_cost_series.empty else 0)

            output_path = output_dir / f"{symbol}_cost_basis.html"
            option_breakdown = {"intrinsic": intrinsic_series, "time_value": time_value_series} if open_options else None
            create_cost_basis_chart(symbol, price_history, series, str(output_path), live_cost_series, option_breakdown, open_options, save_png=args.png)

    log.info("Done.")


if __name__ == "__main__":
    main()
