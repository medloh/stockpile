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

def _wide_spread_mask(bid: pd.Series, ask: pd.Series,
                      mid: pd.Series) -> list[bool]:
    ratios = ((ask - bid) / mid.clip(lower=0.01)).tolist()
    vals   = sorted(ratios)
    median = vals[len(vals) // 2] if vals else 0.0
    thresh = max(median * 1.5, 0.15)
    return [r > thresh for r in ratios]


def _low_oi_mask(oi: pd.Series, min_oi: int) -> list[bool]:
    thresh = max(min_oi * 2, 10)
    return [v < thresh for v in oi.tolist()]


def _low_vol_mask(vol: pd.Series, min_vol: int) -> list[bool]:
    thresh = max(min_vol * 2, 4)
    return [v < thresh for v in vol.tolist()]


def _compute_top_ranks(df: pd.DataFrame, mode: str, buy: bool,
                       min_oi: int, top_n: int,
                       min_vol: int = 0,
                       ) -> dict[tuple[str, float, str], int]:
    """Return {(type, strike, expiration): rank} for top-N candidates,
    where rank is 1-indexed per option type. Same ranking logic the
    bottom table and the chart picks use, factored out so the chart
    and chain table can label each pick with its position.
    """
    if df.empty:
        return {}
    iv_asc = buy
    pick_types = ["call", "put"] if mode == "both" else [mode]
    ranks: dict[tuple[str, float, str], int] = {}
    for t in pick_types:
        ranked = (
            df[(df["type"] == t)
               & (df["open_interest"] >= min_oi)
               & (df["volume"] >= min_vol)]
            .sort_values(["iv_excess", "open_interest"],
                         ascending=[iv_asc, False])
            .head(top_n)
            .reset_index(drop=True)
        )
        for i, r in ranked.iterrows():
            ranks[(r["type"], float(r["strike"]), r["expiration"])] = i + 1
    return ranks


_CELL_WARN = "background-color: rgba(234,179,8,0.45)"
_BID_HELP  = ("Yellow: spread is wider than 1.5× the median for this table"
              " — higher execution cost.")
_OI_HELP   = ("Yellow: OI is below 2× the min OI filter"
              " — limited liquidity, harder to fill at a good price.")
_IVPP_HELP = ("Percentage points the option's IV sits above the fitted"
              " volatility surface. Positive = richer than peers at a"
              " similar strike and DTE. Under ~3 pp is noise; 5+ pp is"
              " a genuine signal.")
_VOL_HELP  = "Yellow: fewer than 4 contracts traded today — very thin activity."


# ── Scan provenance stamp ────────────────────────────────────────────────────
# Data source + scan timestamp shown on every chart and below every table so
# the context survives screenshots, HTML exports, and Reddit reposts.

_PROVIDER_LABELS = {"yahoo": "Yahoo Finance", "schwab": "Schwab"}
_PROVIDER_COLORS = {"yahoo": "#16a34a", "schwab": "#2563eb"}  # green / blue


def _tz_abbr(ts) -> str:
    """3–4 char timezone abbreviation that works across platforms.

    Python's strftime('%Z') gives the full name on Windows ('Eastern
    Daylight Time') but the short form on POSIX ('EDT'). Normalize by
    taking the uppercase initials when the name is long.
    """
    name = ts.tzname() or ""
    if not name:
        return ""
    if len(name) <= 4:
        return name
    return "".join(w[0] for w in name.split() if w[:1].isupper())[:4]


def _scan_stamp_text() -> str:
    """Format like 'Schwab · 2026-05-16 14:32 EDT'. Empty if no scan yet.

    Reads `scan_provider` (snapshotted at scan time) — NOT the live data
    source dropdown — so the stamp reflects what was actually used to
    fetch the displayed data, even after the user changes the dropdown.
    """
    ts = st.session_state.get("scan_ts")
    if not ts:
        return ""
    provider = st.session_state.get("scan_provider", "yahoo")
    label = _PROVIDER_LABELS.get(provider, provider)
    return f"{label} · {ts.strftime('%Y-%m-%d %H:%M')} {_tz_abbr(ts)}".rstrip()


def _scan_stamp_color() -> str:
    """Hex color for the stamp text, based on the provider at scan time."""
    provider = st.session_state.get("scan_provider", "yahoo")
    return _PROVIDER_COLORS.get(provider, "#94a3b8")


def _stamp_caption() -> None:
    """Render the scan stamp as a colored caption below a table."""
    text = _scan_stamp_text()
    if not text:
        return
    color = _scan_stamp_color()
    st.markdown(
        f'<div style="color:{color}; font-size:0.85rem; '
        f'margin-top:-4px;">{text}</div>',
        unsafe_allow_html=True,
    )


def _show_df(sub: pd.DataFrame, roll_close_cost: float | None = None,
             min_oi: int = 0, min_vol: int = 0) -> None:
    if sub.empty:
        empty_state(
            "No matches in this chain",
            "Try widening the delta band, lowering min OI/Volume, or "
            "extending the DTE range.",
        )
        return

    disp = pd.DataFrame({
        "Strike": sub["strike"].apply(lambda x: f"${x:.0f}"),
        "Expiration": sub["expiration"].apply(
            lambda e: datetime.strptime(e, "%Y-%m-%d").strftime("%b %d '%y")
        ),
        "DTE":    sub["dte"].astype(int),
        "Bid":    sub["bid"].round(2),
        "Ask":    sub["ask"].round(2),
        "Mid":    sub["mid"].round(2),
        "IV%":    (sub["iv"] * 100).round(1),
        "IV+pp":  (sub["iv_excess"] * 100).round(1),
        "Delta":  sub["delta"].round(2),
        "Ann%":   sub["ann_yield_pct"].round(1),
        "OI":     sub["open_interest"],
        "Vol":    sub["volume"],
    })
    if roll_close_cost is not None:
        disp["NetCr"] = (sub["mid"] - roll_close_cost).round(2)

    wide   = _wide_spread_mask(sub["bid"], sub["ask"], sub["mid"])
    lo     = _low_oi_mask(sub["open_interest"], min_oi)
    low_vol = _low_vol_mask(sub["volume"], min_vol)

    styled = (
        disp.style
        .apply(lambda _: [_CELL_WARN if w else "" for w in wide],
               subset=["Bid", "Ask"])
        .apply(lambda _: [_CELL_WARN if l else "" for l in lo],
               subset=["OI"])
        .apply(lambda _: [_CELL_WARN if v else "" for v in low_vol],
               subset=["Vol"])
    )

    col_cfg = {
        "Strike":     st.column_config.TextColumn("Strike", width=75),
        "Expiration": st.column_config.TextColumn("Expiration", width=105),
        "DTE":   st.column_config.NumberColumn("DTE", format="%d", width=55),
        "Bid":   st.column_config.NumberColumn("Bid", format="$%.2f",
                                               width=70, help=_BID_HELP),
        "Ask":   st.column_config.NumberColumn("Ask", format="$%.2f",
                                               width=70, help=_BID_HELP),
        "Mid":   st.column_config.NumberColumn("Mid", format="$%.2f",
                                               width=70),
        "IV%":   st.column_config.NumberColumn("IV%", format="%.1f%%",
                                               width=70),
        "IV+pp": st.column_config.NumberColumn("IV+pp", format="%+.1f pp",
                                               width=75, help=_IVPP_HELP),
        "Delta": st.column_config.NumberColumn("Delta", format="%.2f",
                                               width=60),
        "Ann%":  st.column_config.NumberColumn("Ann%", format="%.1f%%",
                                               width=65),
        "OI":    st.column_config.NumberColumn("OI", format="%d",
                                               width=65, help=_OI_HELP),
        "Vol":   st.column_config.NumberColumn("Vol", format="%d",
                                               width=65, help=_VOL_HELP),
    }
    if roll_close_cost is not None:
        col_cfg["NetCr"] = st.column_config.NumberColumn("Net Credit",
                                                         format="$%+.2f",
                                                         width=85)

    st.dataframe(styled, column_config=col_cfg, hide_index=True,
                 width="stretch")
    _stamp_caption()


def _show_iv_chart(df: pd.DataFrame, spot: float, mode: str,
                   min_oi: int, top_n: int, buy: bool,
                   ticker: str = "", key_prefix: str = "s",
                   min_vol: int = 0) -> None:
    """Layered chart: per-expiration smile with the table's top-N picks
    highlighted. Faded background dots are the rest of the chain at the
    selected expiration; bright outlined dots are the picks."""
    if df.empty:
        return

    chart_df = df.copy()
    if mode in ("call", "put"):
        chart_df = chart_df[chart_df["type"] == mode]
    if chart_df.empty:
        return

    top_ranks = _compute_top_ranks(
        chart_df, mode, buy, min_oi, top_n, min_vol,
    )
    chart_df["is_top"] = chart_df.apply(
        lambda r: (r["type"], float(r["strike"]), r["expiration"]) in top_ranks,
        axis=1,
    )
    chart_df["rank_label"] = chart_df.apply(
        lambda r: str(top_ranks.get(
            (r["type"], float(r["strike"]), r["expiration"]), ""
        )),
        axis=1,
    )
    chart_df["IV%"]        = (chart_df["iv"] * 100).round(2)
    chart_df["FittedIV%"]  = (chart_df["iv_fitted"] * 100).round(2)
    chart_df["IV+pp"]      = (chart_df["iv_excess"] * 100).round(2)
    chart_df["Ann%"]       = chart_df["ann_yield_pct"].round(2)
    exp_dte = chart_df.groupby("expiration")["dte"].first().to_dict()
    chart_df["ExpLabel"] = chart_df["expiration"].apply(
        lambda d: f"{datetime.strptime(d, '%Y-%m-%d').strftime('%b %d \'%y')} ({exp_dte.get(d, 0)}d)"
    )

    expirations = sorted(chart_df["expiration"].unique())
    exp_labels  = {
        e: f"{datetime.strptime(e, '%Y-%m-%d').strftime('%b %d \'%y')} — {exp_dte.get(e, 0)}d"
        for e in expirations
    }
    pick_counts = {
        e: int(chart_df[(chart_df["expiration"] == e)
                        & chart_df["is_top"]].shape[0])
        for e in expirations
    }
    # Default to the expiration containing the strongest signal — the
    # pick with the highest IV+pp (or lowest, in buy mode). Falls back
    # to the first expiration if there are no picks for some reason.
    picks_df = chart_df[chart_df["is_top"]]
    if not picks_df.empty:
        extreme_idx = (picks_df["iv_excess"].idxmin() if buy
                       else picks_df["iv_excess"].idxmax())
        default_exp = picks_df.loc[extreme_idx, "expiration"]
        default_idx = expirations.index(default_exp)
    else:
        default_idx = 0

    # Header row: title on the left, expiration selector on the right
    h1, h2 = st.columns([1, 2], vertical_alignment="bottom")
    with h1:
        # Bottom margin lifts the heading 5px up relative to the
        # selectbox in the bottom-aligned column row.
        st.markdown(
            "<h5 style='margin:0 0 5px 0'>Volatility surface</h5>",
            unsafe_allow_html=True,
        )
    with h2:
        chosen_exp = st.selectbox(
            "Expiration to chart",
            options=expirations,
            index=default_idx,
            format_func=lambda d: f"{exp_labels[d]}  ({pick_counts[d]} pick"
                                  f"{'s' if pick_counts[d] != 1 else ''})",
            key=f"{key_prefix}_chart_exp",
            help="Each expiration has its own volatility smile. The number "
                 "in parentheses is how many of the table's top picks live "
                 "at that expiration.",
            label_visibility="collapsed",
        )

    sub = chart_df[chart_df["expiration"] == chosen_exp].sort_values(
        ["type", "strike"]
    )
    if sub.empty:
        return

    excess_max = max(abs(sub["IV+pp"].min()), abs(sub["IV+pp"].max()), 1.0)
    # Green = attractive (high IV+pp to sell; low IV+pp to buy); red = unattractive.
    # Flip the range in buy mode so the color always agrees with the table shading.
    if buy:
        color_range = ["#22c55e", "#cbd5e1", "#ef4444"]  # negative=green, positive=red
    else:
        color_range = ["#ef4444", "#cbd5e1", "#22c55e"]  # negative=red, positive=green
    color_scale = alt.Scale(
        domain=[-excess_max, 0, excess_max],
        range=color_range,
    )
    shape_scale = alt.Scale(domain=["call", "put"],
                            range=["circle", "square"])

    # X-domain extended so the spot line is always inside the visible range
    x_min = min(float(sub["strike"].min()), spot) * 0.97
    x_max = max(float(sub["strike"].max()), spot) * 1.03
    y_max = float(sub[["IV%", "FittedIV%"]].values.max())

    base_x = alt.X(
        "strike:Q", title="Strike",
        scale=alt.Scale(domain=[x_min, x_max]),
        axis=alt.Axis(format="$,.0f"),
    )

    tooltip_fields = [
        alt.Tooltip("strike:Q",        title="Strike", format="$,.0f"),
        alt.Tooltip("type:N",          title="Type"),
        alt.Tooltip("IV%:Q",           format=".1f"),
        alt.Tooltip("FittedIV%:Q",     title="Fitted IV%", format=".1f"),
        alt.Tooltip("IV+pp:Q",         title="IV excess (pp)", format="+.1f"),
        alt.Tooltip("delta:Q",         format=".2f"),
        alt.Tooltip("Ann%:Q",          title="Ann%", format=".1f"),
        alt.Tooltip("volume:Q",        title="Volume", format=",.0f"),
        alt.Tooltip("open_interest:Q", title="OI"),
        alt.Tooltip("bid:Q",           title="Bid",  format="$.2f"),
        alt.Tooltip("ask:Q",           title="Ask",  format="$.2f"),
    ]

    fitted_line = alt.Chart(sub).mark_line(
        color="#94a3b8", strokeDash=[4, 3], size=2,
    ).encode(
        x=base_x,
        y=alt.Y("FittedIV%:Q", title="Implied Volatility (%)"),
        detail="type:N",
    )

    background = alt.Chart(sub[~sub["is_top"]]).mark_circle(
        size=60, opacity=1.0,
    ).encode(
        x=base_x,
        y="IV%:Q",
        color=alt.Color("IV+pp:Q", scale=color_scale,
                        legend=alt.Legend(title="IV excess (pp)")),
        shape=alt.Shape("type:N", scale=shape_scale,
                        legend=alt.Legend(title="Type")),
        tooltip=tooltip_fields,
    )

    picks = alt.Chart(sub[sub["is_top"]]).mark_point(
        size=260, opacity=1.0, filled=True,
        stroke="#0f172a", strokeWidth=2,
    ).encode(
        x=base_x,
        y="IV%:Q",
        color=alt.Color("IV+pp:Q", scale=color_scale, legend=None),
        shape=alt.Shape("type:N", scale=shape_scale, legend=None),
        tooltip=tooltip_fields,
    )

    # Rank badge above each pick — shows where this option sits in
    # the top-N list per type (1 = strongest signal). Same ordering
    # as the bottom table, so the user can match chart picks to table
    # rows at a glance.
    ranks = alt.Chart(sub[sub["is_top"]]).mark_text(
        fontSize=14, dy=-20, fontWeight="bold",
        color="#0f172a",
    ).encode(
        x=base_x,
        y="IV%:Q",
        text="rank_label:N",
    )

    spot_df = pd.DataFrame({"x": [spot], "y": [y_max],
                            "label": [f"Spot ${spot:.2f}"]})
    spot_rule = alt.Chart(spot_df).mark_rule(
        color="#0f172a", strokeDash=[3, 3], size=2,
    ).encode(
        x=alt.X("x:Q", scale=alt.Scale(domain=[x_min, x_max])),
        tooltip=[alt.Tooltip("x:Q", title="Spot", format="$,.2f")],
    )
    spot_label = alt.Chart(spot_df).mark_text(
        align="left", baseline="top", dx=5, dy=2,
        color="#0f172a", fontWeight="bold", fontSize=11,
    ).encode(
        x=alt.X("x:Q", scale=alt.Scale(domain=[x_min, x_max])),
        y="y:Q",
        text="label:N",
    )

    type_word = {"call": "calls", "put": "puts", "both": "options"}[mode]
    title_text = (f"{ticker} {type_word} — {exp_labels[chosen_exp]}"
                  if ticker else f"{type_word} — {exp_labels[chosen_exp]}")
    chart = (
        fitted_line + background + picks + ranks + spot_rule + spot_label
    ).properties(
        height=380,
        title=alt.TitleParams(
            text=title_text,
            subtitle=_scan_stamp_text() or None,
            subtitleColor=_scan_stamp_color(),
            subtitleFontSize=11,
            fontSize=16, fontWeight="bold", anchor="start",
            color="#0f172a",
        ),
    )
    st.altair_chart(chart, use_container_width=True)
    st.caption(
        "Dashed gray line is the fitted volatility surface for this "
        "expiration. **Larger outlined dots with a number above them "
        "are the top picks — the number is the rank in the table "
        "below (1 = strongest signal, ranked per type).** Faded dots "
        "are the rest of the chain at this expiration for context. "
        "Green = attractive premium (rich to sell / cheap to buy), "
        "red = unattractive. Vertical dashed line marks the current "
        "spot price."
    )


def _show_chain_table(df_exp: pd.DataFrame, buy: bool, mode: str,
                      roll_close_cost: float | None = None,
                      min_oi: int = 0, min_vol: int = 0,
                      top_ranks: dict[tuple[str, float, str], int]
                                 | None = None,
                      ) -> None:
    """All options for one expiration, sorted by strike, rows shaded by IV+pp."""
    if df_exp.empty:
        empty_state(
            "No options for this expiration",
            "Filters removed every contract at this date. Lower min OI "
            "or relax the delta band to surface more rows.",
        )
        return

    df_s = df_exp.sort_values(["strike", "type"]).reset_index(drop=True)

    tr = top_ranks or {}
    rank_col = [
        str(tr.get((r["type"], float(r["strike"]), r["expiration"]), ""))
        for _, r in df_s.iterrows()
    ]

    cols: dict = {"Top": rank_col}
    if mode == "both":
        cols["Type"] = df_s["type"].str.capitalize()
    cols.update({
        "Strike": df_s["strike"].apply(lambda x: f"${x:.0f}"),
        "DTE":    df_s["dte"].astype(int),
        "Bid":    df_s["bid"].round(2),
        "Ask":    df_s["ask"].round(2),
        "Mid":    df_s["mid"].round(2),
        "IV%":    (df_s["iv"] * 100).round(1),
        "IV+pp":  (df_s["iv_excess"] * 100).round(1),
        "Delta":  df_s["delta"].round(2),
        "Ann%":   df_s["ann_yield_pct"].round(1),
        "OI":     df_s["open_interest"],
        "Vol":    df_s["volume"],
    })
    if roll_close_cost is not None:
        cols["NetCr"] = (df_s["mid"] - roll_close_cost).round(2)
    disp = pd.DataFrame(cols)

    # Row background: IV+pp signal vs 3pp noise floor.
    _NOISE = 0.03
    iv_vals = df_s["iv_excess"].tolist()
    signals = [-v if buy else v for v in iv_vals]

    all_noise = all(abs(v) < _NOISE for v in iv_vals)
    if all_noise:
        best_i  = signals.index(max(signals))
        worst_i = signals.index(min(signals))

    max_pos = max((s for s in signals if s >= _NOISE), default=_NOISE)
    max_neg = max((abs(s) for s in signals if s <= -_NOISE), default=_NOISE)

    def _row_bg(row: pd.Series) -> list[str]:
        i = int(row.name)
        s = signals[i]
        if all_noise:
            if i == best_i:
                bg = "background-color: rgba(34,197,94,0.40)"
            elif i == worst_i:
                bg = "background-color: rgba(239,68,68,0.40)"
            else:
                bg = "background-color: rgba(100,116,139,0.18)"
        elif s >= _NOISE:
            bg = f"background-color: rgba(34,197,94,{s/max_pos*0.50:.2f})"
        elif s <= -_NOISE:
            bg = f"background-color: rgba(239,68,68,{abs(s)/max_neg*0.45:.2f})"
        else:
            bg = "background-color: rgba(100,116,139,0.18)"
        return [bg] * len(row)

    # Cell-level overrides for spread, OI, and vol (applied after row bg).
    wide    = _wide_spread_mask(df_s["bid"], df_s["ask"], df_s["mid"])
    lo      = _low_oi_mask(df_s["open_interest"], min_oi)
    low_vol = _low_vol_mask(df_s["volume"], min_vol)

    styled = (
        disp.style
        .apply(_row_bg, axis=1)
        .apply(lambda _: [_CELL_WARN if w else "" for w in wide],
               subset=["Bid", "Ask"])
        .apply(lambda _: [_CELL_WARN if l else "" for l in lo],
               subset=["OI"])
        .apply(lambda _: [_CELL_WARN if v else "" for v in low_vol],
               subset=["Vol"])
    )

    col_cfg = {
        "Top":   st.column_config.TextColumn(
            "Top", width=50,
            help="Rank in the top candidates table below "
                 "(1 = strongest signal). Ranked per option type "
                 "after OI/Vol filters. Blank = not in top N.",
        ),
        "Type":  st.column_config.TextColumn("Type", width=60),
        "Strike": st.column_config.TextColumn("Strike", width=75),
        "DTE":   st.column_config.NumberColumn("DTE", format="%d", width=55),
        "Bid":   st.column_config.NumberColumn("Bid", format="$%.2f",
                                               width=70, help=_BID_HELP),
        "Ask":   st.column_config.NumberColumn("Ask", format="$%.2f",
                                               width=70, help=_BID_HELP),
        "Mid":   st.column_config.NumberColumn("Mid", format="$%.2f",
                                               width=70),
        "IV%":   st.column_config.NumberColumn("IV%", format="%.1f%%",
                                               width=70),
        "IV+pp": st.column_config.NumberColumn("IV+pp", format="%+.1f pp",
                                               width=75, help=_IVPP_HELP),
        "Delta": st.column_config.NumberColumn("Delta", format="%.2f",
                                               width=60),
        "Ann%":  st.column_config.NumberColumn("Ann%", format="%.1f%%",
                                               width=65),
        "OI":    st.column_config.NumberColumn("OI", format="%d",
                                               width=65, help=_OI_HELP),
        "Vol":   st.column_config.NumberColumn("Vol", format="%d",
                                               width=65, help=_VOL_HELP),
    }
    if roll_close_cost is not None:
        col_cfg["NetCr"] = st.column_config.NumberColumn("Net Credit",
                                                         format="$%+.2f",
                                                         width=85)
    st.dataframe(styled, column_config=col_cfg, hide_index=True,
                 width="stretch")
    _stamp_caption()


def _show_gex_chart(df: pd.DataFrame, spot: float,
                    provider: str = "yahoo",
                    ticker: str = "") -> None:
    """Gamma Exposure (GEX) bar chart by strike, aggregated across all
    expirations.  Positive bars = dealers net long gamma (pinning);
    negative bars = dealers net short gamma (amplifying)."""
    if df.empty or "gamma" not in df.columns:
        return

    spot_sq = spot * spot

    calls = df[df["type"] == "call"].copy()
    puts  = df[df["type"] == "put"].copy()

    calls["gex"] =  calls["gamma"] * calls["open_interest"] * 100 * spot_sq
    puts["gex"]  = -puts["gamma"]  * puts["open_interest"]  * 100 * spot_sq

    gex = (
        pd.concat([calls[["strike", "gex"]], puts[["strike", "gex"]]])
        .groupby("strike", as_index=False)["gex"]
        .sum()
        .sort_values("strike")
    )

    if gex.empty or gex["gex"].abs().sum() == 0:
        return

    total_gex  = gex["gex"].sum()
    gex["color"] = gex["gex"].apply(lambda v: "Pinning" if v >= 0 else "Amplifying")

    # Zero-gamma level: strike where cumulative GEX crosses zero
    gex_sorted   = gex.sort_values("strike")
    cumulative   = gex_sorted["gex"].cumsum()
    zero_cross   = gex_sorted["strike"][cumulative >= 0].min()

    g1, g2, g3 = st.columns(3)
    regime = "Pinning (mean-reverting)" if total_gex >= 0 else "Amplifying (trending)"
    g1.metric("Total GEX", f"{total_gex:,.0f}", help=(
        "Positive = dealers net long gamma across this chain — price "
        "tends to mean-revert. Negative = dealers net short gamma — "
        "moves tend to be amplified."
    ))
    g2.metric("Regime", regime)
    if not pd.isna(zero_cross):
        g3.metric("Zero-gamma level", f"${zero_cross:,.2f}", help=(
            "Strike where cumulative dealer gamma flips sign. "
            "Price above this level tends to be more volatile."
        ))

    x_min = min(float(gex["strike"].min()), spot) * 0.97
    x_max = max(float(gex["strike"].max()), spot) * 1.03
    y_max_gex = float(gex["gex"].max())

    bars = alt.Chart(gex).mark_bar(opacity=0.85).encode(
        x=alt.X("strike:Q", title="Strike",
                scale=alt.Scale(domain=[x_min, x_max]),
                axis=alt.Axis(format="$,.0f")),
        y=alt.Y("gex:Q", title="Net GEX ($)"),
        color=alt.Color("color:N",
                        scale=alt.Scale(
                            domain=["Pinning", "Amplifying"],
                            range=["#22c55e", "#ef4444"],
                        ),
                        legend=alt.Legend(title=None)),
        tooltip=[
            alt.Tooltip("strike:Q",  title="Strike",  format="$,.0f"),
            alt.Tooltip("gex:Q",     title="Net GEX", format=",.0f"),
            alt.Tooltip("color:N",   title="Effect"),
        ],
    )

    spot_df = pd.DataFrame({"x": [spot], "y": [y_max_gex],
                            "label": [f"Spot ${spot:.2f}"]})
    spot_rule = alt.Chart(spot_df).mark_rule(
        color="#0f172a", strokeDash=[3, 3], strokeWidth=1.5,
    ).encode(
        x=alt.X("x:Q", scale=alt.Scale(domain=[x_min, x_max])),
    )
    spot_label = alt.Chart(spot_df).mark_text(
        align="left", baseline="top", dx=5, dy=2,
        color="#0f172a", fontWeight="bold", fontSize=11,
    ).encode(
        x=alt.X("x:Q", scale=alt.Scale(domain=[x_min, x_max])),
        y="y:Q",
        text="label:N",
    )

    # Build a screenshot-friendly title: ticker first, then chart type.
    # Falls back to just the chart name if no ticker is passed.
    title_text = (f"{ticker} — Gamma Exposure (GEX) by strike"
                  if ticker else "Gamma Exposure (GEX) by strike")

    # DTE scope footnote so screenshots taken days later still convey
    # which slice of the chain the bars are summed over.
    if "dte" in df.columns and not df["dte"].empty:
        dte_lo = int(df["dte"].min())
        dte_hi = int(df["dte"].max())
        n_exp  = int(df["expiration"].nunique())
        dte_note = (f"Aggregated across {n_exp} expiration"
                    f"{'s' if n_exp != 1 else ''} "
                    f"({dte_lo}–{dte_hi} DTE).")
    else:
        dte_note = "Aggregated across all expirations in the current scan."

    st.altair_chart(
        (bars + spot_rule + spot_label).properties(
            height=240,
            title=alt.TitleParams(
                text=title_text,
                subtitle=_scan_stamp_text() or None,
                subtitleColor=_scan_stamp_color(),
                subtitleFontSize=11,
                fontSize=14, fontWeight="bold", anchor="start",
                color="#0f172a",
            ),
        ).configure_view(strokeWidth=0),
        use_container_width=True,
    )

    provider_caveat = (
        "GEX estimated from Black-Scholes gamma (Yahoo IV may be stale on LEAPS)."
        if provider == "yahoo"
        else "GEX computed from Schwab's native gamma values."
    )
    st.caption(f"{dte_note} {provider_caveat}")


def _show_scan_results(df: pd.DataFrame, mode: str, buy: bool,
                       roll_close_cost: float | None,
                       min_oi: int, top_n: int,
                       min_vol: int = 0) -> None:
    iv_asc = buy
    type_labels = {"call": "Calls", "put": "Puts"}
    to_show = [mode] if mode in type_labels else list(type_labels.keys())

    for opt_type in to_show:
        sub = (
            df[df["type"] == opt_type]
            .sort_values(["iv_excess", "open_interest"], ascending=[iv_asc, False])
        )
        sub = sub[(sub["open_interest"] >= min_oi)
                  & (sub["volume"] >= min_vol)].head(top_n)
        if len(to_show) > 1:
            st.subheader(type_labels[opt_type])
        _show_df(sub, roll_close_cost, min_oi, min_vol)


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
            a1, a2, _ = st.columns([2.2, 1.8, 2])
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
                                          help="0 = no limit", key="s_max_dte")
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
        metric_card("SPOT PRICE", f"${spot:,.2f}",
                    help_text="Last trade — Yahoo or Schwab depending on source.")
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

    _show_iv_chart(df_filt, spot, mode_r, res["min_oi"], res["top_n"],
                   buy_r, ticker=ticker_r, key_prefix="s",
                   min_vol=res.get("min_vol", 0))

    _show_gex_chart(df_r, spot,
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
        top_ranks = _compute_top_ranks(
            df_filt, mode_r, buy_r, res["min_oi"], res["top_n"],
            res.get("min_vol", 0),
        )
        _show_chain_table(df_chain, buy_r, mode_r, rcc, res["min_oi"],
                          res.get("min_vol", 0), top_ranks=top_ranks)

    st.subheader("Top candidates — all chains")
    _show_scan_results(df_filt, mode_r, buy_r, rcc,
                       res["min_oi"], res["top_n"],
                       res.get("min_vol", 0))

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


# ── Tab: Portfolio ───────────────────────────────────────────────────────────

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

    pc1, pc2, pc3, pc4, pc5 = st.columns(5)
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
        port_delta_range = st.slider("Delta Range", 0.0, 1.0, (0.10, 0.70),
                                     0.05, key="p_delta")
    with pc5:
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
                metric_card("SPOT", f"${spot:,.2f}")
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

            _show_iv_chart(df_filt, spot, "call",
                           int(port_min_oi), int(port_top), False,
                           ticker=ticker, key_prefix=f"p_{ticker}")

            st.markdown("**Top candidates**")
            _show_scan_results(df_filt, "call", False, roll_close,
                               int(port_min_oi), int(port_top))

    # Portfolio HTML download
    from report import render_portfolio_html
    port_html = render_portfolio_html(
        results, uploaded_name, int(port_min_oi), int(port_top)
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


def _show_payoff_chart(row: pd.Series, spot: float) -> None:
    from spreads import spread_payoff_data, build_legs_from_row
    import altair as alt
    legs = build_legs_from_row(row)
    if not legs:
        return
    T = max(int(row["dte"]), 1) / 365.0
    data = spread_payoff_data(legs, spot, T)

    # Melt to long form for Altair
    melted = data.melt("price", var_name="line", value_name="pl")
    melted["line"] = melted["line"].map(
        {"pl_expiry": "At Expiration", "pl_current": "Current Value (BS)"}
    )

    # Shaded area: green above 0, red below 0 — use two area layers
    zero_line = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
        color="#475569", strokeDash=[3, 3], size=1
    ).encode(y="y:Q")

    spot_rule = alt.Chart(pd.DataFrame({"x": [spot]})).mark_rule(
        color="#0f172a", strokeDash=[4, 4], size=1.5
    ).encode(x="x:Q")

    # Breakeven rules
    be_rules = []
    for be_col, color in [("breakeven1", "#f97316"), ("breakeven2", "#f97316")]:
        be_val = row.get(be_col)
        if be_val and not pd.isna(be_val):
            be_rules.append(
                alt.Chart(pd.DataFrame({"x": [float(be_val)]})).mark_rule(
                    color=color, strokeDash=[5, 3], size=1.5
                ).encode(x="x:Q")
            )

    color_scale = alt.Scale(
        domain=["At Expiration", "Current Value (BS)"],
        range=["#0f172a", "#94a3b8"],
    )
    dash_scale = alt.Scale(
        domain=["At Expiration", "Current Value (BS)"],
        range=[[1, 0], [6, 3]],
    )

    lines = alt.Chart(melted).mark_line(size=2).encode(
        x=alt.X("price:Q", title="Stock Price", axis=alt.Axis(format="$,.0f")),
        y=alt.Y("pl:Q", title="P&L per share ($)", axis=alt.Axis(format="$.2f")),
        color=alt.Color("line:N", scale=color_scale,
                        legend=alt.Legend(title=None, orient="top-left")),
        strokeDash=alt.StrokeDash("line:N", scale=dash_scale, legend=None),
    )

    strategy = row.get("strategy", "Spread")
    exp = row.get("expiration", "")
    pop_pct = f"{row.get('pop', 0):.0%}"
    title = f"{strategy} — {exp} — POP {pop_pct}"

    chart = (zero_line + spot_rule + lines)
    for r in be_rules:
        chart = chart + r
    chart = chart.properties(
        height=300,
        title=alt.TitleParams(
            text=title,
            subtitle=_scan_stamp_text() or None,
            subtitleColor=_scan_stamp_color(),
            subtitleFontSize=11,
            fontSize=14, fontWeight="bold",
            anchor="start", color="#0f172a",
        ),
    )
    st.altair_chart(chart, use_container_width=True)
    be_note = []
    be1 = row.get("breakeven1")
    be2 = row.get("breakeven2")
    if be1 and not pd.isna(be1):
        be_note.append(f"BE₁ ${float(be1):.2f}")
    if be2 and not pd.isna(be2):
        be_note.append(f"BE₂ ${float(be2):.2f}")
    if be_note:
        st.caption(f"Orange dashed lines mark breakevens: {', '.join(be_note)}. "
                   "Dashed gray = current BS value assuming constant IV.")


def _show_spreads_table(sub: pd.DataFrame, strategy_name: str,
                        spot: float) -> int | None:
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
        "IV+pp":      st.column_config.NumberColumn("IV+pp", format="%+.1f pp", width="small",
                                                     help=_IVPP_HELP),
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
        key=f"sp_tbl_{strategy_name.replace(' ', '_').replace('/', '_').replace('×', 'x')}",
    )
    _stamp_caption()
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
        metric_card("SPOT PRICE", f"${spot:,.2f}")
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
            selected_idx = _show_spreads_table(sub, strategy_name, spot)

            if selected_idx is not None and selected_idx < len(sub):
                row = sub.iloc[selected_idx]
                st.markdown("**Payoff diagram**")
                _show_payoff_chart(row, spot)

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

# Active state for the data-source pill picks up the primary blue — the
# whole product follows one accent now, so we no longer recolor the
# scan button per-source. The user can still tell which source is active
# from the segmented-control selection state.
_PRIMARY = PALETTE["primary"]
st.html(
    f"""
    <style>
    [class*="st-key-data_source_pill"] button[aria-pressed="true"],
    [class*="st-key-data_source_pill"] button[aria-selected="true"],
    [class*="st-key-data_source_pill"] button[data-testid*="Active"] {{
        color: {_PRIMARY} !important;
        border-color: {_PRIMARY} !important;
        box-shadow: inset 0 0 0 1px {_PRIMARY} !important;
    }}
    [class*="st-key-data_source_pill"] button[aria-pressed="true"] p,
    [class*="st-key-data_source_pill"] button[aria-selected="true"] p,
    [class*="st-key-data_source_pill"] button[data-testid*="Active"] p {{
        color: {_PRIMARY} !important;
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


# ── Page header ──────────────────────────────────────────────────────────
# Sits inside the main canvas (not the fixed top bar) — gives the page a
# proper title, subtitle, and the disclaimer chip.
_header_left, _header_right = st.columns([3, 2], vertical_alignment="center")
with _header_left:
    section_header(
        title="Options scanner",
        subtitle=(
            "Surface contracts whose implied volatility sits above (or below) "
            "the fitted surface. Filter by DTE, delta, liquidity; export a "
            "shareable HTML report."
        ),
        eyebrow="OPTION CHAIN · IV SURFACE · SPREADS",
    )
with _header_right:
    st.markdown(
        "<div style='display:flex; justify-content:flex-end; "
        "align-items:center; gap:0.5rem;'>"
        + disclaimer_chip("Research tool · Not investment advice")
        + badge(
            f"Source: {_PROVIDER_LABELS.get(data_source, data_source).upper()}",
            "info",
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
        subtitle="Options analytics for the patient seller.",
    )
    st.markdown("---")
    section_header("Data source", eyebrow="ACTIVE PROVIDER")
    _src_label = _source_label(data_source)
    st.markdown(
        f"<div style='font-size:0.86rem; color:#1E3A8A; margin-bottom:0.4rem;'>"
        f"{badge(_src_label, 'info' if data_source == 'yahoo' else 'accent')}"
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

tab_single, tab_portfolio, tab_spreads, tab_directional, tab_neutral = st.tabs(
    ["Single Ticker", "Portfolio", "Spreads", "Directional", "Neutral"]
)

with tab_single:
    _tab_single()

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
