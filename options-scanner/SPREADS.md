# Multi-leg spread tabs

The options scanner has three spread-focused tabs alongside the single-leg
**Single Ticker** and CSV-based **Portfolio** tabs:

| Tab | Purpose | Default strategies |
|-----|---------|--------------------|
| **Spreads** | Power-user "all strategies" view | Bull Put, Bear Call, Iron Condor |
| **Directional** | Bullish/bearish only | Bull Put, Bear Call |
| **Neutral** | Range-bound / delta-neutral + \|Δ\| slider | Iron Condor, Calendar, Long Strangle |

All three tabs share the same underlying engine (`scan_spreads` in
`options-scanner/options_scanner/spreads.py`). They differ only in which strategies
are exposed, the default filter values, and whether the delta-neutral
slider is shown.

## Strategy catalog (13 strategies)

| Strategy | Bias | Credit/Debit | Defining feature |
|----------|------|--------------|------------------|
| Bull Put Spread | bullish | credit | short higher put + long lower put |
| Bear Call Spread | bearish | credit | short lower call + long higher call |
| Bull Call Spread | bullish | debit | long lower call + short higher call |
| Bear Put Spread | bearish | debit | long higher put + short lower put |
| Jade Lizard | bullish | credit | short OTM put + short call spread (no upside risk when credit > call width) |
| Risk Reversal | bullish | mixed | long OTM call + short OTM put (synthetic long) |
| Iron Condor | neutral | credit | short put spread + short call spread, no overlap |
| Iron Butterfly | neutral | credit | short ATM straddle + symmetric wings |
| Broken-Wing Butterfly | neutral | credit | Iron Butterfly variant when no strike sits at spot |
| Calendar / Diagonal | neutral | debit | short front-month + long back-month (same strike) |
| Ratio Spread (1×2) | mixed | mixed | long 1 + short 2 at a higher/lower strike |
| Long Straddle | neutral | debit | long call + long put at same strike (long volatility) |
| Long Strangle | neutral | debit | long OTM call + long OTM put (cheaper long volatility) |

## Column reference

| Column | Meaning |
|--------|---------|
| Expiration | Option expiration date (`"YYYY-MM-DD"`; calendars show `front→back`) |
| DTE | Days to expiration of the front leg |
| Short $ / Long $ | Strike of the leg you're short / long. For condors and butterflies a second pair is shown for the call side |
| Credit/Debit | Net premium received (+) or paid (−) per share |
| Max Profit | Maximum gain per share at the best-case outcome |
| Max Loss | Maximum loss per share (capped at 5× width for Ratio spreads; 3× max loss for unbounded-upside strategies) |
| R/R | Risk-reward ratio: Max Profit ÷ Max Loss — higher is better |
| POP% | Probability of Profit at expiration (Black-Scholes N(d₂) based) |
| EV | Expected Value: `POP × Max Profit − (1 − POP) × Max Loss`. Positive EV = statistically favorable |
| Ann% | Annualized return on capital at risk if the spread hits max profit |
| BE Move% | How far spot must move to breach the lower breakeven |
| Δ | Net delta — directional bias. Near 0 = delta-neutral |
| θ | Net daily theta — premium earned (+) or paid (−) per calendar day |
| ν | Net vega — P&L per 1-point IV move. Positive = long volatility |
| IV+pp | Short-leg IV minus the fitted volatility surface (pp). Positive = rich premium |
| Earn | ⚠ = an earnings event falls before expiration |

## Row highlights

| Color | Meaning |
|-------|---------|
| Green border ⭐ | Positive θ **and** positive ν — earns time decay and benefits from rising IV (common in calendars) |
| Green fill | POP ≥ 65% and R/R ≥ 0.20 — high-probability with reasonable reward |
| Yellow fill | POP ≥ 55% and R/R ≥ 0.10 — moderate probability |
| Orange Earn cell | Earnings event before expiration — IV may spike unpredictably |

## How POP is calculated

For single-leg short positions and credit spreads, POP uses the risk-neutral
probability of finishing on the profitable side at expiration:

```
P(S_T > K) = N(d2)
where d2 = (ln(S/K) + (r − σ²/2) · T) / (σ · √T)
r = 0.045  (risk-free rate, matches chain.py)
```

For range-bound spreads (iron condors, butterflies, strangles), POP is the
difference of two `prob_above` calls — the probability the underlying lands
between the two breakevens at expiration.

For calendar spreads POP is an approximation: it assumes IV stays constant
and uses the back-month IV with time-to-front-expiration. Real P&L depends
heavily on IV behaviour in the back month.

## Width units: $ vs % of spot

The Width controls accept either dollar widths (e.g. $5–$25, good for
sub-$500 underlyings) or **% of spot** (e.g. 0.5%–2%, better for high-priced
underlyings like SPX/NDX where strikes are often $25 apart and a $5 width
matches nothing).

Toggle "Width units" above the DTE row. The default range adjusts
automatically.

## Delta-neutral filter (Neutral tab)

The Neutral tab adds a **Max |Δ|** slider (0.05 – 1.00, default 0.15). The
filter is applied to the spread's *net* delta, not any individual leg's:

- `|Δ| ≤ 0.15` → minimal directional bias, good for income strategies
- `|Δ| ≤ 0.05` → very tight, fewer candidates
- `|Δ| = 1.00` → no filter (matches the Spreads tab)

The Neutral tab defaults to longer DTE (30 – 180) and a lower Min POP (55%)
because delta-neutral income strategies tend to use further-out expirations.

## Caveats

- **Calendar / Diagonal**: profit estimate assumes constant IV. Actual P&L
  depends on IV behaviour in the back month. Real-world calendars often
  profit from IV expansion that the model doesn't capture.
- **Ratio Spread (1×2)**: max loss is theoretically unlimited above the
  upper breakeven (call ratios) or below the lower breakeven (put ratios).
  The displayed `max_loss` is capped at 5× spread width for ranking
  purposes only.
- **Risk Reversal**: max loss assumes put assignment with
  `capital-at-risk = put strike − net credit`. Upside is unbounded — the
  displayed `max_profit` is capped at 3× max loss for ranking.
- **Long Straddle / Long Strangle**: upside is unbounded. `max_profit` is
  capped at 3× debit for ranking.
- **Broken-Wing Butterfly**: when no strike sits at spot exactly, the
  builder picks the nearest call and put strikes independently, producing
  an asymmetric butterfly. Rows are labeled `Broken-Wing Butterfly` so this
  is visible.
- **Yahoo IV staleness on LEAPS**: gamma exposure and POPs on far-dated
  options may be misleading when Yahoo's IV hasn't refreshed.

## Tips & example workflows

**Income hunting on indices (SPX, NDX):**
- Use the Neutral tab
- Toggle Width units to "% of spot", set range 0.5%–2%
- Min DTE 45, Max DTE 180
- Max |Δ| 0.15
- Sort by Expected Value

**Theta + vega sweet spot:**
- Neutral tab → select "Calendar / Diagonal" only
- Check the green-bordered rows — those have positive θ *and* positive ν

**Directional play on earnings:**
- Directional tab
- Enable "hide earnings" if you want to avoid IV crush
- Or specifically hunt for earnings exposure by leaving it on

**Far-dated LEAPS calendars:**
- Neutral tab, Max DTE 365+
- The calendar builder now allows back-month up to `front + max(max_dte, 365)`
  days out

## Running the app

```bash
uv run streamlit run options-scanner/run_app.py
```

Tests:
```bash
uv run pytest options-scanner/tests/ -v
```
