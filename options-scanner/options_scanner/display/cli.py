"""Format and print option scanner results."""

from datetime import date, datetime, timedelta


def _fmt_exp(exp_str: str) -> str:
    return datetime.strptime(exp_str, "%Y-%m-%d").strftime("%b %d '%y")


def print_results(
    df,
    ticker: str,
    spot: float,
    earnings_dates: list,
    mode: str,
    roll_close_cost: float | None = None,
    min_oi: int = 25,
    top_n: int = 10,
    buy: bool = False,
) -> None:
    from tabulate import tabulate

    lt_date = (date.today() + timedelta(days=366)).strftime("%b %d '%y")
    action = "BUY" if buy else "SELL"

    print(f"\n{'-' * 68}")
    print(f"  {ticker}   spot: ${spot:.2f}   action: {action}")
    print(f"  LT close if opened today: {lt_date}")
    if earnings_dates:
        earn_strs = [d.strftime("%b %d") for d in earnings_dates[:4]]
        print(f"  Upcoming earnings: {', '.join(earn_strs)}")
    print(f"{'-' * 68}")

    df = df[df["open_interest"] >= min_oi].copy()
    if df.empty:
        print(f"  No options found (min OI={min_oi}).")
        return

    if roll_close_cost is not None:
        df["net_credit"] = df["mid"] - roll_close_cost

    type_labels = {"call": "CALLS", "put": "PUTS"}
    to_show = [mode] if mode in type_labels else list(type_labels.keys())

    # Selling: highest IV excess first. Buying: lowest (most negative) first.
    iv_asc = buy

    for opt_type in to_show:
        label = type_labels[opt_type]
        sub = (
            df[df["type"] == opt_type]
            .sort_values(["iv_excess", "open_interest"], ascending=[iv_asc, False])
            .head(top_n)
        )
        if sub.empty:
            continue

        print(f"\n  {label}")
        if roll_close_cost is not None:
            print(
                f"  close cost (mid): ${roll_close_cost:.2f} -- "
                f"net credit = new mid minus this"
            )

        rows = []
        for _, r in sub.iterrows():
            earn_tag = f" {int(r['earnings_count'])}E" if r["earnings_count"] > 0 else ""
            row = {
                "Strike": f"${r['strike']:.0f}",
                "Expiration": _fmt_exp(r["expiration"]) + earn_tag,
                "DTE": int(r["dte"]),
                "Bid": f"${r['bid']:.2f}",
                "Ask": f"${r['ask']:.2f}",
                "Mid": f"${r['mid']:.2f}",
                "IV%": f"{r['iv'] * 100:.1f}",
                "IV+pp": f"{r['iv_excess'] * 100:+.1f}",
                "Delta": f"{r['delta']:.2f}",
                "Ann%": f"{r['ann_yield_pct']:.1f}",
                "OI": f"{r['open_interest']:,}",
            }
            if roll_close_cost is not None:
                row["NetCr"] = f"${r['net_credit']:+.2f}"
            rows.append(row)

        print(tabulate(rows, headers="keys", tablefmt="simple"))

    print()
    if buy:
        print("  How to read this table (BUY mode):")
        print("  IV+pp  -- The key column. Negative = option's IV sits below the")
        print("            fitted surface (IV-cheap relative to neighbors).")
        print("            Under -3pp: meaningful ranking signal. Under -5pp: strong.")
        print("  Delta  -- Probability this option expires in the money (profitable).")
        print("            Higher delta = more likely to profit, but costs more.")
        print("  Ann%   -- Annualized cost of the premium as % of underlying value.")
        print("            Lower = cheaper option for the exposure you're buying.")
    else:
        print("  How to read this table (SELL mode):")
        print("  IV+pp  -- The key column. How many percentage points this option's IV")
        print("            sits above the fitted volatility surface. Higher = IV-rich")
        print("            relative to neighbors. Under 3pp: chain's IV is roughly")
        print("            uniform, no strike stands out. Over 5pp: stronger signal.")
        print("  Delta  -- Approximate probability of expiring in the money (i.e. being")
        print("            assigned). 0.30 = ~30% chance the stock closes above the")
        print("            strike at expiration. Lower delta = safer, less premium.")
        print("  Ann%   -- Annualized yield on the premium collected. Calls: vs. spot")
        print("            price. Puts: vs. strike (the capital you'd use to buy shares).")
    print()
