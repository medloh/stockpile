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

## 3. DRY the chain row-building between Yahoo and Schwab

`chain.py` and `schwab_chain.py` are ~90% structural duplicates: same
17-column schema, same `_safe_float`/`_safe_int` helpers, same
quote-quality filters, same annualization formula. The 0DTE fix had
to land in both — that pattern will repeat as we add columns or
filters.

Options:

- Extract a `_build_option_row(side, K, bid, ask, mid, iv, oi, vol,
  delta, gamma, dte, spot, exp_str)` helper used by both paths.
- Or centralize the schema as a typed dict / dataclass so adding a
  column touches one place, not two.

The Schwab path also gets its Greeks from the broker (no BS math
needed), so the two flows aren't identical — the shared piece is the
row assembly + filters, not the Greeks computation.

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
