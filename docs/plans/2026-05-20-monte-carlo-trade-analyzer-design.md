# Monte Carlo Trade Analyzer — Design

**Status:** Draft · **Author:** brainstorm via Claude Code · **Date:** 2026-05-20

A per-trade decision aid for the options-scanner. Click an option row → see the probability your trade makes money, the expected P&L, the worst-case loss, and a chart of where the underlying could end up. Works for single contracts and multi-leg strategies (PMCC, iron condor, etc.).

---

## Goal

When you find an interesting candidate in the scanner (high IV+pp, attractive premium, etc.) the next question is always: **"is this actually a good trade?"** Today you eyeball the strike vs spot, mentally simulate where the stock might go, and guess. This feature replaces the guess with a 10,000-path Monte Carlo simulation showing the full P&L distribution.

## Non-goals

- Not a strategy backtester (no historical replay) — that's a separate idea.
- Not a portfolio-level risk dashboard (no aggregated Greeks across all positions) — separate idea.
- Not a real-time trading interface — read-only analytics.
- No predictions about *direction* — the model is unbiased on drift by default; the user sees a distribution, not a forecast.

## User experience

A new **MC Analyze** button on each row of the existing results tables (Single Ticker, Spreads, Directional, Neutral). Clicking it opens an inline expander below the row.

```
┌─ MC Analysis: OKLO 30C Jan'27 (long, 1 ct) ──────────────┐
│                                                          │
│  Probability of profit          78.4%                    │
│  Expected P&L                   +$612                    │
│  CVaR (worst 5%)                −$1,840                  │
│  Breakeven move from spot      −18.2%                    │
│                                                          │
│  [chart] 10,000 simulated price paths + payoff at expiry │
│  [chart] Histogram of P&L at expiry                      │
│                                                          │
│  Assumptions:  IV 88.6%  ·  rate 4.5%  ·  10k paths      │
│                jumps modeled for earnings Aug 10         │
└──────────────────────────────────────────────────────────┘
```

**Defaults (no knobs needed for the common case):**
- Vol = the option's market IV
- Drift = 0 (risk-neutral)
- 10,000 paths
- Earnings jumps automatically applied if any earnings date falls inside the position's window

**Tweakable (collapsed by default):** vol source (chain IV / 30d historical / custom number), drift, path count (1k for speed, 10k for finality), include/exclude earnings jumps.

For **multi-leg positions** (verticals, PMCC, iron condor, anything in the Spreads / Directional / Neutral tabs) the same panel evaluates the combined position. The path chart shows the underlying; the P&L histogram and metrics reflect the net of all legs.

## Architecture

One new package, three modules. Pure Python — nothing Streamlit-specific in the engine so the same code works from CLI/notebooks if needed later.

```
options-scanner/src/montecarlo/
├── model.py        # price-path generation (GBM, Merton jump)
├── position.py     # Leg + Position dataclasses, payoff evaluation
├── engine.py       # run_simulation(position) → SimulationResult
└── metrics.py      # P(profit), CVaR, breakeven, percentiles
```

**Data flow:**

```
User clicks MC Analyze
    ↓
run_app.py: build Position from the scanner row(s)
    ↓
montecarlo.engine.run_simulation(position)
    ↓
    model.generate_paths()         → (n_paths, n_steps) array of spot prices
    position.evaluate_payoff()     → per-path P&L at horizon
    metrics.summarize()            → POP, EV, CVaR, breakeven
    ↓
SimulationResult → Streamlit renders metrics + Altair charts
```

The engine is a single pure function. Streamlit's `@st.cache_data` memoizes results so re-expanding the same row is instant.

## Simulation model

**Default: Geometric Brownian Motion (GBM) with IV-calibrated vol.**
- Industry-standard, matches the Black-Scholes assumption that backs the rest of the app.
- Daily timesteps. For a 240 DTE LEAP that's 240 steps × 10k paths = 2.4M cells — fast under NumPy vectorization.

**Earnings jumps (default on):** when any earnings date falls inside the position window, on that day each path gets a multiplicative jump `S → S · exp(σ_jump · Z)` where σ_jump is calibrated from the at-the-money straddle's implied move. This handles the binary-event tail that pure GBM misses.

**Why not Heston / stochastic vol:** more parameters, more compute, marginal accuracy gain at retail timescales. Not worth the complexity for v1. Can be added behind a feature flag later.

**Why no drift by default:** risk-neutral pricing. The market already prices in expected drift via IV; adding a custom positive drift biases all results toward the bull case. Users who want a bullish/bearish view can override via the knobs panel.

## Performance & caching

- 10k paths × 240 days × 4-leg payoff evaluation: target **< 1 second** on a laptop.
- All vectorized with NumPy — no Python loops over paths.
- `@st.cache_data` keys on `(underlying, spot, legs_tuple, vol, n_paths, earnings_dates)`. Same position re-expanded = no re-simulation.
- Path sample for plotting capped at 200 (visible) of the 10k (computed).

## Testing

- Unit tests in `options-scanner/tests/test_montecarlo.py`:
  - GBM path generator → check mean/variance match analytical expectations
  - Black-Scholes call price recovered by MC for a long call (within ±1% on 100k paths)
  - PMCC payoff matches hand-calculated values at a few terminal spots
  - Earnings jump applied on the correct date
- No new integration test surface — feature is additive, doesn't touch existing scan logic.

## Phases / milestones

**Phase 1 — Engine + single-leg UI** (smallest shippable):
- `montecarlo/` package with GBM, single-leg payoff, basic metrics
- "MC Analyze" expander on the Single Ticker tab only
- Tests for GBM + Black-Scholes recovery

**Phase 2 — Multi-leg:**
- `Position` accepts arbitrary leg list
- Wire into Spreads / Directional / Neutral tabs
- Tests for vertical / PMCC / iron condor

**Phase 3 — Polish:**
- Earnings jump auto-detection (uses existing earnings.py module)
- Knobs panel (vol source, drift, path count override)
- Histogram + path-chart styling per design system

## Open questions

- **Earnings vol source.** Should `σ_jump` come from (a) the front-month ATM straddle's implied move, (b) historical post-earnings moves on this ticker, or (c) user override? Default to (a) seems cleanest; would (b) be more accurate for tickers with thin options?
- **PMCC P&L horizon.** The long leg may outlive the short. Should the P&L panel report (a) terminal at the *short* leg's expiry (when the trade rolls), (b) terminal at the *long* leg's expiry (the eventual close), or (c) both? My default: (a), since that's when the next decision happens. Worth confirming.
- **Plot library.** The redesigned UI uses Altair. The histogram + path-chart fit Altair fine. Anything that needs a 2D heatmap (e.g. P&L surface vs spot and DTE) would push toward Plotly — defer that to Phase 3 if at all.
- **Mobile.** Is mobile-friendly rendering in scope for this feature, or are we explicitly desktop-only for now?
