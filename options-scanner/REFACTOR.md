# options-scanner — refactor backlog

Planned structural improvements, captured 2026-05-19 after an
end-to-end review of the codebase. Trigger for picking this up:
**after the next PR is merged.**

The repo is in good shape overall — these are growth-pain refactors,
not symptoms of underlying rot. Listed by leverage, highest first.

## 1. Split `run_app.py` into a `tabs/` package

`run_app.py` is ~2,700 lines and carries:

- Six tab functions (`_tab_single`, `_tab_gex`, `_tab_portfolio`,
  `_tab_spreads`, `_tab_directional`, `_tab_neutral`)
- ~10 display helpers (`_show_iv_chart`, `_show_chain_table`,
  `_show_gex_chart`, `_show_scan_results`, `_show_payoff_chart`,
  `_show_spreads_table`, etc.)
- Two computation helpers (`_compute_top_ranks`,
  `_compute_gex_summary`)
- Theme/sidebar setup
- Validation logic
- An inline ~200-line CSS block

It's past the point where the file fits in your head. PR conflicts
on the `st.tabs(...)` registration are a downstream symptom.

**Shape of the refactor:**

```
options-scanner/
  options_scanner/
    tabs/
      __init__.py
      single.py        # _tab_single + tab-local helpers
      gex.py           # _tab_gex + helpers
      portfolio.py
      spreads.py       # the _tab_ wrapper, not the math module
      directional.py
      neutral.py
    display/
      __init__.py
      iv_chart.py
      chain_table.py
      gex_chart.py
      scan_results.py
      payoff_chart.py
    compute/
      __init__.py
      top_ranks.py
      gex_summary.py
  run_app.py           # ~150 lines: theme, sidebar, tab registration,
                       # title-bar pills, st.tabs orchestration
```

Highest leverage by far — every other refactor gets easier afterward.

## 2. Extract inline CSS to a real file ✅ DONE 2026-05-22

CSS now lives in `options_scanner/styles.css`, loaded once at the
top of `run_app.py` via `Path.read_text()`. The dynamic accent
colors flow through CSS custom properties (`--primary`,
`--primary-hover`) instead of f-string interpolation — run_app
injects a 2-line `:root` block per rerun and all the rule
selectors reference `var(--primary)`.

## 3. DRY the chain row-building between Yahoo and Schwab ✅ DONE 2026-05-22

The shared half lives in `chain_common.py` now:

- `safe_float` / `safe_int` (formerly duplicated as `_safe_float` /
  `_safe_int` in both modules; underscores dropped on promotion).
- `build_option_row(...)` — applies the quote-quality filters
  (bid/ask non-zero, mid fallback via (bid+ask)/2 → last, mid > 0,
  iv ≥ 0.01, strike > 0) and assembles the canonical 17-column row.
  Returns None to drop a row.

Each provider keeps its own raw-data parsing (Yahoo iterates
yfinance DataFrames, Schwab iterates the JSON expiration map) and
funnels through `build_option_row` at the end. Greeks stay
provider-specific: chain.py computes BS delta/gamma, schwab_chain.py
takes them from the broker — `build_option_row` is Greek-agnostic.

## 4. Convert `src/` to a proper Python package ✅ DONE 2026-05-22

`src/` is now `options_scanner/` — a real Python package registered
in `pyproject.toml` via hatchling. The `sys.path.insert` shims in
`run_app.py`, `run_scanner.py`, `run_portfolio.py`, `schwab_auth.py`
and `tests/conftest.py` are gone; all imports use absolute
`options_scanner.X` paths. Along the way: dropped the latent
`display.py` / `display/` package collision by folding the CLI
results-printer into `display/cli.py`.

## 5. Magic numbers in CSS layout → named constants ✅ DONE 2026-05-22

Layout magic numbers are now CSS custom properties at the top of
`styles.css`: `--pill-top`, `--wordmark-top`, `--sidebar-shift`,
`--wordmark-left`, `--rescan-pill-left`,
`--data-source-pill-left`, `--z-pill`, `--z-wordmark`. The
sidebar-open variants use `calc(var(--pill-left) +
var(--sidebar-shift))` so the three pills track each other through
a single offset value. If a Streamlit version bump changes the
sidebar's open width, only `--sidebar-shift` needs touching.

The `--logo-width: 12rem` REFACTOR.md item disappeared on its own
when the raster logo was replaced by the typographic wordmark.

---

## Things worth leaving alone

- **Session-state key conventions (`s_*`, `g_*`)** — consistent and
  works; abstracting doesn't pay.
- **`_rescan_trigger` flag pattern** — repeats across tabs but each
  instance is small. Abstracting would obscure more than it saves.
- **Inline `from chain import fetch_chain` inside helpers** —
  unconventional but harmless and saves cold-start latency.
- **Workspace layout (`shared/`, per-tool subdirs, gitignored
  `input/`)** — this is good.

## Test coverage

Separately tracked: see the project test backlog memory for areas
worth adding tests to as code is touched (spreads.py, GEX helpers,
`_compute_top_ranks`, `normalize_ticker`).
