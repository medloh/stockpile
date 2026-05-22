"""Portfolio tab: upload a brokerage CSV, scan every open position.

For each ticker in the CSV: fetch the call chain (only — the portfolio
flow is covered-call-oriented), filter by DTE/OI/volume/delta, and
surface the rank-1 candidate as an explicit action card. Covered
positions also get a roll-close lookup for the existing short call.

The tab keeps its CSV validation helpers (_validate_csv,
_show_validation) module-private — they're only meaningful in this
upload context.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date, datetime

import streamlit as st

from options_scanner.display.iv_chart import show_iv_chart
from options_scanner.display.portfolio_action_card import render_portfolio_action_card
from options_scanner.display.scan_results import show_scan_results
from options_scanner.display.spot_meta import (
    fetch_spot_meta,
    spot_help_text,
    spot_value_html,
)
from options_scanner.fetch import fetch_position
from options_scanner.ui_theme import badge, metric_card, section_header


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
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        from options_scanner.portfolio import get_portfolio
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


def tab_portfolio() -> None:
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
        from options_scanner.portfolio import get_portfolio
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

            df, earnings_dates, err = fetch_position(
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
                _meta = fetch_spot_meta(
                    ticker, st.session_state.get("scan_provider", "yahoo"),
                )
                metric_card("SPOT",
                            spot_value_html(spot, _meta["pct_change"]),
                            help_text=spot_help_text(_meta))
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
            render_portfolio_action_card(
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
    from options_scanner.report import render_portfolio_html
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
