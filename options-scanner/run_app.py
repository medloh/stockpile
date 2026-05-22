"""Streamlit web UI for the options scanner."""

import asyncio
import os
import sys
import tempfile

# Streamlit's internal async handling is incompatible with Windows's default
# ProactorEventLoop on Python 3.12+. Switch to the Selector policy before
# Streamlit starts its own loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import altair as alt
import pandas as pd
import streamlit as st

from ui_theme import (
    PALETTE,
    badge,
    disclaimer_chip,
    empty_state,
    footer as ui_footer,
    inject_theme,
    metric_card,
    register_altair_theme,
    section_header,
    wordmark,
)
from mc_ui import LegSpec, position_from_chain_row, position_from_legs, render_mc_panel
from compute.top_ranks import compute_top_ranks
from compute.gex_summary import compute_gex_summary
from display.scan_stamp import (
    PROVIDER_LABELS,
    PROVIDER_COLORS,
    tz_abbr,
    scan_stamp_text,
    scan_stamp_color,
    stamp_caption,
)
from display.payoff_chart import show_payoff_chart
from display.gex_chart import show_gex_chart
from display.gex_strikes_table import (
    fmt_strike_with_dist,
    show_gex_strikes_of_interest,
)
from display.chain_styling import (
    CELL_WARN,
    BID_HELP,
    OI_HELP,
    VOL_HELP,
    ivpp_help_for,
    wide_spread_mask,
    low_oi_mask,
    low_vol_mask,
)
from display.scan_results import show_df, show_scan_results
from display.iv_chart import show_iv_chart
from display.chain_table import show_chain_table
from display.outlook_card import (
    OUTLOOK_TONE_HEX,
    render_outlook_card,
)

_FAVICON_PATH = Path(__file__).parent / "assets" / "favicon.png"
st.set_page_config(
    page_title="Options Scanner — Stockpile",
    page_icon=str(_FAVICON_PATH) if _FAVICON_PATH.exists() else "•",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Inject the global stylesheet and Altair theme as early as possible so
# every downstream widget renders in the redesigned visual language.
inject_theme()
register_altair_theme()


# ── Legacy theme switcher (kept for backward-compat session_state keys) ─────
# The new design system replaces the old four-way theme picker. We leave a
# no-op so any existing references / saved preferences don't crash.

THEMES: dict[str, None] = {"Default": None}


def _apply_theme(theme_name: str) -> None:  # noqa: ARG001 — preserved for compat
    """Compatibility shim: the new ui_theme.inject_theme() supersedes this."""
    return None


# ── Cached data fetching ─────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _validate_csv(content: bytes, brokerage: str) -> tuple[list, int, str | None]:
    """Validate an uploaded CSV.

    Returns (issues, row_count, parse_error):
    - issues:      list of ValidationIssue (stockpile only; [] for other formats)
    - row_count:   data rows found (stockpile) or positions found (other formats)
    - parse_error: error string if the other-format parse failed, else None
    """
    if brokerage == "stockpile":
        from stocks_shared.validators import validate_stockpile_csv, count_data_rows
        text = content.decode("utf-8-sig")
        return validate_stockpile_csv(text), count_data_rows(text), None

    # For brokerage formats: attempt a parse and report positions found
    import os, tempfile
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        from portfolio import get_portfolio
        positions = get_portfolio(tmp_path, brokerage)
        return [], len(positions), None
    except Exception as exc:
        return [], 0, str(exc)
    finally:
        os.unlink(tmp_path)


def _show_validation(issues: list, row_count: int, parse_error: str | None,
                     brokerage: str) -> bool:
    """Render the validation panel.  Returns True if the file is scan-ready."""
    if parse_error:
        st.error(f"Could not parse CSV: {parse_error}")
        return False

    if brokerage != "stockpile":
        noun = "position" if row_count == 1 else "positions"
        st.success(f"Parsed successfully — {row_count} open {noun} found.")
        return True

    errors   = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]

    if not issues:
        st.success(f"Valid — {row_count} rows, no issues found.")
        return True

    parts = []
    if errors:
        parts.append(f"{len(errors)} error{'s' if len(errors) != 1 else ''}")
    if warnings:
        parts.append(f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''}")
    summary = f"{row_count} rows — {', '.join(parts)}"

    if errors:
        st.error(summary)
    else:
        st.warning(summary)

    with st.expander("Show issues", expanded=bool(errors)):
        import pandas as pd
        df = pd.DataFrame([
            {
                "Row":     str(i.row) if i.row > 0 else "—",
                "Field":   i.field or "—",
                "Level":   i.severity.upper(),
                "Message": i.message,
            }
            for i in issues
        ])

        def _row_style(row):
            color = (
                "background-color: rgba(239,68,68,0.18)"
                if row["Level"] == "ERROR"
                else "background-color: rgba(234,179,8,0.22)"
            )
            return [color] * len(row)

        styled = df.style.apply(_row_style, axis=1)
        st.dataframe(styled, hide_index=True, width="stretch")

    return not errors


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_and_enrich(ticker: str, opt_type: str, min_dte: int,
                      max_dte: int | None, provider: str = "yahoo",
                      schwab_config: dict | None = None):
    from chain import fetch_chain
    from iv_surface import compute_iv_excess
    from earnings import fetch_earnings_dates, annotate_earnings
    try:
        df = fetch_chain(ticker, opt_type=opt_type, min_dte=min_dte,
                         max_dte=max_dte, provider=provider,
                         schwab_config=schwab_config)
    except ValueError as exc:
        return pd.DataFrame(), [], str(exc)
    if df.empty:
        return df, [], None
    df = compute_iv_excess(df)
    earnings = fetch_earnings_dates(ticker)
    df = annotate_earnings(df, earnings)
    return df, earnings, None


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_position(ticker: str, min_dte: int, provider: str = "yahoo",
                    schwab_config: dict | None = None):
    """Cached per-ticker chain fetch for portfolio tab."""
    from chain import fetch_chain
    from iv_surface import compute_iv_excess
    from earnings import fetch_earnings_dates, annotate_earnings
    try:
        df = fetch_chain(ticker, opt_type="calls", min_dte=min_dte,
                         provider=provider, schwab_config=schwab_config)
    except ValueError as exc:
        return pd.DataFrame(), [], str(exc)
    if df.empty:
        return df, [], None
    df = compute_iv_excess(df)
    earnings = fetch_earnings_dates(ticker)
    df = annotate_earnings(df, earnings)
    return df, earnings, None


# ── Display helpers ──────────────────────────────────────────────────────────
# Row-highlight masks (wide_spread / low_oi / low_vol) live in
# display.chain_styling alongside the CELL_WARN constant they trigger,
# the column tooltips, and ivpp_help_for. (The static _IVPP_HELP
# constant was dropped during that move — ivpp_help_for has been the
# sole tooltip source since PR #9.)


# Scan-provenance stamp helpers + provider identity constants moved to
# display.scan_stamp. Imported below alongside the other compute/display
# layer imports.


# ── Spot metadata (day change + last-trade timestamp) ───────────────────────
# Same source the scan used (read from `scan_provider` snapshot, not the live
# data-source toggle). Cached briefly so repeated reruns within a scan session
# don't refetch.

@st.cache_data(ttl=60, show_spinner=False)
def _fetch_spot_meta(ticker: str, data_source: str) -> dict:
    """Fetch day-change % and last-trade timestamp for the spot-price card.

    Returns a dict with keys:
        pct_change:    float % change or None
        last_trade_ts: timezone-aware datetime or None
        source_label: "Yahoo Finance" or "Schwab"
        source_key:   "yahoo" or "schwab"

    Yahoo's fast_info does not reliably expose a last-trade timestamp, so
    Yahoo callers fall back to scan_ts (the fetch time) — handled by the
    caller, not here.
    """
    result = {
        "pct_change":    None,
        "last_trade_ts": None,
        "source_label":  PROVIDER_LABELS.get(data_source, data_source),
        "source_key":    data_source,
    }
    try:
        if data_source == "yahoo":
            import yfinance as yf
            from stocks_shared.yahoo import normalize_ticker
            info = yf.Ticker(normalize_ticker(ticker)).fast_info
            last = info.get("lastPrice") or info.get("regularMarketPrice")
            prev = info.get("previousClose")
            if last and prev and float(prev) > 0:
                result["pct_change"] = (
                    (float(last) - float(prev)) / float(prev) * 100.0
                )
            return result
        cfg = st.session_state.get("schwab_config") or {}
        if not cfg.get("app_key"):
            return result
        from stocks_shared.schwab_live import (
            get_client, normalize_ticker_schwab,
        )
        client = get_client(
            cfg.get("app_key", ""),
            cfg.get("app_secret", ""),
            cfg.get("callback_url", "https://127.0.0.1:8182/"),
            cfg.get("token_file", "~/.config/schwab-token.json"),
        )
        sym = normalize_ticker_schwab(ticker)
        resp = client.get_quote(sym)
        resp.raise_for_status()
        quote = resp.json().get(sym, {}).get("quote", {})
        pct = quote.get("netPercentChange")
        if pct is not None:
            result["pct_change"] = float(pct)
        else:
            last = quote.get("mark") or quote.get("lastPrice")
            prev = quote.get("closePrice")
            if last and prev and float(prev) > 0:
                result["pct_change"] = (
                    (float(last) - float(prev)) / float(prev) * 100.0
                )
        # Schwab tradeTime is epoch milliseconds.
        trade_ms = quote.get("tradeTime")
        if trade_ms:
            from datetime import datetime as _dt
            try:
                result["last_trade_ts"] = (
                    _dt.fromtimestamp(int(trade_ms) / 1000).astimezone()
                )
            except (ValueError, OSError):
                pass
        return result
    except Exception:
        return result


def _spot_value_html(spot: float, pct: float | None) -> str:
    """Return the spot price with an inline colored % change beside it."""
    if pct is None:
        return f"${spot:,.2f}"
    if pct > 0:
        color, arrow = "#16a34a", "▲"
    elif pct < 0:
        color, arrow = "#dc2626", "▼"
    else:
        color, arrow = "#64748b", "●"
    return (
        f"${spot:,.2f}"
        f"<span style='color:{color}; font-size:0.6em; "
        f"font-weight:500; margin-left:0.5em; vertical-align:middle;'>"
        f"{arrow} {abs(pct):.2f}%</span>"
    )


def _spot_help_text(meta: dict) -> str:
    """Source label + last-trade time for the spot-price card's help line."""
    label = meta.get("source_label", "")
    ts = meta.get("last_trade_ts") or st.session_state.get("scan_ts")
    if not ts:
        return label
    today = ts.astimezone().date()
    now_date = st.session_state.get("scan_ts")
    now_date = now_date.astimezone().date() if now_date else today
    time_part = ts.strftime("%I:%M %p").lstrip("0")
    tz = tz_abbr(ts)
    if ts.date() == now_date:
        when = f"{time_part} {tz}".rstrip()
    else:
        when = f"{ts.strftime('%b')} {ts.day}, {time_part} {tz}".rstrip()
    prefix = "trade" if meta.get("source_key") == "schwab" else "fetched"
    return f"{label} · {prefix} {when}"


# ── Tab: Single Ticker ───────────────────────────────────────────────────────


def _tab_single() -> None:
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
            df, earnings_dates, err = _fetch_and_enrich(
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
        _meta = _fetch_spot_meta(
            ticker_r, st.session_state.get("scan_provider", "yahoo"),
        )
        metric_card("SPOT PRICE",
                    _spot_value_html(spot, _meta["pct_change"]),
                    help_text=_spot_help_text(_meta))
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

    from report import render_html
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


# ── Tab: GEX ─────────────────────────────────────────────────────────────────

def _tab_gex() -> None:
    """GEX-only scanner: fetch near-term chains (0–60 DTE) for one or
    more tickers and surface dealer-gamma context (walls, amp zones,
    zero-gamma flip).

    Multi-ticker mode shows a summary table ranked by |Total GEX|;
    the user picks one ticker to drill into a full GEX chart and
    strikes-of-interest table.

    Diagnostic output, not a trade signal — see README's Gamma Exposure
    section for caveats.
    """
    with st.container(border=True):
        tc, sc, _ = st.columns([2, 1, 4], vertical_alignment="bottom")
        with tc:
            tickers_input = st.text_input(
                "Ticker(s) — comma-separated",
                "SPY",
                key="g_ticker",
                help=(
                    "One or more tickers, e.g. `SPY, QQQ, NVDA, AAPL`. "
                    "Multi-ticker mode adds a summary table you can "
                    "sort, then drill into one ticker for the full chart."
                ),
            )
        with sc:
            with st.container(key="gex_scan_btn_lift"):
                scanned = st.button("Scan", type="primary",
                                    use_container_width=True,
                                    key="g_scan_btn")

    st.caption(
        "Scans the **0–60 DTE** chain across both calls and puts. "
        "GEX is most reliable on near-term chains where OI is dense; "
        "LEAPS GEX is too thin to interpret and is excluded."
    )

    if scanned or st.session_state.pop("_gex_rescan_trigger", False):
        raw = tickers_input.strip().upper()
        tickers = [t.strip() for t
                   in raw.replace(";", ",").split(",")
                   if t.strip()]
        # Preserve user order, drop duplicates
        seen = set()
        tickers = [t for t in tickers
                   if not (t in seen or seen.add(t))]
        if not tickers:
            st.error("Enter one or more ticker symbols.")
            st.session_state.pop("gex_results", None)
            return

        per_ticker: dict[str, dict] = {}
        failed: list[tuple[str, str]] = []
        progress = st.progress(
            0.0, text=f"Fetching {len(tickers)} ticker(s)…"
        )
        for i, t in enumerate(tickers, 1):
            progress.progress(
                i / len(tickers),
                text=f"Fetching {t} ({i}/{len(tickers)})…",
            )
            df, earnings, err = _fetch_and_enrich(
                t, "both", 0, 60,
                st.session_state.get("data_source", "yahoo"),
                st.session_state.get("schwab_config"),
            )
            if err:
                failed.append((t, err))
                continue
            if df.empty:
                failed.append((t, "no options in 0–60 DTE"))
                continue
            spot = float(df["spot"].iloc[0])
            summary = compute_gex_summary(df, spot)
            if summary is None:
                failed.append((t, "no GEX data (missing gamma/OI)"))
                continue
            per_ticker[t] = {"df": df, "spot": spot,
                             "earnings_dates": earnings, **summary}
        progress.empty()

        for t, msg in failed:
            st.warning(f"**{t}** skipped — {msg}")
        if not per_ticker:
            st.error("No tickers returned GEX data.")
            st.session_state.pop("gex_results", None)
            return

        st.session_state["scan_ts"] = datetime.now().astimezone()
        st.session_state["scan_provider"] = st.session_state.get(
            "data_source", "yahoo"
        )
        st.session_state["gex_results"] = {
            "tickers": list(per_ticker.keys()),
            "per_ticker": per_ticker,
        }

    res = st.session_state.get("gex_results")
    if not res:
        return

    per_ticker = res["per_ticker"]
    if not per_ticker:
        return

    # Build summary df sorted by |Total GEX| descending so the most
    # gamma-exposed ticker is the default drill-down pick.
    rows = []
    for t, info in per_ticker.items():
        spot = info["spot"]
        rows.append({
            "Ticker":    t,
            "Spot":      spot,
            "Total GEX": info["total_gex"],
            "Regime":    info["regime"],
            "Zero-Γ":    fmt_strike_with_dist(info["zero_gamma"], spot),
            "Top Wall":  fmt_strike_with_dist(info["top_wall"], spot),
            "Top Amp":   fmt_strike_with_dist(info["top_amp"], spot),
        })
    summary_df = pd.DataFrame(rows)
    summary_df = (summary_df
                  .assign(_abs=summary_df["Total GEX"].abs())
                  .sort_values("_abs", ascending=False)
                  .drop(columns=["_abs"])
                  .reset_index(drop=True))

    st.divider()

    n = len(per_ticker)
    rescan_label = (f"↻ Rescan {res['tickers'][0]}"
                    if n == 1 else f"↻ Rescan ({n})")
    with st.container(key="rescan_pill_gex"):
        if st.button(rescan_label, type="primary", key="g_rescan_btn"):
            st.session_state["_gex_rescan_trigger"] = True
            st.rerun()

    if n > 1:
        st.subheader("GEX summary")
        st.caption(
            "One row per ticker, sorted by absolute Total GEX (most "
            "dealer-gamma exposure first). The Zero-Γ, Top Wall, and "
            "Top Amp cells include each strike's distance from spot."
        )
        st.dataframe(
            summary_df, hide_index=True, use_container_width=False,
            column_config={
                "Ticker":    st.column_config.TextColumn(),
                "Spot":      st.column_config.NumberColumn(format="$%.2f"),
                "Total GEX": st.column_config.NumberColumn(format="%,.0f"),
                "Regime":    st.column_config.TextColumn(),
                "Zero-Γ":    st.column_config.TextColumn(),
                "Top Wall":  st.column_config.TextColumn(),
                "Top Amp":   st.column_config.TextColumn(),
            },
        )

        drill = st.selectbox(
            "Drill into ticker",
            summary_df["Ticker"].tolist(),
            index=0,
            key="g_drill",
        )
        st.divider()
    else:
        drill = res["tickers"][0]

    info = per_ticker[drill]
    df_r = info["df"]
    spot = info["spot"]

    if n == 1:
        m1, m2, m3 = st.columns(3)
        with m1:
            _meta = _fetch_spot_meta(
                drill, st.session_state.get("scan_provider", "yahoo"),
            )
            metric_card("SPOT",
                        _spot_value_html(spot, _meta["pct_change"]),
                        help_text=_spot_help_text(_meta))
        with m2:
            metric_card("EXPIRATIONS",
                        f"{df_r['expiration'].nunique()}",
                        help_text="0–60 DTE")
        with m3:
            _earnings = info.get("earnings_dates") or []
            if _earnings:
                _earn_days = (_earnings[0] - date.today()).days
                _earn_label = _earnings[0].strftime("%b %d")
                _earn_sub   = f"in {_earn_days}d"
            else:
                _earn_label = "—"
                _earn_sub   = "no upcoming events"
            metric_card("NEXT EARNINGS", _earn_label,
                        delta=_earn_sub, delta_sign="neutral")
        st.divider()

    show_gex_chart(df_r, spot,
                    provider=st.session_state.get("scan_provider", "yahoo"),
                    ticker=drill)

    show_gex_strikes_of_interest(df_r, spot)


# ── Tab: Portfolio ───────────────────────────────────────────────────────────


def _render_portfolio_action_card(
    ticker: str,
    df_filt: pd.DataFrame,
    spot: float,
    shares: int,
    covered: bool,
    roll_close: float | None,
    open_calls: list[dict],
    min_oi: int,
    min_vol: int,
) -> None:
    """Translate the top IV-rich candidate into an explicit buy/sell action.

    The Portfolio table shows raw option data — this card surfaces the
    'so what should I do?' answer with strike, premium, cash flow, and
    breakeven math computed against the user's actual share count.

    Covered (existing short call) → ROLL: buy back the open call, sell
    the new top pick, show net credit/debit + new breakeven.

    Uncovered (just stock) → SELL TO OPEN: write covered calls. Number
    of contracts is auto-sized to the user's share count (shares // 100).
    """
    # Pick the same #1 row the ranked table picks: IV-rich (descending
    # iv_excess) with open_interest tie-break.
    eligible = df_filt[
        (df_filt["type"] == "call")
        & (df_filt["open_interest"] >= min_oi)
        & (df_filt["volume"] >= min_vol)
    ]
    if eligible.empty:
        return
    pick = (
        eligible.sort_values(["iv_excess", "open_interest"],
                             ascending=[False, False])
        .iloc[0]
    )
    strike = float(pick["strike"])
    expiry = pd.to_datetime(pick["expiration"]).strftime("%b %d '%y")
    mid = float(pick["mid"])
    iv_excess_pp = float(pick["iv_excess"]) * 100.0
    delta = float(pick.get("delta", 0.0))
    max_contracts = max(1, shares // 100)

    accent = OUTLOOK_TONE_HEX["pos"]   # green — premium income

    if covered and roll_close is not None and open_calls:
        # ── ROLL action ───────────────────────────────────────────────
        existing = open_calls[0]
        net_cr_per_contract = (mid - roll_close) * 100.0
        net_cr_total = net_cr_per_contract * existing["contracts"]
        sign = "+" if net_cr_per_contract >= 0 else "−"
        action_label = "ROLL existing covered call"
        action_lines = [
            f"<b>1) Buy to close</b> {existing['contracts']}× <code>{existing['symbol']}</code> at mid ~${roll_close:.2f} → pay <b>${roll_close * 100 * existing['contracts']:,.0f}</b>",
            f"<b>2) Sell to open</b> {existing['contracts']}× <code>{ticker} ${strike:.0f}C exp {expiry}</code> at mid ~${mid:.2f} → collect <b>${mid * 100 * existing['contracts']:,.0f}</b>",
            f"<b>Net result:</b> {sign}${abs(net_cr_total):,.0f} ({sign}${abs(net_cr_per_contract):.2f}/contract)",
        ]
        breakeven_line = f"<b>New breakeven (stock):</b> ${strike + (mid - roll_close):.2f} — below this the roll costs you net"
    else:
        # ── SELL TO OPEN (covered call) ───────────────────────────────
        if shares < 100:
            action_label = "Stock position too small for covered call"
            action_lines = [
                f"You hold <b>{shares}</b> shares — a covered call requires at least 100 shares per contract.",
                f"Top IV-rich call for reference: <code>{ticker} ${strike:.0f}C exp {expiry}</code> at mid ~${mid:.2f}",
            ]
            breakeven_line = ""
            accent = OUTLOOK_TONE_HEX["neutral"]   # amber — informational, not actionable
        else:
            premium_per_contract = mid * 100.0
            premium_total = premium_per_contract * max_contracts
            max_profit_per_share = max(0.0, strike - spot) + mid
            max_profit_total = max_profit_per_share * 100 * max_contracts
            assign_prob = abs(delta) * 100.0
            action_label = "SELL TO OPEN covered call"
            action_lines = [
                f"<b>Action:</b> Sell {max_contracts}× <code>{ticker} ${strike:.0f}C exp {expiry}</code> to open at mid ~${mid:.2f}",
                f"<b>Premium collected:</b> ${premium_total:,.0f} ({max_contracts} contract(s) × ${premium_per_contract:,.0f})",
                f"<b>Max profit if assigned at ${strike:.0f}:</b> ${max_profit_total:,.0f} (capped — your stock gets called away)",
                f"<b>Assignment probability:</b> ~{assign_prob:.0f}% (Δ proxy)",
            ]
            breakeven_line = f"<b>Breakeven (stock):</b> ${spot - mid:.2f} — covered down to this price by the premium received"

    lines_html = "".join(f"<li style='margin: 3px 0;'>{l}</li>" for l in action_lines)
    be_html = (f"<div style='margin-top: 6px; font-size: 0.78rem; "
               f"color: var(--osc-ink-3);'>{breakeven_line}</div>"
               if breakeven_line else "")
    st.html(
        f"""
        <div style="
            border-left: 4px solid {accent};
            background: rgba(255,255,255,0.7);
            border-radius: 8px;
            padding: 0.75rem 1rem;
            margin: 0.6rem 0;
            font-family: var(--osc-font), -apple-system, sans-serif;
            line-height: 1.5;
        ">
            <div style="font-size: 0.65rem; font-weight: 700;
                        text-transform: uppercase; letter-spacing: 0.08em;
                        color: var(--osc-ink-4); margin-bottom: 2px;">
                Recommended action · top IV+pp signal ({iv_excess_pp:+.1f} pp)
            </div>
            <div style="font-size: 1rem; font-weight: 700; color: {accent};
                        margin-bottom: 6px;">
                {action_label}
            </div>
            <ul style="margin: 0; padding-left: 1.1rem; font-size: 0.85rem;
                       color: var(--osc-ink-1);">
                {lines_html}
            </ul>
            {be_html}
        </div>
        """
    )


def _tab_portfolio() -> None:
    section_header(
        title="Portfolio scan",
        subtitle=(
            "Upload a brokerage CSV — we'll surface roll candidates and rich "
            "options ticker-by-ticker, with covered-call positions accounted for."
        ),
        eyebrow="STEP 01 · UPLOAD",
    )
    uploaded = st.file_uploader("Brokerage CSV export", type=["csv"])
    st.markdown(
        "<div style='margin: 0.4rem 0 0.7rem 0;'>"
        + badge("PROCESSED LOCALLY · NEVER UPLOADED", "positive")
        + "</div>",
        unsafe_allow_html=True,
    )

    pc1, pc2, pc3, pc4, pc5, pc6 = st.columns([2, 1, 1, 1, 2, 1])
    with pc1:
        brokerage = st.selectbox(
            "Format",
            ["schwab", "robinhood", "fidelity", "merrill", "stockpile"],
            index=None,
            placeholder="Select format…",
            help="Select your brokerage export format, or 'stockpile' for a "
                 "manually-entered transaction log.",
        )
    with pc2:
        port_min_dte = st.number_input("Min DTE", value=30, min_value=1,
                                       key="p_min_dte")
    with pc3:
        port_min_oi = st.number_input("Min OI", value=25, min_value=0,
                                      key="p_min_oi")
    with pc4:
        port_min_vol = st.number_input("Min Vol", value=1, min_value=0,
                                       key="p_min_vol")
    with pc5:
        port_delta_range = st.slider("Delta Range", 0.0, 1.0, (0.10, 0.70),
                                     0.05, key="p_delta")
    with pc6:
        port_top = st.number_input("Top N per ticker", value=5, min_value=1,
                                   key="p_top")

    # Invalidate stored results when the file or format changes so stale
    # data from a previous scan never bleeds through.
    _cache_key = (
        f"{uploaded.name}:{len(uploaded.getvalue())}" if uploaded else None,
        brokerage,
    )
    if st.session_state.get("_portfolio_cache_key") != _cache_key:
        st.session_state.pop("portfolio_results", None)
        st.session_state["_portfolio_cache_key"] = _cache_key

    # ── Validation (auto-runs whenever a file and format are both set) ──────────
    scan_ready = False
    if uploaded is not None and brokerage is not None:
        with st.container(border=True):
            st.caption(
                f"**Validation** — {uploaded.name}"
                + (" (stockpile format)" if brokerage == "stockpile" else "")
            )
            issues, row_count, parse_error = _validate_csv(
                uploaded.getvalue(), brokerage
            )
            scan_ready = _show_validation(
                issues, row_count, parse_error, brokerage
            )

            if brokerage == "stockpile":
                st.caption(
                    "See the README for the full format spec and an example "
                    "row for every transaction type (BUY, SELL, STO, BTO, "
                    "STC, BTC, EXPIRED, ASSIGNED, EXERCISED, DIVIDEND, "
                    "SPLIT, TRANSFER_IN)."
                )

    if st.button("Scan Portfolio", type="primary",
                 disabled=(uploaded is None or brokerage is None
                           or not scan_ready)):
        from portfolio import get_portfolio
        _provider = st.session_state.get("data_source", "yahoo")
        _scfg = st.session_state.get("schwab_config")

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            f.write(uploaded.getvalue())
            tmp_path = f.name

        try:
            positions = get_portfolio(tmp_path, brokerage)
        except Exception as exc:
            st.error(f"Could not parse CSV: {exc}")
            os.unlink(tmp_path)
            st.stop()

        os.unlink(tmp_path)

        if not positions:
            st.warning("No open stock positions found in this CSV.")
            st.stop()

        st.success(f"Found {len(positions)} position(s): "
                   f"{', '.join(p['ticker'] for p in positions)}")

        progress = st.progress(0, text="Scanning…")
        results = []
        for i, pos in enumerate(positions):
            ticker = pos["ticker"]
            progress.progress((i + 1) / len(positions),
                              text=f"Scanning {ticker} ({i+1}/{len(positions)})…")

            df, earnings_dates, err = _fetch_position(
                ticker, int(port_min_dte), _provider, _scfg
            )

            roll_close_costs = {}
            _schwab_client = None
            if _provider == "schwab" and pos["open_calls"]:
                from stocks_shared.schwab_live import get_client
                try:
                    _schwab_client = get_client(
                        _scfg["app_key"], _scfg["app_secret"],
                        _scfg["callback_url"], _scfg["token_file"],
                    )
                except (ValueError, TypeError):
                    pass

            for opt in pos["open_calls"]:
                m, d, y = opt["expiration"].split("/")
                exp_yf = f"{y}-{m}-{d}"
                if _provider == "schwab" and _schwab_client is not None:
                    from stocks_shared.schwab_live import fetch_option_chain_schwab
                    chain = fetch_option_chain_schwab(_schwab_client, ticker, exp_yf)
                else:
                    from stocks_shared.yahoo import fetch_option_chain
                    chain = fetch_option_chain(ticker, exp_yf)
                if chain is not None:
                    row = chain.calls[chain.calls["strike"] == float(opt["strike"])]
                    if not row.empty:
                        bid  = float(row["bid"].iloc[0] or 0)
                        ask  = float(row["ask"].iloc[0] or 0)
                        last = float(row["lastPrice"].iloc[0] or 0)
                        roll_close_costs[opt["symbol"]] = (
                            (bid + ask) / 2 if bid > 0 and ask > 0 else last
                        )

            results.append({
                "position": pos,
                "error": err,
                "df": df,
                "spot": float(df["spot"].iloc[0]) if not df.empty else None,
                "earnings_dates": earnings_dates,
                "roll_close_costs": roll_close_costs,
            })

        progress.empty()
        st.session_state["scan_ts"] = datetime.now().astimezone()
        st.session_state["scan_provider"] = st.session_state.get(
            "data_source", "yahoo"
        )
        st.session_state["portfolio_results"] = {
            "results": results,
            "uploaded_name": uploaded.name,
        }

    # ── Render stored results (survives widget interactions / re-runs) ───────────
    stored = st.session_state.get("portfolio_results")
    if stored is None:
        return

    results       = stored["results"]
    uploaded_name = stored["uploaded_name"]

    for res in results:
        pos    = res["position"]
        ticker = pos["ticker"]
        covered = bool(pos["open_calls"])
        label  = f"{ticker} — {pos['shares']} shares — {'Covered' if covered else 'Uncovered'}"

        with st.expander(label, expanded=True):
            if res["error"]:
                st.error(res["error"])
                continue

            spot           = res["spot"]
            earnings_dates = res["earnings_dates"]
            df             = res["df"]

            if spot is None or df.empty:
                st.warning("No options data returned — Yahoo may be "
                           "throttling. Try again in a moment.")
                continue

            m1, m2, m3, m4 = st.columns(4)
            if earnings_dates:
                earn_days = (earnings_dates[0] - date.today()).days
                earn_label = f"{earnings_dates[0].strftime('%b %d')}"
                earn_sub   = f"in {earn_days}d"
            else:
                earn_label = "—"
                earn_sub   = "no upcoming events"
            with m1:
                _meta = _fetch_spot_meta(
                    ticker, st.session_state.get("scan_provider", "yahoo"),
                )
                metric_card("SPOT",
                            _spot_value_html(spot, _meta["pct_change"]),
                            help_text=_spot_help_text(_meta))
            with m2:
                metric_card("SHARES", f"{pos['shares']:,}",
                            help_text="Covered" if covered else "Uncovered")
            with m3:
                metric_card("EXPIRATIONS", f"{df['expiration'].nunique()}")
            with m4:
                metric_card("NEXT EARNINGS", earn_label,
                            delta=earn_sub, delta_sign="neutral")

            for opt in pos["open_calls"]:
                close = res["roll_close_costs"].get(opt["symbol"])
                close_str = f" — close mid: **${close:.2f}**" if close else ""
                st.info(f"Open call: **{opt['symbol']}** "
                        f"({opt['contracts']} contract(s)){close_str}")

            roll_close = None
            if pos["open_calls"]:
                first = pos["open_calls"][0]
                roll_close = res["roll_close_costs"].get(first["symbol"])

            port_delta_min, port_delta_max = port_delta_range
            df_filt = df[df["delta"].abs().between(
                port_delta_min, port_delta_max)].copy()

            # Explicit action card BEFORE the chart — answers "what should
            # I actually do?" with the rank-1 candidate spelled out in
            # buy-to-close / sell-to-open language.
            _render_portfolio_action_card(
                ticker=ticker,
                df_filt=df_filt,
                spot=spot,
                shares=int(pos["shares"]),
                covered=covered,
                roll_close=roll_close,
                open_calls=pos["open_calls"],
                min_oi=int(port_min_oi),
                min_vol=int(port_min_vol),
            )

            show_iv_chart(df_filt, spot, "call",
                           int(port_min_oi), int(port_top), False,
                           ticker=ticker, key_prefix=f"p_{ticker}",
                           min_vol=int(port_min_vol))

            st.markdown("**Top candidates**")
            show_scan_results(df_filt, "call", False, roll_close,
                               int(port_min_oi), int(port_top),
                               int(port_min_vol))

    # Portfolio HTML download
    from report import render_portfolio_html
    port_html = render_portfolio_html(
        results, uploaded_name, int(port_min_oi), int(port_top),
        int(port_min_vol),
    )
    st.download_button(
        "⬇ Download Portfolio Report",
        data=port_html.encode("utf-8"),
        file_name=f"portfolio_{date.today().strftime('%Y%m%d')}.html",
        mime="text/html",
    )


# ── Tab: Spreads ─────────────────────────────────────────────────────────────

_GREEK_HELP = {
    "Δ": "Net delta — directional exposure. Near 0 = delta-neutral.",
    "θ": "Net daily theta — time decay earned (positive) or paid (negative) per day.",
    "ν": "Net vega — profit/loss per 1-point rise in IV. Positive = benefits from IV expansion.",
}

_PAYOFF_HELP = "Select a row in the table above to plot its payoff diagram."


def _show_spreads_table(sub: pd.DataFrame, strategy_name: str,
                        spot: float, key_prefix: str = "sp") -> int | None:
    """Render the ranked spread table. Returns the selected row index or None."""
    if sub.empty:
        st.info(f"No {strategy_name} spreads found matching the filters.")
        return None

    # Disclaimer captions
    if strategy_name == "Calendar / Diagonal":
        st.caption("⚠ Profit estimate assumes constant IV — actual P&L depends "
                   "on IV changes in the back month.")
    elif strategy_name == "Ratio Spread (1×2)":
        st.caption("⚠ Max loss is capped at 5× spread width for ranking — "
                   "actual loss is theoretically unlimited above the upper breakeven.")

    has_two_sides = strategy_name in ("Iron Condor", "Iron Butterfly")

    disp_rows = []
    for _, r in sub.iterrows():
        row_d = {
            "Expiration": r["expiration"],
            "DTE":        int(r["dte"]),
            "Short $":    f"${r['short_strike']:.0f}",
            "Long $":     f"${r['long_strike']:.0f}",
        }
        if has_two_sides:
            ss2 = r.get("short_strike2")
            ls2 = r.get("long_strike2")
            if ss2 and not pd.isna(ss2):
                row_d["Short $2"] = f"${ss2:.0f}"
                row_d["Long $2"]  = f"${ls2:.0f}"

        credit = float(r["net_credit"])
        row_d["Credit/Debit"] = credit
        row_d["Max Profit"]   = float(r["max_profit"])
        row_d["Max Loss"]     = float(r["max_loss"])
        row_d["R/R"]          = float(r["risk_reward"])
        row_d["POP%"]         = float(r["pop"]) * 100
        row_d["EV"]           = float(r["expected_value"])
        row_d["Ann%"]         = float(r["ann_yield_pct"])
        row_d["BE Move%"]     = float(r["be_move_pct"])
        row_d["Δ"]            = float(r["net_delta"])
        row_d["θ"]            = float(r["net_theta"])
        row_d["ν"]            = float(r["net_vega"])
        row_d["IV+pp"]        = float(r["short_iv_excess"]) * 100
        row_d["Earnings"]     = "⚠" if r.get("earnings_in_window") else ""
        disp_rows.append(row_d)

    disp = pd.DataFrame(disp_rows)

    # Row styling: θ+ν sweet spot → bold green; green fill; yellow fill
    def _row_style(row):
        i = row.name
        orig = sub.iloc[i]
        pt = bool(orig["positive_theta"])
        pv = bool(orig["positive_vega"])
        pop = float(orig["pop"])
        rr = float(orig["risk_reward"])
        if pt and pv:
            bg = "background-color: rgba(34,197,94,0.30); outline: 2px solid #16a34a"
        elif pop >= 0.65 and rr >= 0.20:
            bg = "background-color: rgba(34,197,94,0.18)"
        elif pop >= 0.55 and rr >= 0.10:
            bg = "background-color: rgba(234,179,8,0.22)"
        else:
            bg = ""
        return [bg] * len(row)

    earnings_mask = [bool(sub.iloc[i].get("earnings_in_window", False))
                     for i in range(len(sub))]

    styled = disp.style.apply(_row_style, axis=1)
    if any(earnings_mask) and "Earnings" in disp.columns:
        styled = styled.apply(
            lambda _: ["background-color: rgba(249,115,22,0.35)"
                       if earnings_mask[i] else ""
                       for i in range(len(disp))],
            subset=["Earnings"],
        )

    col_cfg = {
        "DTE":        st.column_config.NumberColumn("DTE", format="%d", width="small"),
        "Credit/Debit": st.column_config.NumberColumn("Credit/Debit", format="$%+.2f"),
        "Max Profit": st.column_config.NumberColumn("Max Profit", format="$%.2f"),
        "Max Loss":   st.column_config.NumberColumn("Max Loss", format="$%.2f"),
        "R/R":        st.column_config.NumberColumn("R/R", format="%.2f",
                                                     help="max_profit / max_loss — higher is better"),
        "POP%":       st.column_config.NumberColumn("POP%", format="%.1f%%",
                                                     help="Probability of profit at expiration"),
        "EV":         st.column_config.NumberColumn("EV", format="$%+.2f",
                                                     help="Expected value = POP×MaxProfit − (1−POP)×MaxLoss"),
        "Ann%":       st.column_config.NumberColumn("Ann%", format="%.1f%%", width="small"),
        "BE Move%":   st.column_config.NumberColumn("BE Move%", format="%.1f%%",
                                                     help="How far spot must move to breach the lower breakeven"),
        "Δ":          st.column_config.NumberColumn("Δ", format="%.2f", width="small",
                                                     help=_GREEK_HELP["Δ"]),
        "θ":          st.column_config.NumberColumn("θ", format="%.4f", width="small",
                                                     help=_GREEK_HELP["θ"]),
        "ν":          st.column_config.NumberColumn("ν", format="%.3f", width="small",
                                                     help=_GREEK_HELP["ν"]),
        "IV+pp":      st.column_config.NumberColumn(
            "IV+pp", format="%+.1f pp", width="small",
            help=(
                "Short-leg IV residual vs the fitted surface. Spreads"
                " here are CREDIT-leaning — positive IV+pp on the short"
                " leg means you're collecting richer-than-fair premium"
                " on the side you sold. Look for +3 pp or higher."
            ),
        ),
        "Earnings":   st.column_config.TextColumn("Earn", width="small",
                                                   help="⚠ = earnings event before expiration"),
    }

    event = st.dataframe(
        styled,
        column_config=col_cfg,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key=f"{key_prefix}_tbl_{strategy_name.replace(' ', '_').replace('/', '_').replace('×', 'x')}",
    )
    stamp_caption()
    selected_rows = event.selection.rows if hasattr(event, "selection") else []
    return selected_rows[0] if selected_rows else None


def _render_spreads_view(
    *,
    key_prefix: str,
    tab_label: str,
    available_strategies: list[str],
    default_strategies: list[str],
    default_min_dte: int,
    default_max_dte: int,
    default_min_pop_pct: int,
    default_sort_by: str,
    session_key: str,
    include_delta_filter: bool = False,
    default_max_abs_delta: float = 1.0,
) -> None:
    """Shared controls + scan + results rendering for all spread tabs."""
    from spreads import scan_spreads

    # ── Controls ──────────────────────────────────────────────────────────────
    with st.container(border=True):
        tc, _ = st.columns([1, 5])
        with tc:
            ticker = st.text_input("Ticker", "AAPL", key=f"{key_prefix}_ticker")

    # Width-mode toggle determines $ vs % defaults dynamically
    width_mode_label = st.radio(
        "Width units", ["$", "% of spot"],
        horizontal=True, key=f"{key_prefix}_width_mode",
    )
    width_mode = "percent" if "%" in width_mode_label else "dollar"
    if width_mode == "percent":
        min_w_default, max_w_default = 0.5, 5.0
        min_w_step, max_w_step = 0.1, 0.5
        min_w_label = "Min Width (%)"
        max_w_label = "Max Width (%)"
    else:
        min_w_default, max_w_default = 5.0, 25.0
        min_w_step, max_w_step = 0.5, 1.0
        min_w_label = "Min Width ($)"
        max_w_label = "Max Width ($)"

    with st.container(border=True):
        d1, d2, w1, w2, oi_col = st.columns([1, 1, 1, 1, 1])
        with d1:
            min_dte = st.number_input("Min DTE", value=default_min_dte,
                                      min_value=1, key=f"{key_prefix}_min_dte")
        with d2:
            max_dte = st.number_input("Max DTE", value=default_max_dte,
                                      min_value=1, key=f"{key_prefix}_max_dte")
        with w1:
            min_width = st.number_input(min_w_label, value=min_w_default,
                                        min_value=0.1, step=min_w_step,
                                        key=f"{key_prefix}_min_width")
        with w2:
            max_width = st.number_input(max_w_label, value=max_w_default,
                                        min_value=0.1, step=max_w_step,
                                        key=f"{key_prefix}_max_width")
        with oi_col:
            min_oi = st.number_input("Min OI (each leg)", value=10,
                                     min_value=0, key=f"{key_prefix}_min_oi")

    with st.container(border=True):
        # Pre-filter the default list to the strategies actually available
        effective_default = [s for s in default_strategies if s in available_strategies]
        selected_strategies = st.multiselect(
            "Strategies to scan",
            options=available_strategies,
            default=effective_default,
            key=f"{key_prefix}_strategies",
        )

    # Delta-neutral slider (Neutral tab only)
    max_abs_delta = 1.0
    if include_delta_filter:
        max_abs_delta = st.slider(
            "Max |Δ| (delta-neutrality)",
            min_value=0.05, max_value=1.00,
            value=default_max_abs_delta, step=0.05,
            key=f"{key_prefix}_max_delta",
            help="Tighter values = more delta-neutral. 0.15 ≈ minimal "
                 "directional bias. 1.00 disables the filter.",
        )

    f1, f2, f3, f4, _, f5 = st.columns([2, 1, 1, 1, 1, 1.2], vertical_alignment="bottom")
    with f1:
        min_pop_pct = st.slider("Min POP %", min_value=40, max_value=90,
                                value=default_min_pop_pct, step=5,
                                key=f"{key_prefix}_min_pop")
    with f2:
        sort_by = st.selectbox("Sort by",
                               ["Risk/Reward", "POP", "Expected Value", "Ann%"],
                               index=["Risk/Reward", "POP", "Expected Value", "Ann%"].index(default_sort_by),
                               key=f"{key_prefix}_sort_by")
    with f3:
        only_pos_theta = st.checkbox("θ > 0 only", key=f"{key_prefix}_pos_theta")
    with f4:
        only_pos_vega = st.checkbox("ν > 0 only", key=f"{key_prefix}_pos_vega")
    with f5:
        scanned = st.button(f"Scan {tab_label}", type="primary",
                            use_container_width=True,
                            key=f"{key_prefix}_scan_btn")

    # ── Scan ──────────────────────────────────────────────────────────────────
    # Also fires when the floating rescan button below was clicked on the
    # previous run (it sets `_{key_prefix}_rescan_trigger` and calls
    # st.rerun()).
    rescan_flag = f"_{key_prefix}_rescan_trigger"
    if scanned or st.session_state.pop(rescan_flag, False):
        ticker_clean = ticker.strip().upper()
        if not ticker_clean:
            st.error("Enter a ticker symbol.")
            st.session_state.pop(session_key, None)
            return
        if not selected_strategies:
            st.error("Select at least one strategy.")
            return

        if int(max_dte) < int(min_dte):
            st.error(
                f"Max DTE ({int(max_dte)}) must be ≥ Min DTE "
                f"({int(min_dte)})."
            )
            st.session_state.pop(session_key, None)
            return

        with st.spinner(f"Fetching {ticker_clean} option chain…"):
            df, earnings_dates, err = _fetch_and_enrich(
                ticker_clean, "both", int(min_dte), int(max_dte),
                st.session_state.get("data_source", "yahoo"),
                st.session_state.get("schwab_config"),
            )

        if err:
            st.error(err)
            st.session_state.pop(session_key, None)
            return
        if df.empty:
            st.warning(f"No options found for {ticker_clean}.")
            st.session_state.pop(session_key, None)
            return

        with st.spinner("Building spreads…"):
            results_df, errors = scan_spreads(
                df,
                strategies=selected_strategies,
                min_dte=int(min_dte),
                max_dte=int(max_dte),
                min_width=float(min_width),
                max_width=float(max_width),
                min_oi=int(min_oi),
                min_pop=min_pop_pct / 100.0,
                sort_by=sort_by,
                only_positive_theta=only_pos_theta,
                only_positive_vega=only_pos_vega,
                earnings_dates=earnings_dates,
                max_abs_delta=max_abs_delta,
                width_mode=width_mode,
            )

        st.session_state["scan_ts"] = datetime.now().astimezone()
        st.session_state["scan_provider"] = st.session_state.get(
            "data_source", "yahoo"
        )
        st.session_state[session_key] = {
            "ticker": ticker_clean,
            "spot": float(df["spot"].iloc[0]),
            "earnings_dates": earnings_dates,
            "df": results_df,
            "errors": errors,
            "selected_strategies": selected_strategies,
            "min_pop_pct": min_pop_pct,
            "max_abs_delta": max_abs_delta,
        }

    # ── Display ───────────────────────────────────────────────────────────────
    res = st.session_state.get(session_key)
    if not res:
        return

    for err in res.get("errors", []):
        st.warning(f"Builder failed — {err}")

    spot = res["spot"]
    df_r = res["df"]
    ticker_r = res["ticker"]

    # Floating rescan button — same fixed-position treatment as the
    # Single Ticker tab. The shared `[class*="st-key-rescan_pill"]` CSS
    # block in the global style section pins this to the header bar.
    with st.container(key=f"rescan_pill_{key_prefix}"):
        if st.button(f"↻ Rescan {ticker_r}", type="primary",
                     key=f"{key_prefix}_rescan_btn"):
            st.session_state[rescan_flag] = True
            st.rerun()

    section_header(
        title=f"{ticker_r} — spread candidates",
        subtitle="Ranked by your chosen criterion, filtered by POP and width.",
        eyebrow="RESULTS",
    )
    m1, m2, m3 = st.columns(3)
    ed = res["earnings_dates"]
    if ed:
        earn_days = (ed[0] - date.today()).days
        earn_label = f"{ed[0].strftime('%b %d')}"
        earn_sub   = f"in {earn_days}d"
    else:
        earn_label = "—"
        earn_sub   = "no upcoming events"
    with m1:
        _meta = _fetch_spot_meta(
            ticker_r, st.session_state.get("scan_provider", "yahoo"),
        )
        metric_card("SPOT PRICE",
                    _spot_value_html(spot, _meta["pct_change"]),
                    help_text=_spot_help_text(_meta))
    with m2:
        metric_card("SPREADS FOUND", f"{len(df_r):,}",
                    help_text="After all filters & sorting")
    with m3:
        metric_card("NEXT EARNINGS", earn_label,
                    delta=earn_sub, delta_sign="neutral")
    st.markdown(
        "<div style='margin:0.85rem 0 0.35rem 0;'></div>",
        unsafe_allow_html=True,
    )

    if df_r.empty:
        delta_hint = (f", |Δ| ≤ {res['max_abs_delta']:.2f}"
                      if include_delta_filter else "")
        st.info(f"No spreads met the filters (POP ≥ {res['min_pop_pct']}%"
                f"{delta_hint}). Try widening the spread width, lowering "
                "Min POP, or selecting more strategies.")
        return

    for strategy_name in res["selected_strategies"]:
        sub = df_r[df_r["strategy"] == strategy_name].reset_index(drop=True)
        n = len(sub)
        has_theta_vega = (sub["positive_theta"] & sub["positive_vega"]).any() if not sub.empty else False
        label = f"{strategy_name} — {n} spread(s)"
        if has_theta_vega:
            label += "  ⭐ θ+ν"

        with st.expander(label, expanded=True):
            if has_theta_vega:
                st.caption("⭐ **Green-bordered rows** = positive theta AND vega — "
                           "earns time decay and benefits from rising IV.")
            if strategy_name == "Risk Reversal":
                st.caption("⚠ Max loss assumes put assignment "
                           "(capital-at-risk = put strike − net credit). "
                           "Theoretical upside is unbounded; max profit is "
                           "capped at 3× max loss for ranking.")
            if strategy_name in ("Long Straddle", "Long Strangle"):
                st.caption("ℹ Max profit is capped at 3× debit for ranking — "
                           "actual upside is unbounded.")
            selected_idx = _show_spreads_table(sub, strategy_name, spot,
                                                key_prefix=key_prefix)

            if selected_idx is not None and selected_idx < len(sub):
                row = sub.iloc[selected_idx]
                st.markdown("**Payoff diagram**")
                show_payoff_chart(row, spot)

                # ── Monte Carlo for the selected multi-leg strategy ─────────
                from spreads import build_legs_from_row
                raw_legs = build_legs_from_row(row)
                if raw_legs:
                    try:
                        exp = pd.to_datetime(row["expiration"]).date()
                        legs_spec = [
                            LegSpec(
                                opt_type=lg["type"],
                                strike=float(lg["strike"]),
                                expiration=exp,
                                side="long" if int(lg["qty"]) > 0 else "short",
                                mid=float(lg.get("entry_mid", 0.0)),
                                iv=float(lg["iv"]) if lg.get("iv") else None,
                                qty=abs(int(lg["qty"])),
                            )
                            for lg in raw_legs
                        ]
                        spread_position = position_from_legs(
                            underlying=ticker,
                            spot=spot,
                            legs_spec=legs_spec,
                            earnings_dates=(),
                            risk_free_rate=0.045,
                        )
                        st.markdown("**Monte Carlo P&L distribution**")
                        render_mc_panel(
                            spread_position,
                            key=f"{key_prefix}_mc_{strategy_name.replace(' ', '_')}_{selected_idx}",
                            label=f"{strategy_name} — {len(legs_spec)}-leg position",
                        )
                    except Exception as exc:  # noqa: BLE001
                        st.caption(f"_MC unavailable for this row: {exc}_")

    with st.expander("Column & Greek key"):
        st.markdown("""
**Spread columns**

| Column | Meaning |
|--------|---------|
| Credit/Debit | Net premium received (+) or paid (−) per share to enter the spread. |
| Max Profit | Maximum gain per share at the best possible outcome. |
| Max Loss | Maximum loss per share (capped at 5× width for Ratio spreads). |
| R/R | Risk-reward ratio: Max Profit ÷ Max Loss. Higher is better. |
| POP% | Probability of Profit at expiration (Black-Scholes N(d₂) based). |
| EV | Expected Value = POP × Max Profit − (1−POP) × Max Loss. Positive EV is statistically favorable. |
| Ann% | Annualized return on capital at risk if the spread reaches max profit. |
| BE Move% | How far the stock price must move from spot to breach the lower breakeven. |
| Δ | Net delta — directional bias of the spread. Near 0 = delta-neutral. |
| θ | Net daily theta — premium earned (positive) or paid (negative) per calendar day. |
| ν | Net vega — P&L change per 1-point rise in IV. Positive = long volatility. |
| IV+pp | IV excess of the short leg above the fitted surface — positive means rich premium. |
| Earn | ⚠ = an earnings event falls before this expiration. |

**Row highlights**

| Color | Meaning |
|-------|---------|
| Green border ⭐ | Positive theta AND positive vega — earns decay and benefits from IV expansion (common in calendars). |
| Green fill | POP ≥ 65% and R/R ≥ 0.20 — high-probability, reasonable reward. |
| Yellow fill | POP ≥ 55% and R/R ≥ 0.10 — moderate probability. |
| Orange Earn cell | Earnings before expiration — IV may spike unpredictably. |
""")


def _tab_spreads() -> None:
    """Power-user view — all 13 spread strategies available."""
    from spreads import STRATEGY_NAMES
    _render_spreads_view(
        key_prefix="sp",
        tab_label="Spreads",
        available_strategies=STRATEGY_NAMES,
        default_strategies=["Bull Put Spread", "Bear Call Spread", "Iron Condor"],
        default_min_dte=21, default_max_dte=60,
        default_min_pop_pct=60,
        default_sort_by="Risk/Reward",
        session_key="spreads_results",
    )


def _tab_directional() -> None:
    """Bullish / bearish strategies only."""
    from spreads import DIRECTIONAL_STRATEGIES
    _render_spreads_view(
        key_prefix="dir",
        tab_label="Directional",
        available_strategies=DIRECTIONAL_STRATEGIES,
        default_strategies=["Bull Put Spread", "Bear Call Spread"],
        default_min_dte=21, default_max_dte=60,
        default_min_pop_pct=60,
        default_sort_by="Risk/Reward",
        session_key="directional_results",
    )


def _tab_neutral() -> None:
    """Range-bound / delta-neutral strategies with a Max |Δ| slider."""
    from spreads import NEUTRAL_STRATEGIES
    _render_spreads_view(
        key_prefix="nu",
        tab_label="Neutral",
        available_strategies=NEUTRAL_STRATEGIES,
        default_strategies=["Iron Condor", "Calendar / Diagonal", "Long Strangle"],
        default_min_dte=30, default_max_dte=180,
        default_min_pop_pct=55,
        default_sort_by="Expected Value",
        session_key="neutral_results",
        include_delta_filter=True,
        default_max_abs_delta=0.15,
    )


# ── Main ─────────────────────────────────────────────────────────────────────

# Layout-specific overrides that build on top of the design system in
# ui_theme.py. These cover Streamlit-version-specific behaviors (rescan
# pill, data-source pill positioning, number-input width caps) that
# don't belong in the shared theme module.
st.html(
    """
    <style>
    [data-testid="stDivider"] {
        margin-top: 0 !important;
        margin-bottom: 0 !important;
    }
    [data-testid="stDivider"] hr {
        margin-top: 0.15rem !important;
        margin-bottom: 0.15rem !important;
    }

    /* Cap number-input widths so the filter row doesn't look like an
       enterprise intake form. */
    [data-testid="stNumberInput"] {
        max-width: 7rem;
    }
    [class*="st-key-top_n_align"] {
        padding-left: 1rem;
    }
    [class*="st-key-scan_btn_lift"] {
        margin-bottom: 0;
        padding-left: 10px;
    }

    /* Floating rescan button — pinned to the top header bar just right
       of the wordmark. Tracks the sidebar shift via the data-sidebar-open
       observer further down. */
    [class*="st-key-rescan_pill"] {
        position: fixed;
        top: 13px;
        left: 21rem;
        transform: none;
        z-index: 999990;
        width: auto !important;
    }
    body[data-sidebar-open="true"] [class*="st-key-rescan_pill"] {
        left: 36rem;
    }
    [class*="st-key-rescan_pill"] .stButton > button {
        padding: 0.35rem 0.95rem !important;
        min-height: 2.5rem;
        border-radius: 8px !important;
        box-shadow: 0 1px 3px rgba(15,23,42,0.12);
        font-weight: 600;
    }

    /* Data-source segmented control — sits to the right of the rescan
       pill. The pill keeps its slot even before a scan so the toggle
       doesn't reflow when results appear. */
    [class*="st-key-data_source_pill"] {
        position: fixed;
        top: 13px;
        left: 33rem;
        transform: none;
        z-index: 999990;
        width: auto !important;
    }
    body[data-sidebar-open="true"] [class*="st-key-data_source_pill"] {
        left: 48rem;
    }
    [class*="st-key-data_source_pill"] [data-testid="stSegmentedControl"] {
        background: rgba(255, 255, 255, 0.92);
        border-radius: 8px;
        box-shadow: 0 1px 3px rgba(15,23,42,0.10);
        border: 1px solid #DBEAFE;
    }
    [class*="st-key-data_source_pill"] button {
        padding: 0.3rem 0.85rem !important;
        min-height: 2.5rem;
        font-weight: 500;
    }
    </style>
    """
)

# Load config and seed data_source_choice into session_state BEFORE the
# dynamic CSS block below reads it.
from config import load_config, get_provider, get_schwab_config as _get_schwab_cfg
_app_cfg = load_config()
_cfg_provider = get_provider(_app_cfg)
_cfg_schwab = _get_schwab_cfg(_app_cfg)
_schwab_configured = (
    bool(_cfg_schwab.get("app_key"))
    and not _cfg_schwab["app_key"].startswith("your-")
    and bool(_cfg_schwab.get("app_secret"))
    and not _cfg_schwab["app_secret"].startswith("your-")
)
if "data_source_choice" not in st.session_state:
    st.session_state["data_source_choice"] = (
        "schwab" if (_cfg_provider == "schwab" and _schwab_configured) else "yahoo"
    )

# Primary buttons and the data-source pill's active state recolor based on
# which data source is selected: green for Yahoo, blue for Schwab. Reads
# `data_source_choice` (the widget key) — NOT the effective `data_source` —
# so the color flips on the same rerun the dropdown changed, and clicking
# Scan doesn't trigger spurious color flips.
_BTN_COLORS = {
    "yahoo":  ("#16a34a", "#15803d"),   # normal, hover
    "schwab": ("#2563eb", "#1d4ed8"),
}
_btn_bg, _btn_hover = _BTN_COLORS.get(
    st.session_state.get("data_source_choice", "yahoo"),
    _BTN_COLORS["yahoo"],
)
st.html(
    f"""
    <style>
    .stButton > button[kind="primary"],
    button[data-testid="stBaseButton-primary"] {{
        background-color: {_btn_bg} !important;
        border-color: {_btn_bg} !important;
    }}
    .stButton > button[kind="primary"]:hover,
    button[data-testid="stBaseButton-primary"]:hover {{
        background-color: {_btn_hover} !important;
        border-color: {_btn_hover} !important;
    }}
    [class*="st-key-data_source_pill"] button[aria-pressed="true"],
    [class*="st-key-data_source_pill"] button[aria-selected="true"],
    [class*="st-key-data_source_pill"] button[data-testid*="Active"] {{
        color: {_btn_bg} !important;
        border-color: {_btn_bg} !important;
        box-shadow: inset 0 0 0 1px {_btn_bg} !important;
    }}
    [class*="st-key-data_source_pill"] button[aria-pressed="true"] p,
    [class*="st-key-data_source_pill"] button[aria-selected="true"] p,
    [class*="st-key-data_source_pill"] button[data-testid*="Active"] p {{
        color: {_btn_bg} !important;
    }}
    </style>
    """
)

# Brand wordmark pinned to the top header bar. Replaces the legacy
# raster-logo overlay with a typographic mark — sharper, scales cleanly,
# and matches the rest of the design system.
st.html(
    """
    <style>
    .osc-wordmark-overlay {
        position: fixed;
        top: 14px;
        left: 5rem;
        height: 2.5rem;
        display: flex;
        align-items: center;
        z-index: 999991;
        pointer-events: none;
        gap: 0.55rem;
        font-family: 'Inter', system-ui, sans-serif;
    }
    @media (prefers-reduced-motion: no-preference) {
        .osc-wordmark-overlay { transition: left 0.2s ease; }
    }
    body[data-sidebar-open="true"] .osc-wordmark-overlay {
        left: 20rem;
    }
    .osc-wordmark-overlay .osc-wm-dot {
        width: 8px; height: 8px; border-radius: 50%;
        background: #1E40AF;
        display: inline-block;
    }
    .osc-wordmark-overlay .osc-wm-brand {
        font-weight: 700;
        font-size: 0.95rem;
        letter-spacing: -0.01em;
        color: #0F172A;
    }
    .osc-wordmark-overlay .osc-wm-suffix {
        font-size: 0.66rem;
        font-weight: 500;
        letter-spacing: 0.18em;
        text-transform: uppercase;
        color: #64748B;
    }
    </style>
    <div class='osc-wordmark-overlay' aria-hidden='true'>
      <span class='osc-wm-dot'></span>
      <span class='osc-wm-brand'>STOCKPILE</span>
      <span class='osc-wm-suffix'>· OPTIONS SCANNER</span>
    </div>
    """
)

# Sidebar-state observer: watches the actual sidebar element's rendered
# width and writes data-sidebar-open onto body so the header-bar CSS
# above can respond. Identical to the previous implementation — Streamlit
# offers no native hook for this.
import streamlit.components.v1 as _components
_components.html(
    """
    <script>
    (function() {
        const doc = window.parent.document;
        const sync = () => {
            const sb = doc.querySelector('[data-testid="stSidebar"]');
            if (!sb) return;
            const w = sb.getBoundingClientRect().width;
            doc.body.dataset.sidebarOpen = w > 60 ? 'true' : 'false';
        };
        sync();
        const obs = new MutationObserver(sync);
        obs.observe(doc.body, {
            childList: true, subtree: true,
            attributes: true,
            attributeFilter: ['style', 'class', 'aria-expanded'],
        });
        window.addEventListener('resize', sync);
    })();
    </script>
    """,
    height=0, width=0,
)


# Title-bar data-source switch — pinned via CSS to the right of the
# rescan pill so it's always visible without opening the sidebar.
def _source_label(s: str) -> str:
    if s == "yahoo":
        return "Yahoo Finance"
    return "Schwab (live)" if _schwab_configured else "Schwab (unconfigured)"

with st.container(key="data_source_pill"):
    _source_raw = st.segmented_control(
        "Data source",
        ["yahoo", "schwab"],
        format_func=_source_label,
        label_visibility="collapsed",
        key="data_source_choice",
    )
if _source_raw is None:
    _source_raw = "yahoo"

if _source_raw == "schwab" and _schwab_configured:
    data_source = "schwab"
else:
    data_source = "yahoo"
st.session_state["data_source"] = data_source
st.session_state["schwab_config"] = _cfg_schwab if data_source == "schwab" else None


# ── Page header chips ────────────────────────────────────────────────────
# The title + subtitle moved to the sidebar "About" panel to reclaim
# vertical space on the main canvas. Disclaimer + source badge remain
# inline at the top, right-aligned, since they're small and useful at-a-
# glance context.
_src_chip_color = PROVIDER_COLORS.get(data_source, "#94a3b8")
_src_chip_label = (
    f"Source: {PROVIDER_LABELS.get(data_source, data_source).upper()}"
)
st.markdown(
    "<div style='display:flex; justify-content:flex-end; "
    "align-items:center; gap:0.5rem; margin-bottom:0.5rem;'>"
    + disclaimer_chip("Research tool · Not investment advice")
    + (
        f"<span style='display:inline-block; padding:0.2rem 0.65rem; "
        f"border-radius:6px; font-size:0.78rem; font-weight:500; "
        f"color:#FFFFFF; background-color:{_src_chip_color};'>"
        f"{_src_chip_label}</span>"
    )
    + "</div>",
    unsafe_allow_html=True,
)

# Sidebar: an "About" panel — the legacy theme picker is gone (we now ship
# one canonical design system). Add helpful links and a status indicator.
with st.sidebar:
    st.markdown(
        "<div style='padding: 0.5rem 0 0.75rem 0;'>"
        + badge("WORKSPACE", "neutral")
        + "</div>",
        unsafe_allow_html=True,
    )
    section_header(
        title="Stockpile",
        subtitle=(
            "Options Analytics made for:<br>"
            "• Income generation<br>"
            "• Directional bets<br>"
            "• Defined-risk spreads<br>"
            "• GEX analysis"
        ),
    )
    st.markdown("---")
    section_header("Data source", eyebrow="ACTIVE PROVIDER")
    _src_label = _source_label(data_source)
    _src_color = PROVIDER_COLORS.get(data_source, "#94a3b8")
    st.markdown(
        f"<div style='font-size:0.86rem; margin-bottom:0.4rem;'>"
        f"<span style='display:inline-block; padding:0.2rem 0.65rem; "
        f"border-radius:6px; font-weight:500; color:#FFFFFF; "
        f"background-color:{_src_color};'>{_src_label}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Switch between Yahoo Finance (free, 15-min delay) and Schwab "
        "(authenticated, live). Use the toggle in the top bar."
    )
    st.markdown("---")
    section_header("About", eyebrow="HOW THIS WORKS")
    st.caption(
        "Surface contracts whose implied volatility sits above (or below) "
        "the fitted surface. Filter by DTE, delta, liquidity; export a "
        "shareable HTML report."
    )
    st.caption(
        "For every option in the chain, we fit a smooth volatility "
        "surface across strike and DTE, then rank contracts by how much "
        "their IV exceeds the fit (IV+pp). 3pp ≈ noise; 5+pp is signal."
    )
    st.markdown("---")
    section_header("Documentation", eyebrow="REFERENCE")
    st.markdown(
        "- [README](https://github.com/) — overview & install\n"
        "- [Interpreting IV](https://github.com/) — what IV+pp means\n"
        "- [Spreads](https://github.com/) — strategy glossary",
        unsafe_allow_html=False,
    )

# Compatibility shim — keep `_apply_theme(theme_choice)` working in case
# any deferred code path references it. With the new design system in
# place this is a no-op.
_apply_theme("Default")

(
    tab_single, tab_gex, tab_portfolio,
    tab_spreads, tab_directional, tab_neutral,
) = st.tabs(
    ["Single Ticker", "GEX", "Portfolio",
     "Spreads", "Directional", "Neutral"]
)

with tab_single:
    _tab_single()

with tab_gex:
    _tab_gex()

with tab_portfolio:
    _tab_portfolio()

with tab_spreads:
    _tab_spreads()

with tab_directional:
    _tab_directional()

with tab_neutral:
    _tab_neutral()

# ── Footer ───────────────────────────────────────────────────────────────
ui_footer()
