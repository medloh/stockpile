# options-scanner

Scans an option chain and ranks each option by how far its implied
volatility sits above or below a fitted volatility surface. Use it
to surface IV-rich candidates for covered calls, cash-secured puts,
and roll setups — or, in buy mode, IV-cheap candidates.

The ranking is a **screening heuristic, not a mispricing or
arbitrage claim**: vol smiles and skew are real, and a strike
sitting above the fit can reflect demand pressure, event-specific
risk, or stale data as easily as a genuine signal. Treat the
output as a starting point for further analysis on your broker.
See [INTERPRETING_IV.md](INTERPRETING_IV.md) for what IV+pp
actually means mechanically, why an outlier is not the same as
edge, and how to read the magnitudes in practice.

Three entry points:

- **Web UI** — browser-based, no CLI knowledge required. Recommended.
- **CLI scanner** — single ticker, scriptable.
- **Portfolio scanner** — reads a brokerage CSV and scans every open
  position.

Data is sourced from **Yahoo Finance** (default, no setup) or the
**Schwab developer API** (real-time quotes and actual Greeks). See
[SCHWAB_DATA_SOURCE.md](SCHWAB_DATA_SOURCE.md) to enable Schwab.

For repo-wide setup (`uv sync`, etc.) see the
[root README](../README.md#setup).

## Web UI

```bash
uv run streamlit run options-scanner/run_app.py
```

A browser tab opens at `http://localhost:8501` with five tabs:

- **Single Ticker** — type a symbol, pick Calls/Puts/Both and Sell/Buy,
  hit Scan. Filter inputs: Min DTE / Max DTE, Min OI, Min Vol (today's
  trading volume), delta range, Top N. You get a volatility-surface
  chart (top picks labeled with their rank — `1` is the strongest
  signal per type), a per-expiration chain view sorted by strike with
  IV+pp row shading and a "Top" column showing the same rank, and a
  top candidates table ranked across all expirations. A Market View
  card explains what your Direction × Option Type selection screens
  for, and selecting any candidate row opens an MC Analyze panel
  with the per-trade P&L distribution. The data source (Yahoo Finance
  / Schwab) toggle sits in the title bar so you can flip between
  sources without opening the sidebar.
- **Portfolio** — drag in a brokerage CSV (Schwab, Robinhood, Fidelity,
  Merrill, or a hand-written
  [stockpile file](../docs/stockpile-format.md)), pick the format, hit
  Scan Portfolio. Each position gets its own chart and table in a
  collapsible section, with a Recommended Action card above the table
  translating the top IV-rich pick into explicit SELL TO OPEN / ROLL
  instructions. The validator runs automatically on upload and shows
  any problems before you scan.
- **Spreads** — power-user view of 13 multi-leg strategies ranked by
  risk/reward subject to a POP threshold. Click a row to see the
  payoff diagram (at-expiry + current value).
- **Directional** — bullish/bearish strategies only (verticals,
  jade lizard, risk reversal).
- **Neutral** — range-bound and delta-neutral strategies with a
  Max \|Δ\| slider for income hunting on long-DTE underlyings.

See [SPREADS.md](SPREADS.md) for the full strategy catalog, column
reference, POP math, and caveats.

See [MONTE_CARLO.md](MONTE_CARLO.md) for the MC Analyze panel
(per-trade P&L distribution under GBM + earnings jumps), the
Market View card, the Portfolio Recommended Action card, and what
the simulation's assumptions get wrong.

Single Ticker and Portfolio tabs offer a Download HTML Report button.

### What's actually running at localhost:8501

`streamlit run` starts a local **Uvicorn** web server. The browser
loads the page over HTTP, then opens a persistent **WebSocket** that
streams widget changes back to Python; every interaction re-runs
`run_app.py` top-to-bottom and pushes the new output to the page.

By default Streamlit binds to `0.0.0.0`, so the app is reachable from
other machines on your network at the "Network URL" Streamlit prints
(e.g. `http://10.0.0.5:8501`). For solo home use that's harmless. On a
shared/public network, pass `--server.address 127.0.0.1` to bind only
to localhost.

To stop the server: `Ctrl+C` in the terminal where you started it.
There is no in-app shutdown button.

### Common problems

**`Port 8501 is already in use` (or app appears on `:8502`)**
A previous Streamlit is still running. Stop it with `Ctrl+C` in its
terminal, or pass `--server.port 9000` to use a different port.

**Browser doesn't open automatically**
Happens on some Windows setups and over SSH. Just paste the URL the
terminal printed. Pass `--server.headless true` to suppress the
auto-open attempt.

**Windows Firewall prompt the first time**
Allow on Private networks; deny Public.

**First scan takes 5–15 seconds**
Normal — fetching the chain from Yahoo Finance, fitting the surface,
looking up earnings. There's a spinner.

**Empty chart on a ticker that worked moments ago**
Yahoo throttling. The 5-minute cache mitigates repeated scans of the
same ticker; otherwise wait it out.

**Edited `run_app.py` and the chart still looks wrong**
Streamlit auto-reloads code, but `@st.cache_data` results survive
across reruns. Open the hamburger menu (top-right) → **Clear cache** →
rerun.

**`ModuleNotFoundError` after a `git pull`**
Dependencies changed. Run `uv sync` from the repo root.

## Portfolio scanner (CLI)

```bash
uv run options-scanner/run_portfolio.py --csv input/schwab028.csv
uv run options-scanner/run_portfolio.py --csv input/schwab028.csv \
    --html --tickers AAPL AMD
```

Reads the CSV, finds every open stock position, and runs a sell scan
on each. Positions with an existing covered call get a roll scan
showing the `NetCr` column instead. Add `--html` for one combined
report covering the whole account.

## CLI scanner

Always run from the **repo root** using `uv run`:

```bash
# Covered call selection
uv run options-scanner/run_scanner.py AMD --calls

# Cash-secured put selection
uv run options-scanner/run_scanner.py AMD --puts

# Both calls and puts
uv run options-scanner/run_scanner.py AMD

# Narrow to a delta range (e.g. 0.20–0.45 sweet spot)
uv run options-scanner/run_scanner.py AMD --calls \
    --min-delta 0.20 --max-delta 0.45

# Roll an existing short call
uv run options-scanner/run_scanner.py AMD --roll \
    --type call --strike 600 --expiration 2026-01-16
```

### Index tickers

Each data source uses a different prefix for cash-settled index
options. The scanner normalizes automatically so you can always type
the bare name:

| Index | Yahoo Finance | Schwab |
|-------|--------------|--------|
| S&P 500 | `^SPX` / `^SPXW` | `$SPX` / `$SPXW` |
| Nasdaq 100 | `^NDX` / `^NDXP` | `$NDX` / `$NDXP` |
| Russell 2000 | `^RUT` | `$RUT` |
| VIX | `^VIX` | `$VIX` |
| Dow Jones | `^DJI` / `^INDU` | `$DJI` / `$INDU` |
| S&P 100 | `^OEX` / `^XEO` | `$OEX` / `$XEO` |
| Volatility (Nasdaq/Russell) | `^VXN` / `^RVX` | `$VXN` / `$RVX` |
| Treasury rates | `^TNX` / `^TYX` | `$TNX` / `$TYX` |

All of these forms resolve to the same result:

```
SPX        bare name — works on both Yahoo and Schwab
^SPX       Yahoo Finance native form — also works on Schwab
$SPX       Schwab native form — also works on Yahoo Finance
```

**Escaping:** If a ticker symbol conflicts with a known index name
(e.g. `SPX` is also NYSE-listed SPX Corp), append `!` to bypass
normalization and query the underlying stock directly:

```
SPX!       use exactly "SPX" — fetches the stock, not the index
```

### All options

| Flag | Default | Meaning |
|------|---------|---------|
| `--calls` / `--puts` | both | Show only calls or only puts |
| `--buy` | off | Buy mode: rank by IV vs. surface, lowest first (IV-cheap relative to neighbors) |
| `--min-dte` | 30 | Minimum days to expiration |
| `--max-dte` | 90 | Maximum days to expiration |
| `--min-oi` | 25 | Minimum open interest. Filters the top candidates table only; the volatility-surface chart and per-expiration chain table show all strikes, with low-OI rows shaded yellow as a liquidity warning. |
| `--min-delta` | 0.10 | Exclude abs(delta) below this |
| `--max-delta` | 0.75 | Exclude abs(delta) above this |
| `--top` | 10 | Max rows shown in terminal |
| `--html` | off | Save an HTML report (see below) |
| `--output-dir` | `options-scanner/output/` | Directory for HTML files |
| `--roll` | — | Roll mode (requires `--type`, `--strike`, `--expiration`) |
| `--data-source` | from config | `yahoo` or `schwab` — overrides config.toml |

### HTML report

Add `--html` to save a self-contained HTML file alongside the
terminal output:

```bash
uv run options-scanner/run_scanner.py AMD --calls --html
```

The file is written to `options-scanner/output/` by default, named
`{TICKER}_{type}_{action}_{date}.html` (e.g.
`AMD_call_sell_20260505.html`). Open it in any browser — columns are
sortable by clicking the headers, and the IV+pp column is
color-coded (green = IV-rich, a candidate to consider selling;
red = IV-cheap, a candidate to consider buying).

Override the directory with `--output-dir path/to/dir`.

## Output columns

| Column | What it means |
|--------|--------------|
| Top | Web UI only. Rank within the top-N list per option type (1 = strongest signal). Blank for rows that didn't make the cut. |
| Strike | Option strike price |
| Expiration | Expiration date |
| DTE | Days to expiration |
| Bid / Ask / Mid | Market prices |
| IV% | Implied volatility (annualized) |
| IV+pp | IV excess above the fitted surface (see below) |
| Delta | Approx. probability of expiring in the money |
| Ann% | Annualized yield on premium (calls vs. spot; puts vs. strike) |
| OI | Open interest |
| Vol | Web UI only. Today's trading volume — short-term liquidity signal complementing OI. |
| NetCr | Roll mode only: new mid minus close cost |

## Example output and how to read it

```
--------------------------------------------------------------------
  AMD   spot: $355.26   LT close if opened today: May 06 '27
  Upcoming earnings: May 05
--------------------------------------------------------------------

  CALLS
Strike  Expiration      DTE  Bid     Ask     Mid      IV%  IV+pp  Delta  Ann%    OI
------  ------------  -----  ------  ------  ------  ----  -----  -----  ----  ----
$700    Jun 17 '27      408  $27.15  $29.60  $28.38  65.1   +1.6   0.29   7.1   461
$590    Jun 17 '27      408  $39.40  $42.55  $40.97  65.2   +1.3   0.38  10.3    59
$600    Jun 17 '27      408  $37.90  $40.45  $39.17  64.9   +1.1   0.36   9.9   473
$530    Jun 17 '27      408  $48.75  $52.30  $50.52  65.2   +1.0   0.44  12.7  2179
$520    Jun 17 '27      408  $50.45  $54.45  $52.45  65.3   +1.0   0.45  13.2   474
```

### Is there a genuine IV outlier?

Look at the `IV+pp` column first. If the top value is under ~3pp,
the chain's IV is roughly uniform and the ranking is mostly noise.
In the AMD example above, the max is +1.6pp — all these options
sit close to the fitted surface. When you see IV+pp of 5pp or more
on a specific strike, that's a stronger ranking signal worth a
closer look on your broker.

See [INTERPRETING_IV.md](INTERPRETING_IV.md) for what those numbers
mean mechanically and why an outlier isn't the same as a
mispricing.

### Picking a strike

When IV+pp is flat across the chain (as above), the decision comes
down to your own risk tolerance:

**Lower delta (e.g. $700, delta 0.29):**
- ~29% chance of assignment at expiration
- Collects $28.38 per share (~7% annualized)
- More room for the stock to run before you're called away

**Higher delta (e.g. $530, delta 0.44):**
- ~44% chance of assignment — roughly a coin flip
- Collects $50.52 per share (~12.7% annualized)
- Much better premium, but real risk of losing the shares

A common covered call sweet spot is delta 0.25–0.40, which balances
premium against assignment risk. Use `--delta-min 0.25 --delta-max
0.40` to filter to that range.

### Earnings tag

`1E` next to an expiration means one earnings event falls before
that date. Elevated IV near earnings is expected and is not a free
lunch — see [INTERPRETING_IV.md](INTERPRETING_IV.md#earnings-and-iv)
for why.

### LT capital gains

The header shows the earliest date you could close to qualify for
long-term capital gains treatment (open date + 366 days). If you sell
today and close after that date, the premium is taxed at the LT rate.
In the example: sell today, close any time after **May 06 '27**.

### Ann% for puts

For puts, `Ann%` is calculated as premium divided by the **strike
price** (the capital you'd need to buy 100 shares if assigned),
annualized. This gives the true return on capital at risk.

## Gamma Exposure (GEX)

The web UI single-ticker tab shows a **GEX bar chart** below the
volatility surface chart. It is not available in the CLI.

### What it is

GEX measures the aggregate gamma that market makers (dealers) hold
across every strike in the chain. Because dealers typically sell
options to retail buyers, they end up short gamma. To stay delta-
neutral they must hedge:

- **Short gamma (negative GEX):** dealers buy stock as price rises
  and sell as it falls — amplifying moves in both directions.
- **Long gamma (positive GEX):** dealers sell into rallies and buy
  dips — dampening moves and pinning price near high-OI strikes.

### How to read the chart

The chart shows net GEX per strike as green (positive / pinning) or
red (negative / amplifying) bars. The dashed vertical line marks
the current spot price (with the `Spot $XXX.XX` label next to it).
The chart title carries the ticker symbol and the caption notes how
many expirations and what DTE range were summed — so a screenshot
stays self-explanatory days later.

Three summary metrics appear above the chart:

| Metric | What it means |
|--------|--------------|
| **Total GEX** | Sum across all strikes. Positive = pinning regime; negative = amplifying regime. |
| **Regime** | Plain-English label for the current total GEX sign. |
| **Zero-gamma level** | The strike where cumulative dealer gamma flips from positive to negative. Price above this level tends to behave more volatilely. |

### What it tells a covered call seller

A large **green wall above your strike** means dealers are long gamma
there — their hedging activity tends to cap the stock near that
level, acting like a ceiling. The stock has trouble breaking
through, which is what a covered call seller wants.

A **red zone above your strike** means the opposite — if the stock
enters that range, dealer hedging amplifies the move and your call
is more likely to get tested.

### Caveats

**Long-dated chains are thin.** GEX is most reliable on heavily
traded near-term options (0–60 DTE) where OI is large and IVs are
fresh. LEAPS and other far-dated options have lower OI and wider
bid/ask spreads, so treat the chart as directional context rather
than a precise signal.

**Yahoo Finance data quality.** When using Yahoo Finance, gamma is
estimated via Black-Scholes from Yahoo's IV, which can be hours or
days old on LEAPS and other far-dated strikes. GEX computed from
stale IV is a rougher approximation. The Schwab data source provides
real-time gamma values from Schwab's own model, which are more reliable.

**Dealer positioning is an assumption.** The standard GEX model
assumes dealers are net short calls and net long puts. This is
generally true in aggregate but not always correct for every strike
or every ticker.

## Roll mode example

```bash
uv run options-scanner/run_scanner.py AMD --roll \
    --type call --strike 600 --expiration 2026-01-16
```

Adds a `NetCr` column showing what you'd receive net after buying
back the existing position. Positive = net credit roll. The table
shows only calls (same type as the position being rolled), ranked
by IV excess so the richest new premium surfaces first.

## Data sources

The tool supports two data sources, selectable via `config.toml`,
the `--data-source` CLI flag, or the title-bar toggle in the web UI:

| Source | Setup | Data quality |
|--------|-------|-------------|
| **Yahoo Finance** (default) | None — works out of the box | Delayed IV, no live Greeks |
| **Schwab** | Free Schwab developer account; see [SCHWAB_DATA_SOURCE.md](SCHWAB_DATA_SOURCE.md) | Real-time quotes, actual Greeks |

When using Schwab, delta comes directly from Schwab's model rather
than being estimated via Black-Scholes from stale IV. Earnings dates
still come from Yahoo Finance — the Schwab API does not provide them.

Before relying on Yahoo Finance output, it's worth understanding
where that data falls short.

### Yahoo Finance limitations

**Stale implied volatility.** Yahoo returns the IV from the last
option trade, not a live market-maker quote. On thinly traded
strikes — common on LEAPS, deep OTM, and low-volume tickers —
that trade may have happened hours or days ago. Stale IV
distorts the surface fit, produces false IV+pp signals, and
makes the Black-Scholes delta unreliable. A lone dot sitting
far from its neighbors with no obvious reason is almost always
a stale quote, not a real signal.

**No live bid/ask.** The bid and ask returned are from the last
market refresh, not a live feed. For actively traded near-term
options this is usually fine; for LEAPS it can be meaningfully
wrong. Always check the live spread on your broker before
placing a trade.

**Greeks not provided.** Yahoo does not return delta, gamma,
theta, or vega. Delta is computed from Black-Scholes using
Yahoo's IV — so if the IV is stale, the delta is too.

**Rate limiting.** Yahoo's unofficial API is unauthenticated and
subject to throttling. Repeated rapid scans can return empty
results. The scanner caches results for 5 minutes to mitigate
this.

**Expiration coverage.** Yahoo may not return the full list of
expirations available at your broker. Some far-dated LEAPS
expirations can be missing entirely.

**Earnings dates.** Yahoo's earnings calendar is sometimes
missing, off by a day, or not yet populated for upcoming
quarters.

### Alternative data sources

These limitations are known, and one or more of the sources
below will likely be added to the tool soon — better data
support is on the roadmap and will be a drop-in improvement
when it lands. In the meantime, the sources below are options
if you want to explore plugging one in yourself:

### Free with a brokerage account

| Source | Notes |
|--------|-------|
| **Schwab** (`schwab-py`) | Real-time, full Greeks, clean REST API. Free for any Schwab account holder. Largest overlap with covered-call sellers. |
| **Tradier** | Very clean REST/JSON API, excellent docs, free developer sandbox with delayed data, real-time with a funded account. Most developer-friendly broker API available. |
| **Tastytrade** | Official API, free for account holders. Options-focused, good Greeks. Popular with the theta-gang crowd. |
| **Interactive Brokers** | Free for account holders via TWS API or newer REST API. Most comprehensive data, but requires their desktop app running as a gateway (`ib_insync` library). |
| **E\*TRADE** | Official OAuth-based API, free for account holders, decent option chain data with Greeks. |

### Free without an account (limited)

| Source | Notes |
|--------|-------|
| **Alpha Vantage** | Free API key, 25 requests/day on free tier. Option chains with some Greeks — workable for single-ticker use, too slow for portfolio scans. |
| **Polygon.io** | Free tier gives 15-minute delayed data; real-time ~$29/mo. Clean API, strong Python SDK, popular in the algo/quant community. |
| **Market Data App** | Free tier with options snapshots. Less well-known but solid. |

### Paid

| Source | Notes |
|--------|-------|
| **Polygon.io** | ~$29/mo for real-time. Most popular among independent developers. |
| **EODHD** | ~$20–50/mo, global coverage, options chains with Greeks. |
| **Intrinio** | Mid-tier pricing, solid quality, good Python SDK. |
| **CBOE LiveVol** | Professional grade, expensive — overkill for this use case. |

### Practical recommendation

**Schwab** (`schwab-py`) is now supported — see
[SCHWAB_DATA_SOURCE.md](SCHWAB_DATA_SOURCE.md) to enable it.
**Tradier** is the easiest next step for a second alternative — the
free developer sandbox lets you test without an active account, and
the REST/JSON responses map cleanly onto how `options_scanner/chain.py` fetches
data.

## Roadmap

Planned improvements, roughly in priority order:

- **Index ticker normalization** — implemented. Yahoo Finance uses
  `^SPX`, `^NDX`, etc.; Schwab uses `$SPX`, `$NDX`, etc. Typing the
  bare name (`SPX`, `NDX`, …) works with both data sources.

- **GEX (Gamma Exposure)** — implemented in the web UI single-ticker
  tab. See the [Gamma Exposure section](#gamma-exposure-gex) above
  for full documentation.

- **IV Rank / IV Percentile (IVR/IVP)** — show how current IV compares
  to its 52-week range. High IVR means premiums are rich relative to
  recent history — the most important context for deciding whether to
  sell options on a given ticker.

- **Expected move** — derive the market-implied move for each
  expiration from the at-the-money straddle price. Useful when picking
  a strike: the expected move is the range the market thinks the stock
  will stay within by expiration.

- **Theta** — add time decay (per day) to the output alongside delta
  and the other Greeks. Options sellers care about how much premium
  they collect each day the position is held.

- **Tradier data source** — the free developer sandbox lets you test
  without a funded account; REST/JSON responses map cleanly onto the
  existing chain fetcher. Easiest next broker integration to add.

- **Interactive Brokers CSV support** — several users have requested
  this. Waiting on an example export file to spec the parser.

- **GEX on portfolio tab** — extend the GEX chart to each position
  in the portfolio scan, not just the single-ticker tab.

- **GEX-aware option ranking** — fold dealer-gamma context into the
  chain output: tag strikes sitting just below a large positive GEX
  wall (pinning resistance — favorable for covered calls), strikes
  inside a negative-GEX amplifying zone (caution for sellers), and
  proximity to the zero-gamma flip level. GEX is most reliable on
  near-term chains where OI is dense, which lines up well with the
  scanner's default DTE range (30–90).

- **IV term structure chart** — plot IV by expiration (rather than by
  strike) to show whether near-term or far-dated vol is elevated.
  Helps identify which expiration has the richest premium environment.

- **Skew chart** — plot IV by strike for a single expiration to
  visualize the put/call skew. Shows how the market is pricing
  downside vs. upside risk at a glance.

- **Watchlist** — save a list of tickers to scan without uploading a
  brokerage CSV each time. Useful for monitoring a fixed set of
  stocks on a regular basis.

- **Portfolio-level Greeks summary** — aggregate delta, theta, and
  vega across all open positions so you can see total book exposure
  at a glance.

- **Third-party Schwab / Yahoo client libraries** — evaluate whether
  community-maintained CLIs / Python clients (e.g. `schwab-py`,
  `schwabdev`, `yfinance`) are worth adopting in place of the
  hand-rolled HTTP calls and OAuth flow currently in `options_scanner/chain.py`
  and `schwab_auth.py`. Tradeoffs: less code to maintain and easier
  access to endpoints we haven't wired yet (streaming quotes,
  account history) vs. taking on an external dependency that could
  go stale or change shape. Pick one per data source and prototype
  before committing.

## Disclaimer

This software is provided free of charge, as-is, with no warranty
of any kind. There is no guarantee of accuracy, completeness, or
fitness for any particular purpose.

Data is sourced from Yahoo Finance or the Schwab developer API
depending on your configuration. Output quality is limited by what
those sources return. Implied volatility figures can be stale,
especially on thinly traded strikes; bid/ask spreads on LEAPS can
be wide; and data may occasionally be missing or incorrect. Nothing
this tool produces should be taken as a guarantee of any particular
result.

This is not financial advice. Options trading involves substantial
risk of loss and is not appropriate for all investors. Do your own
research before acting on anything this tool surfaces. The authors
are not responsible for any trading losses or other damages arising
from use of this software.

## License

This project is free for personal, non-commercial use under the
[Creative Commons Attribution-NonCommercial 4.0 International
(CC BY-NC 4.0)](https://creativecommons.org/licenses/by-nc/4.0/)
license. Commercial use is not permitted without a separate agreement.
If you're interested in licensing this for commercial purposes, reach
out to driekhof@gmail.com.