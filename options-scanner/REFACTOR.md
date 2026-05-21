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
  src/
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

## 2. Extract inline CSS to a real file

Currently a triple-quoted blob inside `run_app.py`. Move to
`options-scanner/src/styles.css`, load via:

```python
from pathlib import Path
_CSS = (Path(__file__).parent / "src" / "styles.css").read_text()
st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)
```

Gains: editor syntax highlighting, CSS comments without escaping,
easier diffs, no `f""" ... """` interpolation hazards. Cheap win.

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

## 4. Convert `src/` to a proper Python package

Currently `options-scanner/src/` has no `__init__.py`; the
`tests/conftest.py` does `sys.path.insert(0, src)` to make imports
work. Functional, but:

- IDE auto-imports don't always find these modules
- Type-checkers (mypy, pyright) get confused
- `python -m` invocation breaks

Fix: add `__init__.py` files, register the package in
`options-scanner/pyproject.toml`, drop the `sys.path` shim. ~30-minute
change, pays for itself forever. Best done *after* the `tabs/` split
above so the package structure lands together.

## 5. Magic numbers in CSS layout → named constants

The title-bar pill positioning relies on `left: 18rem`, `left: 30rem`,
`left: 33rem`, `left: 45rem`, `top: 13px`, and a hardcoded `12rem`
favicon-width assumption. A Streamlit version bump could shift any of
these and break the layout silently.

After (2) lands, define these as CSS custom properties at the top of
`styles.css`:

```css
:root {
  --logo-width: 12rem;
  --pill-top: 13px;
  --pill-left-collapsed: 18rem;
  --pill-left-expanded: 33rem;
  ...
}
```

Then individual rules reference `var(--pill-left-collapsed)`. One
source of truth per layout dimension, and the constants are visible
when debugging.

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
