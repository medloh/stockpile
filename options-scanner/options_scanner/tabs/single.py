"""Single-ticker tab: the scanner's primary workflow.

Fetches an option chain for one ticker, fits the IV surface, and
ranks contracts by IV excess. Supports two flows:

- **Find new options** — sell IV-rich or buy IV-cheap candidates,
  filtered by DTE, OI, volume, and a delta-range slider.
- **Roll an existing position** — same scan plus a NetCr column
  showing the credit/debit from rolling out of a user-supplied
  strike + expiration.

Results panel includes the IV chart, GEX chart, chain table for the
chosen expiration, the full "Top candidates" ranking, a Monte Carlo
trade analyzer, and an HTML report download.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import streamlit as st

from options_scanner.compute.top_ranks import compute_top_ranks
from options_scanner.display.chain_table import show_chain_table
from options_scanner.display.gex_chart import show_gex_chart
from options_scanner.display.iv_chart import show_iv_chart
from options_scanner.display.outlook_card import render_outlook_card
from options_scanner.display.scan_results import show_scan_results
from options_scanner.display.spot_meta import (
    fetch_spot_meta,
    spot_help_text,
    spot_value_html,
)
from options_scanner.fetch import fetch_and_enrich
from options_scanner.mc_ui import position_from_chain_row, render_mc_panel
from options_scanner.ui_theme import badge, empty_state, metric_card, section_header


def tab_single() -> None:
    # ── Group 1: Ticker + flow ────────────────────────────────────────────────
    with st.container(border=True):
        tc, fc = st.columns([1, 6])
        with tc:
            ticker = st.text_input("Ticker", "AAPL", key="s_ticker")
        with fc:
            flow = st.radio(
                "What do you want to do?",
                ["Find new options", "Roll an existing position"],
                horizontal=True,
                key="s_flow",
            )
    rolling = (flow == "Roll an existing position")

    # Defaults so the same scan code path handles both flows
    buy            = False
    option_type    = "Calls"
    roll_type_sel  = "call"
    roll_strike    = 0.0
    roll_exp       = date.today()

    # ── Group 2: Action-specific controls ─────────────────────────────────────
    with st.container(border=True):
        if rolling:
            rc1, rc2, rc3, _ = st.columns([1, 1, 1.2, 3])
            with rc1:
                roll_type_sel = st.selectbox("Position type", ["call", "put"],
                                             key="s_roll_type")
            with rc2:
                roll_strike = st.number_input("Current strike", value=0.0,
                                              min_value=0.0, step=1.0,
                                              key="s_roll_strike")
            with rc3:
                roll_exp = st.date_input("Current expiration", key="s_roll_exp")
        else:
            a1, a2, a3 = st.columns([2.2, 1.8, 3.0])
            with a1:
                action = st.radio(
                    "Direction",
                    ["Sell (IV-rich candidates)", "Buy (IV-cheap candidates)"],
                    horizontal=True,
                    key="s_action",
                )
                buy = action.startswith("Buy")
            with a2:
                option_type = st.radio("Option Type",
                                       ["Calls", "Puts", "Both"],
                                       horizontal=True, key="s_opt_type")
            with a3:
                render_outlook_card(buy, option_type)

    # ── Group 3: Filters ──────────────────────────────────────────────────────
    with st.container(border=True):
        n1, n2, n3, n4, n5 = st.columns(
            [1, 1, 1, 1, 5], vertical_alignment="bottom",
        )
        with n1:
            min_dte = st.number_input("Min DTE", value=30, min_value=1,
                                      key="s_min_dte")
        with n2:
            max_dte_inp = st.number_input("Max DTE", value=90, min_value=0,
                                          help="0 = no limit; otherwise ≥ Min DTE",
                                          key="s_max_dte")
        with n3:
            min_oi = st.number_input("Min OI", value=25, min_value=0,
                                     key="s_min_oi")
        with n4:
            min_vol = st.number_input(
                "Min Vol", value=10, min_value=0,
                key="s_min_vol",
            )
        with n5:
            st.markdown(
                "<div style='padding:0 0 0.4rem 1rem;'>"
                + badge("MARKET HOURS RECOMMENDED", "warn")
                + "<p style='color:#475569; font-size:0.78rem; "
                "margin:0.45rem 0 0 0; line-height:1.4;'>"
                "Pre/post-market quotes may be stale or missing — IV+pp "
                "rankings depend on fresh data.</p></div>",
                unsafe_allow_html=True,
            )

    # ── Slider + Top N + Scan row ─────────────────────────────────────────────
    # All three controls sit on one row. Layout (T=9):
    #   Delta=2   → covers Min DTE + Max DTE width above
    #   Top N=1   → aligns with Min OI (with CSS padding-left tweak)
    #   spacer=1.10
    #   Scan=1    → left-aligned with the orange warning text column
    #               above (which starts after Min DTE/Max DTE/Min OI/Min
    #               Vol, i.e. at 4 col-units + 4 gaps from the row's left
    #               edge). 1 + G/col_unit ≈ 1.10 makes Scan's left edge
    #               match exactly (assumes ~16px gap).
    #   spacer=3.90
    s1, s2, _, s3, _ = st.columns(
        [2, 1, 1.10, 1, 3.90], vertical_alignment="bottom",
    )
    with s1:
        delta_range = st.slider("Delta Range (abs value)", 0.0, 1.0,
                                (0.10, 0.75), step=0.05, key="s_delta")
    with s2:
        with st.container(key="top_n_align"):
            top_n = st.number_input("Top N", value=10, min_value=1,
                                    max_value=50, key="s_top")
    with s3:
        # Wrapped so CSS can lift the button a few pixels above the row's
        # bottom baseline (it otherwise sits flush with the bottom of the
        # Top N input, which reads as too low against the input's label).
        with st.container(key="scan_btn_lift"):
            scanned = st.button("Scan", type="primary",
                                use_container_width=True, key="s_scan_btn")

    # ── Run scan on button click, store in session state ──────────────────────
    # Also triggers when the sticky "Rescan" pill below the results was
    # clicked on the previous run — it sets `_rescan_trigger` and calls
    # st.rerun() so this top-of-script handler picks it up.
    if scanned or st.session_state.pop("_rescan_trigger", False):
        ticker_clean = ticker.strip().upper()
        if not ticker_clean:
            st.error("Enter a ticker symbol.")
            st.session_state.pop("single_results", None)
            return

        if 0 < int(max_dte_inp) < int(min_dte):
            st.error(
                f"Max DTE ({int(max_dte_inp)}) must be ≥ Min DTE "
                f"({int(min_dte)}), or 0 for no limit."
            )
            st.session_state.pop("single_results", None)
            return

        if rolling:
            eff_opt_fetch = roll_type_sel + "s"   # "calls" or "puts"
            eff_mode      = roll_type_sel          # "call"  or "put"
        else:
            opt_map  = {"Calls": "calls", "Puts": "puts", "Both": "both"}
            mode_map = {"Calls": "call",  "Puts": "put",  "Both": "both"}
            eff_opt_fetch = opt_map[option_type]
            eff_mode      = mode_map[option_type]

        max_dte_arg = int(max_dte_inp) if max_dte_inp > 0 else None
        delta_min, delta_max = delta_range

        with st.spinner(f"Fetching {ticker_clean} option chain…"):
            df, earnings_dates, err = fetch_and_enrich(
                ticker_clean, eff_opt_fetch, int(min_dte), max_dte_arg,
                st.session_state.get("data_source", "yahoo"),
                st.session_state.get("schwab_config"),
            )

        if err:
            st.error(err)
            st.session_state.pop("single_results", None)
            return
        if df.empty:
            st.warning(f"No options found for {ticker_clean} with the given DTE range.")
            st.session_state.pop("single_results", None)
            return

        # Roll: look up close cost for the existing position
        roll_close_cost = None
        if rolling and roll_strike > 0:
            exp_yf = roll_exp.strftime("%Y-%m-%d")
            _provider = st.session_state.get("data_source", "yahoo")
            _scfg = st.session_state.get("schwab_config")
            with st.spinner("Looking up close cost…"):
                if _provider == "schwab":
                    from stocks_shared.schwab_live import (
                        get_client, fetch_option_chain_schwab
                    )
                    try:
                        _sclient = get_client(
                            _scfg["app_key"], _scfg["app_secret"],
                            _scfg["callback_url"], _scfg["token_file"],
                        )
                        chain = fetch_option_chain_schwab(
                            _sclient, ticker_clean, exp_yf
                        )
                    except ValueError as exc:
                        st.warning(f"Schwab roll lookup failed: {exc}")
                        chain = None
                else:
                    from stocks_shared.yahoo import fetch_option_chain
                    chain = fetch_option_chain(ticker_clean, exp_yf)
            if chain is not None:
                side_df = chain.calls if roll_type_sel == "call" else chain.puts
                row = side_df[side_df["strike"] == float(roll_strike)]
                if not row.empty:
                    bid  = float(row["bid"].iloc[0] or 0)
                    ask  = float(row["ask"].iloc[0] or 0)
                    last = float(row["lastPrice"].iloc[0] or 0)
                    roll_close_cost = (bid + ask) / 2 if bid > 0 and ask > 0 else last
                else:
                    st.warning("Position not found in chain — NetCr column omitted.")
            else:
                st.warning(f"Could not fetch chain for {exp_yf} — NetCr column omitted.")

        st.session_state["scan_ts"] = datetime.now().astimezone()
        st.session_state["scan_provider"] = st.session_state.get(
            "data_source", "yahoo"
        )
        st.session_state["single_results"] = {
            "ticker": ticker_clean,
            "df": df,
            "earnings_dates": earnings_dates,
            "mode": eff_mode,
            "buy": buy,
            "roll_close_cost": roll_close_cost,
            "delta_min": delta_min,
            "delta_max": delta_max,
            "min_oi": int(min_oi),
            "min_vol": int(min_vol),
            "top_n": int(top_n),
            "roll_exp_str": roll_exp.strftime("%Y-%m-%d") if rolling else None,
            "roll_strike": roll_strike if rolling else None,
            "roll_type": roll_type_sel if rolling else None,
        }

    # ── Display results (persists across re-runs until next scan) ─────────────
    res = st.session_state.get("single_results")
    if not res:
        return

    ticker_r  = res["ticker"]
    df_r      = res["df"]
    mode_r    = res["mode"]
    buy_r     = res["buy"]
    rcc       = res["roll_close_cost"]
    df_filt   = df_r[df_r["delta"].abs().between(
                    res["delta_min"], res["delta_max"])].copy()
    spot      = float(df_r["spot"].iloc[0])

    st.markdown(
        "<div style='margin-top:0.4rem;'></div>", unsafe_allow_html=True,
    )
    section_header(
        title=f"{ticker_r} — scan results",
        subtitle="Spot, available expirations, and the next earnings event.",
        eyebrow="SUMMARY",
    )
    m1, m2, m3, m4 = st.columns(4)
    ed = res["earnings_dates"]
    if ed:
        earn_days = (ed[0] - date.today()).days
        earn_label = f"{ed[0].strftime('%b %d')}"
        earn_sub   = f"in {earn_days}d"
    else:
        earn_label = "—"
        earn_sub   = "no upcoming events"
    n_contracts = int(len(df_filt))
    action_lbl = "Find new" if not res["roll_close_cost"] else "Roll"
    direction_lbl = "BUY" if buy_r else "SELL"
    with m1:
        _meta = fetch_spot_meta(
            ticker_r, st.session_state.get("scan_provider", "yahoo"),
        )
        metric_card("SPOT PRICE",
                    spot_value_html(spot, _meta["pct_change"]),
                    help_text=spot_help_text(_meta))
    with m2:
        metric_card("EXPIRATIONS", f"{df_r['expiration'].nunique()}",
                    help_text=f"{n_contracts} contracts after filters")
    with m3:
        metric_card("NEXT EARNINGS", earn_label,
                    delta=earn_sub, delta_sign="neutral")
    with m4:
        metric_card("ACTION", f"{action_lbl}",
                    delta=f"{direction_lbl} · {mode_r.upper()}",
                    delta_sign="neutral")
    st.markdown(
        "<div style='margin:0.85rem 0 0.35rem 0;'></div>",
        unsafe_allow_html=True,
    )

    if rcc is not None:
        st.info(f"Rolling {res['roll_type']} ${res['roll_strike']:.0f} "
                f"{res['roll_exp_str']} — close cost (mid): **${rcc:.2f}**")

    # Floating rescan button — CSS pins it to the top header bar next to
    # the logo so it stays visible at every scroll position. Lets the
    # user re-run the scan (e.g. after flipping the sidebar data source)
    # without scrolling back to the top of the page. The container is
    # rendered here but `position: fixed` (in the global style block)
    # lifts it out of normal flow — so its location in the code doesn't
    # affect the visible layout, only that it's scoped to Single Ticker
    # results.
    with st.container(key="rescan_pill_single"):
        if st.button(f"↻ Rescan {ticker_r}", type="primary",
                     key="s_rescan_btn"):
            st.session_state["_rescan_trigger"] = True
            st.rerun()

    show_iv_chart(df_filt, spot, mode_r, res["min_oi"], res["top_n"],
                   buy_r, ticker=ticker_r, key_prefix="s",
                   min_vol=res.get("min_vol", 0))

    show_gex_chart(df_r, spot,
                    provider=st.session_state.get("scan_provider", "yahoo"),
                    ticker=ticker_r)

    chosen_exp = st.session_state.get("s_chart_exp")
    if chosen_exp:
        df_chain = df_filt[df_filt["expiration"] == chosen_exp].copy()
        exp_lbl  = datetime.strptime(chosen_exp, "%Y-%m-%d").strftime("%b %d '%y")
        exp_date = datetime.strptime(chosen_exp, "%Y-%m-%d").date()
        earn_before = [d for d in res["earnings_dates"]
                       if date.today() < d <= exp_date]
        if earn_before:
            next_earn   = min(earn_before)
            earn_days   = (next_earn - date.today()).days
            earn_lbl    = next_earn.strftime("%b %d")
            chain_title = f"{exp_lbl} — next earnings {earn_lbl} ({earn_days}d)"
        else:
            chain_title = exp_lbl
        st.subheader(chain_title)
        top_ranks = compute_top_ranks(
            df_filt, mode_r, buy_r, res["min_oi"], res["top_n"],
            res.get("min_vol", 0),
        )
        show_chain_table(df_chain, buy_r, mode_r, rcc, res["min_oi"],
                          res.get("min_vol", 0), top_ranks=top_ranks)

    st.subheader("Top candidates — all chains")
    show_scan_results(df_filt, mode_r, buy_r, rcc,
                       res["min_oi"], res["top_n"],
                       res.get("min_vol", 0))

    # ── Monte Carlo trade analyzer ────────────────────────────────────────
    # Pick any candidate from the ranked table above and simulate its
    # full P&L distribution. Engine: 10k GBM paths with optional
    # earnings jumps. Pure NumPy — sub-second for typical 30-90 DTE.
    section_header(
        "Monte Carlo Trade Analyzer",
        eyebrow="DECISION SUPPORT",
        subtitle="Simulate the P&L distribution of any contract above. "
                 "P(profit), expected value, worst-5% CVaR, breakeven move.",
    )
    if df_filt.empty:
        empty_state(
            title="Nothing to analyze",
            subtitle="Run a scan to populate the candidate table, then pick "
                     "a row to simulate.",
        )
    else:
        # Apply the EXACT same filters and ranking the "Top candidates"
        # table uses, so the MC dropdown order matches the table order
        # row-for-row. show_scan_results does:
        #   1. filter to opt_type (or both)
        #   2. require open_interest >= min_oi AND volume >= min_vol
        #   3. sort by iv_excess (asc if buy / desc if sell), OI tie-break
        #   4. head(top_n)
        # Without these filters, the auto-filled top row could be a
        # low-liquidity option the table itself hides.
        if mode_r in ("call", "put"):
            df_mc_base = df_filt[df_filt["type"] == mode_r]
        else:
            df_mc_base = df_filt
        df_mc = (
            df_mc_base[
                (df_mc_base["open_interest"] >= res["min_oi"])
                & (df_mc_base["volume"] >= res.get("min_vol", 0))
            ]
            .sort_values(
                ["iv_excess", "open_interest"],
                ascending=[buy_r, False],
            )
            .head(res["top_n"])
            .reset_index(drop=True)
            .copy()
        )
        # The first row is now exactly rank-1 from
        # "Top candidates — all chains" for the current scan direction.
        df_mc["_label"] = (
            df_mc.apply(lambda r: (
                f"{r.get('type', mode_r).upper()[0]}  "
                f"${r['strike']:>7.2f}  "
                f"exp {pd.to_datetime(r['expiration']).strftime('%b %d %y')}  "
                f"·  mid ${r.get('mid', 0):.2f}"
                f"  ·  IV {r.get('iv', 0) * 100:.0f}%"
                f"  ·  IV+pp {r.get('iv_excess', 0) * 100:+.1f}"
            ), axis=1)
        )
        # Empty after filters → nothing to analyze. Surface the reason
        # explicitly rather than render an empty dropdown.
        if df_mc.empty:
            empty_state(
                title="No candidates pass the table's filters",
                subtitle="Top candidates is empty for this ticker — relax "
                         "Min OI / Min Vol on the scan, or pick a ticker "
                         "with more option-chain liquidity.",
            )
            return
        # Mark the best one so the user knows the default isn't arbitrary.
        best_signal = df_mc.iloc[0]["iv_excess"] * 100
        best_label = df_mc.iloc[0]["_label"]
        arrow = "▼" if buy_r else "▲"
        st.caption(
            f"**Strongest signal (rank-1 from Top candidates):** {arrow} {best_label}  "
            f"(IV+pp {best_signal:+.1f}, pre-selected below)"
        )

        # Side defaults from scan direction (buy=long, sell=short).
        c_pick, c_side, c_qty, c_btn = st.columns([4, 1.2, 0.8, 1])
        with c_pick:
            choice_idx = st.selectbox(
                "Candidate to analyze",
                df_mc.index,
                index=0,  # auto-fill: strongest-signal row from the sort above
                format_func=lambda i: df_mc.at[i, "_label"],
                key="s_mc_choice",
            )
        with c_side:
            side = st.radio(
                "Side", ["long", "short"],
                index=0 if buy_r else 1,
                horizontal=True, key="s_mc_side",
            )
        with c_qty:
            qty = st.number_input("Contracts", value=1, min_value=1,
                                  max_value=100, step=1, key="s_mc_qty")
        with c_btn:
            st.write("")  # vertical-align nudge
            run_mc = st.button("Run MC", type="primary", key="s_mc_run")

        # Persist the trigger across reruns so the panel stays expanded.
        if run_mc:
            st.session_state["s_mc_armed"] = True

        if st.session_state.get("s_mc_armed", False) and choice_idx is not None:
            picked = df_mc.loc[choice_idx]
            opt_type = str(picked.get("type", "call")).lower()
            opt_type = "call" if opt_type.startswith("c") else "put"
            try:
                position = position_from_chain_row(
                    underlying=ticker_r,
                    spot=spot,
                    row={
                        "Strike": picked["strike"],
                        "Expiration": picked["expiration"],
                        "Mid": picked.get("mid", picked.get("ask", 0)),
                        "IV%": picked.get("iv", 0) * 100,
                    },
                    side=side,  # type: ignore[arg-type]
                    opt_type=opt_type,  # type: ignore[arg-type]
                    qty=int(qty),
                    earnings_dates=tuple(ed) if ed else (),
                    risk_free_rate=0.045,
                )
            except Exception as exc:  # noqa: BLE001
                st.error(f"Couldn't build position from this row: {exc}")
            else:
                render_mc_panel(
                    position,
                    key=f"s_mc_panel_{choice_idx}_{side}_{qty}",
                    label=f"{ticker_r} {opt_type.upper()} ${picked['strike']:.0f} "
                          f"exp {pd.to_datetime(picked['expiration']).strftime('%b %d %y')}",
                )

    from options_scanner.report import render_html
    html = render_html(df_filt, ticker_r, spot, ed, mode_r, buy_r, rcc,
                       res["min_oi"], res.get("min_vol", 0))
    action_tag = "buy" if buy_r else "sell"
    type_tag   = mode_r if mode_r != "both" else "both"
    st.download_button(
        "⬇ Download HTML Report",
        data=html.encode("utf-8"),
        file_name=f"{ticker_r}_{type_tag}_{action_tag}_{date.today().strftime('%Y%m%d')}.html",
        mime="text/html",
        key="s_download",
    )

    with st.expander("Column & color key"):
        st.markdown("""
**Columns**

| Column | Meaning |
|--------|---------|
| Strike | Option strike price. |
| Expiration | Expiration date. |
| DTE | Days to expiration. |
| Bid / Ask | Market bid and ask prices. |
| Mid | Midpoint of bid and ask — the price you'd typically target. |
| IV% | Implied volatility, annualized. |
| IV+pp | How many percentage points the option's IV sits *above* the fitted volatility surface for its expiration. Positive = richer premium than peers at similar strike/DTE. |
| Delta | Black-Scholes delta. For calls: probability of expiring in the money (0–1). For puts: same magnitude, negative sign (−1–0). |
| Ann% | Annualized yield on capital at risk — calls vs. spot price, puts vs. strike. |
| OI | Open interest — total outstanding contracts. Higher = more liquid. |
| Vol | Volume — contracts traded today. |
| NetCr | Roll mode only: net credit received if you close the existing position and open this one. |

**Row shading (chain view)**

| Color | Meaning |
|-------|---------|
| Green | IV+pp is meaningfully above average — premium is rich relative to this chain. |
| Red | IV+pp is below average — premium is thin or cheap relative to this chain. |
| Gray | IV+pp is near average or within the ~3 pp noise floor — no strong signal. |

**Cell highlighting**

| Color | Column | Meaning |
|-------|--------|---------|
| Yellow cell | Bid / Ask | Spread exceeds 1.5× the median spread for this table — wider than typical, execution may cost more than expected. |
| Yellow cell | OI | Open interest is below 2× the minimum OI filter — limited liquidity, harder to fill at a good price. |
| Yellow cell | Vol | Fewer than 4 contracts traded today — very thin activity. |
""")
