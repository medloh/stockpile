"""Streamlit web UI for the options scanner."""

import asyncio
import sys

# Streamlit's internal async handling is incompatible with Windows's default
# ProactorEventLoop on Python 3.12+. Switch to the Selector policy before
# Streamlit starts its own loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from pathlib import Path

import streamlit as st

from options_scanner.ui_theme import (
    badge,
    disclaimer_chip,
    footer as ui_footer,
    inject_theme,
    metric_card,
    register_altair_theme,
    section_header,
)
from options_scanner.display.scan_stamp import PROVIDER_LABELS, PROVIDER_COLORS
from options_scanner.tabs.gex import tab_gex
from options_scanner.tabs.portfolio import tab_portfolio
from options_scanner.tabs.single import tab_single
from options_scanner.tabs.spreads import tab_directional, tab_neutral, tab_spreads

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


# ── Main ─────────────────────────────────────────────────────────────────────

# Load the layout-specific stylesheet that builds on top of the design
# system in ui_theme.py. See options_scanner/styles.css for the rules.
_STYLES_CSS = (
    Path(__file__).parent / "options_scanner" / "styles.css"
).read_text(encoding="utf-8")
st.html(f"<style>{_STYLES_CSS}</style>")

# Load config and seed data_source_choice into session_state BEFORE the
# dynamic accent-color block below reads it.
from options_scanner.config import load_config, get_provider, get_schwab_config as _get_schwab_cfg
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

# Inject the dynamic accent colors as CSS custom properties. Primary
# buttons and the data-source pill's active state read --primary /
# --primary-hover from styles.css. Reads `data_source_choice` (the
# widget key) — NOT the effective `data_source` — so the color flips
# on the same rerun the dropdown changed, and clicking Scan doesn't
# trigger spurious color flips.
_BTN_COLORS = {
    "yahoo":  ("#16a34a", "#15803d"),   # normal, hover
    "schwab": ("#2563eb", "#1d4ed8"),
}
_btn_bg, _btn_hover = _BTN_COLORS.get(
    st.session_state.get("data_source_choice", "yahoo"),
    _BTN_COLORS["yahoo"],
)
st.html(
    f"<style>:root {{ --primary: {_btn_bg}; "
    f"--primary-hover: {_btn_hover}; }}</style>"
)

# Brand wordmark pinned to the top header bar. The styling lives in
# styles.css; only the markup is emitted here.
st.html(
    """
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
    panel_single, panel_gex, panel_portfolio,
    panel_spreads, panel_directional, panel_neutral,
) = st.tabs(
    ["Single Ticker", "GEX", "Portfolio",
     "Spreads", "Directional", "Neutral"]
)

with panel_single:
    tab_single()

with panel_gex:
    tab_gex()

with panel_portfolio:
    tab_portfolio()

with panel_spreads:
    tab_spreads()

with panel_directional:
    tab_directional()

with panel_neutral:
    tab_neutral()

# ── Footer ───────────────────────────────────────────────────────────────
ui_footer()
