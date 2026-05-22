"""Options scanner — rank options by IV vs. a fitted surface, to sell or buy.

The ranking is a screening heuristic, not a mispricing or arbitrage
claim — IV+pp deviations can reflect skew, demand, event risk, or
stale prints just as easily as a tradeable signal.

Modes:
  (default)  show both calls and puts
  --calls    calls only
  --puts     puts only
  --buy      reverse ranking — surface IV-cheap candidates (below the surface)
  --roll     show net credit vs. closing an existing short position
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rank an option chain by IV vs. a fitted surface."
    )
    parser.add_argument("ticker", metavar="TICKER")

    side = parser.add_mutually_exclusive_group()
    side.add_argument("--calls", action="store_true", help="Show calls only")
    side.add_argument("--puts", action="store_true", help="Show puts only")

    parser.add_argument(
        "--buy",
        action="store_true",
        help="Buy mode: rank by IV vs. surface, lowest first "
             "(IV-cheap relative to neighbors)",
    )
    parser.add_argument(
        "--roll",
        action="store_true",
        help="Roll mode: display net credit vs. closing an existing position",
    )
    parser.add_argument(
        "--type",
        dest="roll_type",
        choices=["call", "put"],
        help="Option type of the position to roll (required with --roll)",
    )
    parser.add_argument(
        "--strike",
        dest="roll_strike",
        type=float,
        help="Strike of the position to roll (required with --roll)",
    )
    parser.add_argument(
        "--expiration",
        dest="roll_expiration",
        metavar="YYYY-MM-DD",
        help="Expiration of the position to roll (required with --roll)",
    )
    parser.add_argument(
        "--min-dte",
        type=int,
        default=30,
        help="Minimum days to expiration (default: 30)",
    )
    parser.add_argument(
        "--max-dte",
        type=int,
        default=90,
        metavar="N",
        help="Maximum days to expiration (default: 90)",
    )
    parser.add_argument(
        "--min-oi",
        type=int,
        default=25,
        help="Minimum open interest filter (default: 25)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Max rows to show in terminal (default: 10)",
    )
    parser.add_argument(
        "--min-delta",
        type=float,
        default=0.10,
        metavar="D",
        help="Exclude options where abs(delta) < D (default: 0.10)",
    )
    parser.add_argument(
        "--max-delta",
        type=float,
        default=0.75,
        metavar="D",
        help="Exclude options where abs(delta) > D (default: 0.75)",
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="Also save an HTML report to --output-dir",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        metavar="DIR",
        help="Directory for HTML output (default: options-scanner/output/)",
    )
    parser.add_argument(
        "--data-source",
        dest="data_source",
        choices=["yahoo", "schwab"],
        default=None,
        help="Data source override (default: from config.toml or 'yahoo')",
    )

    args = parser.parse_args()
    ticker = args.ticker.upper()

    if args.roll and not (args.roll_type and args.roll_strike and args.roll_expiration):
        parser.error("--roll requires --type, --strike, and --expiration")

    if args.max_dte is not None and args.max_dte < args.min_dte:
        parser.error("--max-dte must be >= --min-dte")

    # Load config and resolve provider
    from options_scanner.config import load_config, get_provider, get_schwab_config
    cfg = load_config()
    provider = args.data_source or get_provider(cfg)
    schwab_config = get_schwab_config(cfg)

    # Determine which side(s) to fetch
    if args.calls or (args.roll and args.roll_type == "call"):
        opt_type_fetch = "calls"
        mode = "call"
    elif args.puts or (args.roll and args.roll_type == "put"):
        opt_type_fetch = "puts"
        mode = "put"
    else:
        opt_type_fetch = "both"
        mode = "both"

    from options_scanner.chain import fetch_chain
    from options_scanner.iv_surface import compute_iv_excess
    from options_scanner.earnings import fetch_earnings_dates, annotate_earnings
    from options_scanner.display.cli import print_results

    log.info(
        "Fetching %s chain for %s (DTE %s) via %s...",
        opt_type_fetch,
        ticker,
        f"{args.min_dte}–{args.max_dte}" if args.max_dte else f">= {args.min_dte}",
        provider,
    )
    try:
        df = fetch_chain(
            ticker,
            opt_type=opt_type_fetch,
            min_dte=args.min_dte,
            max_dte=args.max_dte,
            provider=provider,
            schwab_config=schwab_config,
        )
    except ValueError as exc:
        sys.exit(str(exc))

    if df.empty:
        sys.exit(
            f"No options found for {ticker} in the specified DTE range. "
            "Try adjusting --min-dte or --max-dte."
        )

    log.info(
        "Found %d options across %d expirations. Fitting IV surface...",
        len(df),
        df["expiration"].nunique(),
    )
    df = compute_iv_excess(df)

    log.info("Fetching earnings dates...")
    earnings_dates = fetch_earnings_dates(ticker)
    df = annotate_earnings(df, earnings_dates)

    spot = float(df["spot"].iloc[0])

    df = df[df["delta"].abs().between(args.min_delta, args.max_delta)]
    if df.empty:
        sys.exit(
            f"No options remaining after delta filter "
            f"(abs delta {args.min_delta:.2f}–{args.max_delta:.2f})"
        )

    # Roll: look up mid price of the position being closed
    roll_close_cost: float | None = None
    if args.roll:
        log.info(
            "Looking up close cost for %s %s $%.0f %s via %s...",
            ticker, args.roll_type, args.roll_strike, args.roll_expiration,
            provider,
        )
        if provider == "schwab":
            from stocks_shared.schwab_live import get_client, fetch_option_chain_schwab
            try:
                schwab_client = get_client(
                    schwab_config["app_key"],
                    schwab_config["app_secret"],
                    schwab_config["callback_url"],
                    schwab_config["token_file"],
                )
                chain = fetch_option_chain_schwab(
                    schwab_client, ticker, args.roll_expiration
                )
            except ValueError as exc:
                log.warning("  Schwab roll lookup failed: %s", exc)
                chain = None
        else:
            from stocks_shared.yahoo import fetch_option_chain
            chain = fetch_option_chain(ticker, args.roll_expiration)

        if chain is not None:
            side_df = chain.calls if args.roll_type == "call" else chain.puts
            row = side_df[side_df["strike"] == args.roll_strike]
            if not row.empty:
                bid = float(row["bid"].iloc[0] or 0)
                ask = float(row["ask"].iloc[0] or 0)
                last = float(row["lastPrice"].iloc[0] or 0)
                roll_close_cost = (bid + ask) / 2 if bid > 0 and ask > 0 else last
                log.info("  Close cost (mid): $%.2f", roll_close_cost)
            else:
                log.warning("  Could not find current position in chain")
        else:
            log.warning("  Could not fetch chain for %s", args.roll_expiration)

    print_results(
        df,
        ticker,
        spot,
        earnings_dates,
        mode,
        roll_close_cost=roll_close_cost,
        min_oi=args.min_oi,
        top_n=args.top,
        buy=args.buy,
    )

    if args.html:
        from options_scanner.report import save_html
        action_tag = "buy" if args.buy else "sell"
        type_tag = mode if mode != "both" else "both"
        filename = f"{ticker}_{type_tag}_{action_tag}_{date.today().strftime('%Y%m%d')}.html"
        output_dir = (Path(args.output_dir) if args.output_dir
                      else Path(__file__).parents[1] / "output")
        output_path = output_dir / filename
        save_html(
            df,
            ticker,
            spot,
            earnings_dates,
            mode,
            buy=args.buy,
            roll_close_cost=roll_close_cost,
            min_oi=args.min_oi,
            output_path=output_path,
        )
        print(f"  HTML report: {output_path}")


if __name__ == "__main__":
    main()
