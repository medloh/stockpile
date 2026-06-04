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

Output:
  (default)  formatted terminal table with legend
  --json     JSON to stdout (single ticker: object; multiple: array)
  --agent    implies --json + --quiet; use from scripts and agents
"""

import argparse
import json as _json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)


def _opt_float(row, key):
    """float(row[key]) or None for missing/NaN — for nullable JSON fields."""
    if key not in row:
        return None
    try:
        f = float(row[key])
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN → None


def _to_candidate(row, roll_close_cost: float | None) -> dict:
    ss = _opt_float(row, "signal_score")
    hv = _opt_float(row, "hv_20")
    vr = _opt_float(row, "vr_ratio")
    c = {
        "type": str(row["type"]),
        "strike": float(row["strike"]),
        "expiration": str(row["expiration"]),
        "dte": int(row["dte"]),
        "bid": round(float(row["bid"]), 2),
        "ask": round(float(row["ask"]), 2),
        "mid": round(float(row["mid"]), 2),
        "iv_pct": round(float(row["iv"]) * 100, 1),
        "iv_pp": round(float(row["iv_excess"]) * 100, 1),
        "signal_score": round(ss, 4) if ss is not None else None,
        "signal_kind": str(row["signal_kind"]) if "signal_kind" in row else "IV+pp",
        "delta": round(float(row["delta"]), 3),
        "ann_pct": round(float(row["ann_yield_pct"]), 1),
        "open_interest": int(row["open_interest"]),
        "earnings_before_exp": bool(row["earnings_count"] > 0),
        "hv_20": round(hv, 4) if hv is not None else None,
        "vr_ratio": round(vr, 3) if vr is not None else None,
    }
    if roll_close_cost is not None:
        c["net_credit"] = round(float(row["mid"]) - roll_close_cost, 2)
    return c


def _candidates_for(df, mode, iv_asc, min_oi, min_vol, top,
                    roll_close_cost, ivpp_threshold=None):
    """Filter, rank, and serialize candidates from an already-fetched chain.

    iv_asc=True ranks IV-cheap-first (buy); False ranks IV-rich-first (sell).
    ivpp_threshold (if given) keeps only options at least that many pp on the
    correct side of the surface (>= thr for sell, <= -thr for buy).
    """
    df_filt = df[
        (df["open_interest"] >= min_oi) & (df["volume"] >= min_vol)
    ].copy()
    if ivpp_threshold is not None:
        if iv_asc:
            df_filt = df_filt[df_filt["iv_excess"] * 100 <= -ivpp_threshold]
        else:
            df_filt = df_filt[df_filt["iv_excess"] * 100 >= ivpp_threshold]
    sort_col = "signal_score" if "signal_score" in df_filt.columns else "iv_excess"
    types_to_show = ["call", "put"] if mode == "both" else [mode]
    candidates = []
    for opt_type in types_to_show:
        sub = (
            df_filt[df_filt["type"] == opt_type]
            .sort_values([sort_col, "open_interest"], ascending=[iv_asc, False])
            .head(top)
        )
        for _, row in sub.iterrows():
            candidates.append(_to_candidate(row, roll_close_cost))
    return candidates


def _candidates_with_fallback(df, mode, iv_asc, args, roll_close_cost,
                              ivpp_threshold=None):
    """Build candidates; if empty and a volume floor is set, retry at vol=0.

    When the market is closed the day's volume is 0 across the whole chain, so
    the default --min-vol wipes every row and the scan returns nothing useful.
    Relaxing volume (but NOT open interest, which is cumulative and survives a
    closed session) recovers the ranking. Returns (candidates, relaxed) where
    relaxed is True only when the retry is what produced the candidates.
    """
    cands = _candidates_for(
        df, mode, iv_asc, args.min_oi, args.min_vol, args.top,
        roll_close_cost, ivpp_threshold,
    )
    relaxed = False
    if not cands and args.min_vol > 0:
        cands = _candidates_for(
            df, mode, iv_asc, args.min_oi, 0, args.top,
            roll_close_cost, ivpp_threshold,
        )
        relaxed = bool(cands)
    return cands, relaxed


def _result_envelope(ticker: str, spot: float, provider: str) -> dict:
    return {
        "ticker": ticker,
        "spot": round(spot, 2),
        "data_source": provider,
        "scan_time": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _build_json_result(
    ticker: str,
    spot: float,
    df,
    mode: str,
    provider: str,
    args,
    roll_close_cost: float | None,
) -> dict:
    # ivpp already applied in _scan_one for single-mode; don't double-filter.
    candidates, relaxed = _candidates_with_fallback(
        df, mode, args.buy, args, roll_close_cost, ivpp_threshold=None,
    )
    result = _result_envelope(ticker, spot, provider)
    result["mode"] = "buy" if args.buy else "sell"
    result["candidates"] = candidates
    result["volume_filter_relaxed"] = relaxed
    return result


def _build_both_json_result(
    ticker: str,
    spot: float,
    df,
    mode: str,
    provider: str,
    args,
    roll_close_cost: float | None,
) -> dict:
    """Both rankings from a single fetched chain — no double fetch.

    _scan_one is called with apply_ivpp=False for --both, so the (direction-
    specific) IV+pp threshold is applied here, once per side.
    """
    thr = args.min_ivpp
    sell_c, sell_relaxed = _candidates_with_fallback(
        df, mode, False, args, roll_close_cost, ivpp_threshold=thr,
    )
    buy_c, buy_relaxed = _candidates_with_fallback(
        df, mode, True, args, roll_close_cost, ivpp_threshold=thr,
    )
    result = _result_envelope(ticker, spot, provider)
    result["mode"] = "both"
    result["sell"] = {"candidates": sell_c, "volume_filter_relaxed": sell_relaxed}
    result["buy"] = {"candidates": buy_c, "volume_filter_relaxed": buy_relaxed}
    return result


def _scan_one(ticker: str, args, opt_type_fetch: str, mode: str,
              provider: str, schwab_config: dict | None,
              apply_ivpp: bool = True):
    """Fetch and rank one ticker.

    Returns (df, spot, earnings_dates, roll_close_cost) on success,
    or None if the ticker cannot be scanned. apply_ivpp=False leaves the
    --min-ivpp filter unapplied so --both can apply it per-side downstream.
    """
    import numpy as np

    from options_scanner.chain import fetch_chain
    from options_scanner.iv_surface import compute_iv_excess
    from options_scanner.iv_scores import ScoreContext
    from options_scanner.earnings import fetch_earnings_dates, annotate_earnings
    from options_scanner import iv_history
    from stocks_shared.yahoo import realized_vol

    log.info(
        "Fetching %s chain for %s (DTE %s–%s) via %s...",
        opt_type_fetch, ticker, args.min_dte, args.max_dte, provider,
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
        log.error("Error fetching %s: %s", ticker, exc)
        return None

    if df.empty:
        log.error(
            "No options found for %s in DTE %s–%s.", ticker, args.min_dte, args.max_dte
        )
        return None

    log.info("Fetching earnings dates...")
    earnings_dates = fetch_earnings_dates(ticker)
    # Annotate earnings BEFORE the fit so the exclude_earnings filter
    # (if selected) can see earnings_count.
    df = annotate_earnings(df, earnings_dates)

    log.info(
        "Found %d options across %d expirations. Fitting IV surface...",
        len(df), df["expiration"].nunique(),
    )
    hv = realized_vol(ticker)
    ctx = ScoreContext(ticker=ticker, hv_20=hv, history=iv_history)
    df = compute_iv_excess(
        df,
        surface_filters=getattr(args, "surface_filters", None),
        algo_config=getattr(args, "algo_config", None),
        score_config=getattr(args, "score_config", None),
        ctx=ctx,
    )
    df["hv_20"] = hv
    df["vr_ratio"] = (df["iv"] / hv) if (np.isfinite(hv) and hv > 0) \
        else float("nan")
    iv_history.record_scan(ticker, df)

    spot = float(df["spot"].iloc[0])

    df = df[df["delta"].abs().between(args.min_delta, args.max_delta)]
    if df.empty:
        log.error(
            "No options remaining for %s after delta filter (abs delta %.2f–%.2f).",
            ticker, args.min_delta, args.max_delta,
        )
        return None

    if args.min_strike is not None:
        df = df[df["strike"] >= args.min_strike]
    if args.max_strike is not None:
        df = df[df["strike"] <= args.max_strike]
    if df.empty:
        log.error("No options remaining for %s after strike filter.", ticker)
        return None

    if apply_ivpp and args.min_ivpp is not None:
        if args.buy:
            df = df[df["iv_excess"] * 100 <= -args.min_ivpp]
        else:
            df = df[df["iv_excess"] * 100 >= args.min_ivpp]
        if df.empty:
            log.warning(
                "No options for %s met the --min-ivpp %.1f threshold.", ticker, args.min_ivpp
            )
            # Return empty df — caller gets an empty candidates list rather than nothing

    roll_close_cost: float | None = None
    if args.roll:
        log.info(
            "Looking up close cost for %s %s $%.0f %s via %s...",
            ticker, args.roll_type, args.roll_strike, args.roll_expiration, provider,
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
                log.warning("  Could not find current position in chain — assuming $0.00 close cost (deep OTM / worthless).")
                roll_close_cost = 0.0
        else:
            log.warning("  Could not fetch chain for %s.", args.roll_expiration)

    return df, spot, earnings_dates, roll_close_cost


def _html_path(ticker: str, mode: str, args) -> Path:
    """Build the output path for an HTML report."""
    action_tag = "roll" if args.roll else ("buy" if args.buy else "sell")
    type_tag = mode if mode != "both" else "both"
    parts = [ticker, type_tag, action_tag]

    if args.roll and args.roll_strike is not None:
        parts.append(f"k{args.roll_strike:g}")

    if args.min_dte != 30 or args.max_dte != 90:
        parts.append(f"dte{args.min_dte}-{args.max_dte}")

    if args.min_strike is not None or args.max_strike is not None:
        lo = f"{args.min_strike:g}" if args.min_strike is not None else ""
        hi = f"{args.max_strike:g}" if args.max_strike is not None else ""
        parts.append(f"s{lo}-{hi}" if lo and hi else f"smin{lo}" if lo else f"smax{hi}")

    if args.top != 4:
        parts.append(f"top{args.top}")

    parts.append(date.today().strftime('%Y%m%d'))
    filename = "_".join(parts) + ".html"
    output_dir = (Path(args.output_dir) if args.output_dir
                  else Path(__file__).parents[1] / "output")
    return output_dir / filename


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rank an option chain by IV vs. a fitted surface."
    )
    parser.add_argument(
        "ticker", metavar="TICKER", nargs="+",
        help="One or more ticker symbols to scan",
    )

    side = parser.add_mutually_exclusive_group()
    side.add_argument("--calls", action="store_true", help="Show calls only")
    side.add_argument("--puts", action="store_true", help="Show puts only")

    parser.add_argument(
        "--buy", action="store_true",
        help="Buy mode: rank by IV vs. surface, lowest first "
             "(IV-cheap relative to neighbors)",
    )
    parser.add_argument(
        "--both", action="store_true",
        help="Both modes from a single fetch: emit sell and buy rankings "
             "(JSON: nested 'sell'/'buy' objects, each with its own "
             "volume_filter_relaxed flag). Avoids fetching the chain twice.",
    )
    parser.add_argument(
        "--roll", action="store_true",
        help="Roll mode: display net credit vs. closing an existing position",
    )
    parser.add_argument(
        "--type", dest="roll_type", choices=["call", "put"],
        help="Option type of the position to roll (required with --roll)",
    )
    parser.add_argument(
        "--strike", dest="roll_strike", type=float,
        help="Strike of the position to roll (required with --roll)",
    )
    parser.add_argument(
        "--expiration", dest="roll_expiration", metavar="YYYY-MM-DD",
        help="Expiration of the position to roll (required with --roll)",
    )
    parser.add_argument(
        "--min-dte", type=int, default=30,
        help="Minimum days to expiration (default: 30)",
    )
    parser.add_argument(
        "--max-dte", type=int, default=90, metavar="N",
        help="Maximum days to expiration (default: 90)",
    )
    parser.add_argument(
        "--min-oi", type=int, default=25,
        help="Minimum open interest filter (default: 25)",
    )
    parser.add_argument(
        "--min-vol", type=int, default=10,
        help="Minimum today's volume filter (default: 10)",
    )
    parser.add_argument(
        "--top", type=int, default=4,
        help="Max rows per option type in terminal or JSON (default: 4)",
    )
    parser.add_argument(
        "--min-delta", type=float, default=0.10, metavar="D",
        help="Exclude options where abs(delta) < D (default: 0.10)",
    )
    parser.add_argument(
        "--max-delta", type=float, default=0.75, metavar="D",
        help="Exclude options where abs(delta) > D (default: 0.75)",
    )
    parser.add_argument(
        "--min-strike", type=float, default=None, metavar="X",
        help="Exclude options with strike below X",
    )
    parser.add_argument(
        "--max-strike", type=float, default=None, metavar="X",
        help="Exclude options with strike above X",
    )
    parser.add_argument(
        "--min-ivpp", type=float, default=None, metavar="N",
        help="Only show options where IV+pp >= N pp above the surface "
             "(sell mode) or >= N pp below (buy mode)",
    )
    parser.add_argument(
        "--html", action="store_true",
        help="Save an HTML report to --output-dir (works with --agent too)",
    )
    parser.add_argument(
        "--browser", action="store_true",
        help="Open the HTML report in the default browser after saving "
             "(implies --html)",
    )
    parser.add_argument(
        "--output-dir", default=None, metavar="DIR",
        help="Directory for HTML output (default: options-scanner/output/)",
    )
    parser.add_argument(
        "--data-source", dest="data_source", choices=["yahoo", "schwab"],
        default=None,
        help="Data source override (default: from config.toml or 'yahoo')",
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Emit JSON to stdout instead of a formatted table",
    )
    parser.add_argument(
        "--agent", action="store_true",
        help="Agent mode: implies --json and --quiet. Use when calling from "
             "scripts or AI agents",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress progress logging to stderr",
    )
    parser.add_argument(
        "--no-legend", action="store_true",
        help="Suppress the 'how to read this table' legend",
    )

    from options_scanner import iv_algorithms, iv_scores
    from options_scanner.iv_filters import DEFAULT_CONFIG as _FILTER_DEFAULT
    _algo_choices = [n for n, e in iv_algorithms.REGISTRY.items()
                     if e.get("enabled", True)]
    _score_choices = [n for n, e in iv_scores.REGISTRY.items()
                      if e.get("enabled", True)]
    parser.add_argument(
        "--preset", choices=["current", "v2"], default="current",
        help="Surface-fit preset: 'current' (global poly + raw IV+pp) or "
             "'v2' (per-expiration spread-weighted, earnings excluded, "
             "z-score). Default: current",
    )
    parser.add_argument(
        "--algorithm", choices=_algo_choices, default=None,
        help="Override the preset's surface-fit algorithm",
    )
    parser.add_argument(
        "--fit-weights", choices=["none", "oi", "inv_spread"], default=None,
        help="Regression weighting for the fit (with --algorithm)",
    )
    parser.add_argument(
        "--score", choices=_score_choices, default=None,
        help="Override the preset's ranking score",
    )

    args = parser.parse_args()

    if args.agent:
        args.as_json = True
        args.quiet = True
    if args.browser:
        args.html = True

    # Resolve the pluggable surface-fit configs from preset + overrides.
    _presets = {
        "current": (_FILTER_DEFAULT, ("global_poly", frozenset()),
                    ("raw_pp", frozenset())),
        "v2": (_FILTER_DEFAULT + (("exclude_earnings", frozenset()),),
               ("per_expiration", frozenset({("weights", "inv_spread")})),
               ("zscore", frozenset())),
    }
    args.surface_filters, args.algo_config, args.score_config = _presets[args.preset]
    if args.algorithm:
        _w = frozenset({("weights", args.fit_weights)}) if args.fit_weights \
            else frozenset()
        args.algo_config = (args.algorithm, _w)
    if args.score:
        args.score_config = (args.score, frozenset())

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    tickers = [t.upper() for t in args.ticker]

    if args.roll and not (args.roll_type and args.roll_strike and args.roll_expiration):
        parser.error("--roll requires --type, --strike, and --expiration")
    if args.roll and len(tickers) > 1:
        parser.error("--roll requires a single ticker")
    if args.both and args.buy:
        parser.error("--both cannot be combined with --buy (it emits both)")
    if args.both and args.roll:
        parser.error("--both cannot be combined with --roll")
    if args.max_dte < args.min_dte:
        parser.error("--max-dte must be >= --min-dte")

    if args.calls or (args.roll and args.roll_type == "call"):
        opt_type_fetch = "calls"
        mode = "call"
    elif args.puts or (args.roll and args.roll_type == "put"):
        opt_type_fetch = "puts"
        mode = "put"
    else:
        opt_type_fetch = "both"
        mode = "both"

    from options_scanner.config import load_config, get_provider, get_schwab_config
    cfg = load_config()
    provider = args.data_source or get_provider(cfg)
    schwab_config = get_schwab_config(cfg)

    from options_scanner.display.cli import print_results

    scan_params = {
        "data_source": provider,
        "preset": args.preset,
        "algorithm": args.algo_config[0],
        "score": args.score_config[0],
        "min_dte": args.min_dte,
        "max_dte": args.max_dte,
        "min_delta": args.min_delta,
        "max_delta": args.max_delta,
        "min_oi": args.min_oi,
        "min_vol": args.min_vol,
        "top": args.top,
        "min_strike": args.min_strike,
        "max_strike": args.max_strike,
        "roll": args.roll,
        "roll_type": args.roll_type if args.roll else None,
        "roll_strike": args.roll_strike if args.roll else None,
        "roll_expiration": args.roll_expiration if args.roll else None,
    }

    json_results = []
    any_success = False

    for ticker in tickers:
        result = _scan_one(ticker, args, opt_type_fetch, mode, provider,
                           schwab_config, apply_ivpp=not args.both)
        if result is None:
            if len(tickers) == 1:
                sys.exit(1)
            continue

        df, spot, earnings_dates, roll_close_cost = result
        any_success = True

        if args.as_json:
            if args.both:
                json_result = _build_both_json_result(
                    ticker, spot, df, mode, provider, args, roll_close_cost
                )
            else:
                json_result = _build_json_result(
                    ticker, spot, df, mode, provider, args, roll_close_cost
                )
            if args.html:
                from options_scanner.report import save_html
                html_path = _html_path(ticker, mode, args)
                save_html(
                    df, ticker, spot, earnings_dates, mode,
                    buy=args.buy, roll_close_cost=roll_close_cost,
                    min_oi=args.min_oi, min_vol=args.min_vol,
                    output_path=html_path, scan_params=scan_params,
                    top_n=args.top,
                )
                json_result["html_report"] = str(html_path)
                if args.browser:
                    import webbrowser
                    webbrowser.open(html_path.as_uri())
            json_results.append(json_result)
        else:
            for side_buy in ((False, True) if args.both else (args.buy,)):
                print_results(
                    df, ticker, spot, earnings_dates, mode,
                    roll_close_cost=roll_close_cost,
                    min_oi=args.min_oi,
                    min_vol=args.min_vol,
                    top_n=args.top,
                    buy=side_buy,
                    no_legend=args.no_legend,
                )
            if args.html:
                from options_scanner.report import save_html
                html_path = _html_path(ticker, mode, args)
                save_html(
                    df, ticker, spot, earnings_dates, mode,
                    buy=args.buy, roll_close_cost=roll_close_cost,
                    min_oi=args.min_oi, min_vol=args.min_vol,
                    output_path=html_path, scan_params=scan_params,
                    top_n=args.top,
                )
                print(f"  HTML report: {html_path}")
                if args.browser:
                    import webbrowser
                    webbrowser.open(html_path.as_uri())

    if args.as_json:
        output = json_results[0] if len(tickers) == 1 else json_results
        print(_json.dumps(output, indent=2))

    if not any_success and len(tickers) > 1:
        sys.exit(1)


if __name__ == "__main__":
    main()
