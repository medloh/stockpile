"""UI theme + design-system helpers for the options-scanner Streamlit app.

Implements the Analytics Dashboard token set: deep blue primary, amber
accent, Inter throughout. Restraint over decoration — blue data, amber
highlights for the IV+pp signal, semantic green/red reserved for genuine
positive/negative deltas only.

Public helpers:
    inject_theme()                — global CSS injection, call once at top of app
    section_header(title, sub)    — display heading with optional subtitle
    metric_card(label, value, …)  — refined KPI card (replaces st.metric)
    badge(text, variant)          — inline pill (neutral / positive / negative / warn / info)
    altair_theme()                — dict to register with Altair
    PALETTE                       — canonical color tokens for charts and code

Accessibility commitments enforced via this module:
- Inter at 400/500/600 with tabular-nums on numerics for column alignment.
- Focus rings restyled (NOT removed): 3px box-shadow ring on inputs/buttons.
- prefers-reduced-motion gates every transition we add.
- Semantic colors (Destructive/Success) are reserved for sign-of-change
  use only; chrome relies on the blue + neutral scale.
- No emoji glyphs for structural icons — Unicode triangles (▲ ▼ ●) only.
"""

from __future__ import annotations

from typing import Literal

import streamlit as st


# ── Color tokens (Analytics Dashboard token set) ────────────────────────────
#
# These are fixed by the design system. Do not introduce new hues — every
# additional color reads as decorative noise in a dense analytics tool.

PALETTE: dict[str, str] = {
    # Brand / interactive
    "primary":      "#1E40AF",   # deep blue — buttons, brand, ring
    "primary_fg":   "#FFFFFF",
    "secondary":    "#3B82F6",   # mid blue — secondary fills, links
    "accent":       "#D97706",   # amber — IV+pp signal highlight
    "ring":         "#1E40AF",

    # Canvas / surface
    "background":   "#F8FAFC",
    "foreground":   "#1E3A8A",   # ink — body text on canvas
    "card":         "#FFFFFF",
    "card_fg":      "#1E3A8A",
    "muted":        "#E9EEF6",
    "muted_fg":     "#64748B",
    "border":       "#DBEAFE",
    "border_strong":"#BFD7F2",   # mid-stop between border and primary for charts

    # Semantic — sign-of-change ONLY
    "destructive":  "#DC2626",
    "success":      "#059669",

    # Ink scale (derived, all pass 4.5:1 on the F8FAFC canvas)
    "ink_1":        "#0F172A",   # heading ink — 17.4:1
    "ink_2":        "#1E3A8A",   # body / card_fg — 9.1:1
    "ink_3":        "#475569",   # secondary — 7.0:1
    "ink_4":        "#64748B",   # muted — 5.0:1
}

# Inter only. Numerics get tabular-nums via font-variant-numeric.
_FONT_IMPORT = (
    "@import url('https://fonts.googleapis.com/css2?"
    "family=Inter:wght@300;400;500;600;700&display=swap');"
)
FONT_SANS = "'Inter', ui-sans-serif, system-ui, -apple-system, sans-serif"


# ── Global CSS injection ────────────────────────────────────────────────────

def inject_theme() -> None:
    """Inject the global stylesheet. Idempotent — call once per page render.

    Covers typography (Inter), color tokens as CSS vars, focus rings,
    tab styling, sidebar, buttons, inputs, dataframes, metrics, dividers,
    and helper classes for section_header / metric_card / badge.
    """
    p = PALETTE
    css = f"""
    <style>
    {_FONT_IMPORT}

    :root {{
      --osc-primary: {p["primary"]};
      --osc-primary-fg: {p["primary_fg"]};
      --osc-secondary: {p["secondary"]};
      --osc-accent: {p["accent"]};
      --osc-bg: {p["background"]};
      --osc-fg: {p["foreground"]};
      --osc-card: {p["card"]};
      --osc-card-fg: {p["card_fg"]};
      --osc-muted: {p["muted"]};
      --osc-muted-fg: {p["muted_fg"]};
      --osc-border: {p["border"]};
      --osc-border-strong: {p["border_strong"]};
      --osc-destructive: {p["destructive"]};
      --osc-success: {p["success"]};
      --osc-ring: {p["ring"]};
      --osc-ink-1: {p["ink_1"]};
      --osc-ink-2: {p["ink_2"]};
      --osc-ink-3: {p["ink_3"]};
      --osc-ink-4: {p["ink_4"]};
      --osc-font: {FONT_SANS};
      --osc-radius: 8px;
      --osc-radius-sm: 6px;
    }}

    /* ── Canvas ──────────────────────────────────────────────────────── */
    html, body, [data-testid="stAppViewContainer"] {{
      background-color: var(--osc-bg);
      color: var(--osc-fg);
      font-family: var(--osc-font);
    }}
    [data-testid="stHeader"] {{
      background: rgba(248, 250, 252, 0.88);
      backdrop-filter: saturate(180%) blur(10px);
      border-bottom: 1px solid var(--osc-border);
    }}

    /* ── Typography ──────────────────────────────────────────────────── */
    h1, h2, h3, h4, h5, h6 {{
      font-family: var(--osc-font);
      color: var(--osc-ink-1);
      letter-spacing: -0.012em;
      font-weight: 600;
    }}
    h1 {{ font-size: 1.75rem; line-height: 1.15; }}
    h2 {{ font-size: 1.375rem; line-height: 1.25; }}
    h3 {{ font-size: 1.125rem; line-height: 1.3; }}
    .stMarkdown p, .stMarkdown li, .stMarkdown {{
      color: var(--osc-ink-2);
      font-size: 0.92rem;
      line-height: 1.55;
    }}
    .stCaption, [data-testid="stCaptionContainer"],
    small, [data-testid="stCaption"] {{
      color: var(--osc-ink-3);
      font-size: 0.78rem;
    }}

    /* ── Sidebar ─────────────────────────────────────────────────────── */
    [data-testid="stSidebar"] {{
      background-color: var(--osc-card);
      border-right: 1px solid var(--osc-border);
    }}
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {{
      font-size: 0.85rem;
    }}
    [data-testid="stSidebar"] hr {{
      border-color: var(--osc-border);
      margin: 0.5rem 0 !important;
    }}

    /* ── Tabs ────────────────────────────────────────────────────────── */
    [data-testid="stTabs"] [role="tablist"] {{
      gap: 0;
      border-bottom: 1px solid var(--osc-border);
      background: transparent;
    }}
    [data-testid="stTabs"] [role="tab"] {{
      padding: 0.65rem 1.1rem !important;
      border-radius: 0 !important;
      background: transparent !important;
      border-bottom: 2px solid transparent !important;
      color: var(--osc-ink-3) !important;
      font-weight: 500;
      font-size: 0.9rem;
    }}
    @media (prefers-reduced-motion: no-preference) {{
      [data-testid="stTabs"] [role="tab"] {{
        transition: color 120ms ease, border-color 120ms ease;
      }}
    }}
    [data-testid="stTabs"] [role="tab"]:hover {{
      color: var(--osc-primary) !important;
    }}
    [data-testid="stTabs"] [role="tab"][aria-selected="true"] {{
      color: var(--osc-primary) !important;
      border-bottom-color: var(--osc-primary) !important;
      font-weight: 600;
    }}
    [data-testid="stTabs"] [role="tab"] p {{
      font-weight: inherit !important;
      font-size: inherit !important;
    }}

    /* ── Focus rings (restyled, never removed) ──────────────────────── */
    [data-testid="stTextInput"] input:focus,
    [data-testid="stNumberInput"] input:focus,
    [data-baseweb="input"] input:focus,
    [data-baseweb="select"] > div:focus-within,
    .stButton > button:focus-visible,
    .stDownloadButton > button:focus-visible,
    [data-testid="stTabs"] [role="tab"]:focus-visible {{
      outline: none !important;
      box-shadow: 0 0 0 3px rgba(30, 64, 175, 0.30) !important;
      border-color: var(--osc-ring) !important;
    }}

    /* ── Buttons ─────────────────────────────────────────────────────── */
    .stButton > button, .stDownloadButton > button {{
      font-family: var(--osc-font);
      font-weight: 500;
      font-size: 0.875rem;
      border-radius: var(--osc-radius);
      border: 1px solid var(--osc-border-strong);
      background: var(--osc-card);
      color: var(--osc-ink-2);
      padding: 0.5rem 0.95rem;
      min-height: 44px;       /* touch target */
    }}
    @media (prefers-reduced-motion: no-preference) {{
      .stButton > button, .stDownloadButton > button {{
        transition: background 120ms ease, border-color 120ms ease,
                    color 120ms ease;
      }}
    }}
    .stButton > button:hover, .stDownloadButton > button:hover {{
      border-color: var(--osc-primary);
      color: var(--osc-primary);
      background: var(--osc-card);
    }}
    .stButton > button[kind="primary"],
    button[data-testid="stBaseButton-primary"] {{
      background: var(--osc-primary) !important;
      color: var(--osc-primary-fg) !important;
      border-color: var(--osc-primary) !important;
      font-weight: 600;
    }}
    .stButton > button[kind="primary"]:hover,
    button[data-testid="stBaseButton-primary"]:hover {{
      background: #1d3a9c !important;
      border-color: #1d3a9c !important;
      color: var(--osc-primary-fg) !important;
    }}
    .stButton > button[kind="primary"] p,
    button[data-testid="stBaseButton-primary"] p {{
      color: var(--osc-primary-fg) !important;
    }}

    /* ── Inputs ──────────────────────────────────────────────────────── */
    [data-testid="stTextInput"] input,
    [data-testid="stNumberInput"] input,
    [data-baseweb="select"] > div,
    [data-baseweb="input"] > div {{
      background: var(--osc-card) !important;
      border-color: var(--osc-border-strong) !important;
      border-radius: var(--osc-radius-sm) !important;
      font-family: var(--osc-font);
      color: var(--osc-ink-1);
    }}
    [data-testid="stNumberInput"] input,
    [data-testid="stTextInput"] input {{
      font-variant-numeric: tabular-nums;
    }}
    [data-testid="stWidgetLabel"] p,
    [data-testid="stWidgetLabel"] label {{
      font-size: 0.74rem !important;
      font-weight: 500 !important;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--osc-ink-3) !important;
    }}

    /* ── Containers / borders ───────────────────────────────────────── */
    [data-testid="stVerticalBlockBorderWrapper"] {{
      border-radius: var(--osc-radius) !important;
      border-color: var(--osc-border) !important;
      background: var(--osc-card) !important;
    }}

    /* ── Dividers ───────────────────────────────────────────────────── */
    [data-testid="stDivider"] hr {{
      border-color: var(--osc-border) !important;
      margin: 0.4rem 0 !important;
    }}

    /* ── Metrics (st.metric) ────────────────────────────────────────── */
    [data-testid="stMetric"] {{
      background: var(--osc-card);
      border: 1px solid var(--osc-border);
      border-radius: var(--osc-radius);
      padding: 0.85rem 1rem;
      min-height: 88px;       /* reserve space → no CLS */
    }}
    [data-testid="stMetricLabel"] p {{
      font-size: 0.7rem !important;
      font-weight: 500 !important;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--osc-ink-3) !important;
    }}
    [data-testid="stMetricValue"] {{
      font-family: var(--osc-font) !important;
      font-size: 1.5rem !important;
      font-weight: 600 !important;
      color: var(--osc-ink-1) !important;
      letter-spacing: -0.01em;
      font-variant-numeric: tabular-nums;
    }}
    [data-testid="stMetricDelta"] {{
      font-family: var(--osc-font) !important;
      font-size: 0.78rem !important;
      font-variant-numeric: tabular-nums;
    }}

    /* ── DataFrame ──────────────────────────────────────────────────── */
    [data-testid="stDataFrame"] {{
      border: 1px solid var(--osc-border);
      border-radius: var(--osc-radius);
      overflow: hidden;
      background: var(--osc-card);
    }}
    [data-testid="stDataFrame"] [data-testid="stDataFrameResizable"] {{
      font-family: var(--osc-font);
      font-size: 0.84rem;
      font-variant-numeric: tabular-nums;
    }}

    /* ── Alerts ─────────────────────────────────────────────────────── */
    [data-testid="stAlert"] {{
      border-radius: var(--osc-radius);
      border: 1px solid var(--osc-border);
      font-size: 0.88rem;
    }}

    /* ── Expander ───────────────────────────────────────────────────── */
    [data-testid="stExpander"] {{
      border: 1px solid var(--osc-border);
      border-radius: var(--osc-radius);
      background: var(--osc-card);
    }}
    [data-testid="stExpander"] summary {{
      font-weight: 500;
      color: var(--osc-ink-2);
    }}

    /* ── Block container (page padding) ─────────────────────────────── */
    .block-container {{
      padding-top: 1.25rem !important;
      padding-left: 1.5rem !important;
      padding-right: 1.5rem !important;
      max-width: 100% !important;
    }}

    /* ── Custom helpers ─────────────────────────────────────────────── */
    .osc-wordmark {{
      display: flex;
      align-items: baseline;
      gap: 0.45rem;
      font-family: var(--osc-font);
    }}
    .osc-wordmark-brand {{
      font-weight: 700;
      letter-spacing: -0.02em;
      font-size: 1.15rem;
      color: var(--osc-ink-1);
    }}
    .osc-wordmark-sub {{
      font-size: 0.72rem;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--osc-ink-3);
      font-weight: 500;
    }}
    .osc-wordmark-dot {{
      width: 8px; height: 8px; border-radius: 50%;
      background: var(--osc-primary);
      display: inline-block;
      margin: 0 0.15rem 0.1rem 0;
    }}

    .osc-disclaimer {{
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      font-size: 0.7rem;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--osc-ink-3);
      padding: 0.3rem 0.6rem;
      border: 1px solid var(--osc-border);
      border-radius: 999px;
      background: var(--osc-card);
      font-weight: 500;
    }}
    .osc-disclaimer-dot {{
      width: 6px; height: 6px; border-radius: 50%;
      background: var(--osc-accent);
    }}

    .osc-section-title {{
      font-family: var(--osc-font);
      font-weight: 600;
      font-size: 1.05rem;
      color: var(--osc-ink-1);
      letter-spacing: -0.008em;
      margin: 0;
    }}
    .osc-section-sub {{
      font-family: var(--osc-font);
      font-size: 0.82rem;
      color: var(--osc-ink-3);
      margin: 0.15rem 0 0 0;
      line-height: 1.4;
    }}
    .osc-section-eyebrow {{
      font-family: var(--osc-font);
      font-size: 0.66rem;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      color: var(--osc-muted-fg);
      margin: 0 0 0.2rem 0;
      font-weight: 600;
    }}

    .osc-card {{
      background: var(--osc-card);
      border: 1px solid var(--osc-border);
      border-radius: var(--osc-radius);
      padding: 0.85rem 1rem;
      min-height: 88px;
    }}
    .osc-card-label {{
      font-size: 0.66rem;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--osc-ink-3);
      margin: 0 0 0.35rem 0;
      font-weight: 600;
    }}
    .osc-card-value {{
      font-size: 1.55rem;
      font-weight: 600;
      color: var(--osc-ink-1);
      letter-spacing: -0.012em;
      line-height: 1.1;
      margin: 0;
      font-variant-numeric: tabular-nums;
    }}
    .osc-card-delta {{
      font-size: 0.78rem;
      margin: 0.35rem 0 0 0;
      letter-spacing: 0.01em;
      font-variant-numeric: tabular-nums;
      font-weight: 500;
    }}
    .osc-card-delta-pos {{ color: var(--osc-success); }}
    .osc-card-delta-neg {{ color: var(--osc-destructive); }}
    .osc-card-delta-neutral {{ color: var(--osc-ink-3); }}
    .osc-card-help {{
      font-size: 0.74rem;
      color: var(--osc-ink-3);
      margin: 0.3rem 0 0 0;
      line-height: 1.35;
    }}

    .osc-badge {{
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      font-size: 0.7rem;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      padding: 0.18rem 0.6rem;
      border-radius: 999px;
      border: 1px solid transparent;
      line-height: 1.4;
      font-weight: 600;
      font-variant-numeric: tabular-nums;
    }}
    .osc-badge-neutral {{
      background: var(--osc-muted);
      color: var(--osc-ink-3);
      border-color: var(--osc-border);
    }}
    .osc-badge-positive {{
      background: rgba(5, 150, 105, 0.10);
      color: var(--osc-success);
      border-color: rgba(5, 150, 105, 0.25);
    }}
    .osc-badge-negative {{
      background: rgba(220, 38, 38, 0.08);
      color: var(--osc-destructive);
      border-color: rgba(220, 38, 38, 0.22);
    }}
    .osc-badge-warn {{
      background: rgba(217, 119, 6, 0.10);
      color: var(--osc-accent);
      border-color: rgba(217, 119, 6, 0.22);
    }}
    .osc-badge-info {{
      background: rgba(30, 64, 175, 0.08);
      color: var(--osc-primary);
      border-color: rgba(30, 64, 175, 0.20);
    }}
    .osc-badge-accent {{
      background: rgba(217, 119, 6, 0.10);
      color: var(--osc-accent);
      border-color: rgba(217, 119, 6, 0.22);
    }}

    .osc-footer {{
      margin-top: 2.5rem;
      padding: 1.25rem 0 0.5rem 0;
      border-top: 1px solid var(--osc-border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      font-size: 0.7rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--osc-ink-3);
      font-weight: 500;
    }}
    .osc-footer a {{
      color: var(--osc-ink-3);
      text-decoration: none;
      border-bottom: 1px dotted var(--osc-ink-4);
    }}
    .osc-footer a:hover {{ color: var(--osc-primary); border-bottom-color: var(--osc-primary); }}

    /* Empty / loading placeholder */
    .osc-empty {{
      background: var(--osc-card);
      border: 1px dashed var(--osc-border-strong);
      border-radius: var(--osc-radius);
      padding: 2.25rem 1.5rem;
      text-align: center;
      color: var(--osc-ink-3);
      min-height: 140px;
    }}
    .osc-empty-title {{
      font-family: var(--osc-font);
      font-weight: 600;
      color: var(--osc-ink-2);
      font-size: 0.95rem;
      margin: 0 0 0.25rem 0;
    }}
    .osc-empty-sub {{ font-size: 0.82rem; margin: 0; }}

    /* Streamlit spinner caption color */
    [data-testid="stSpinner"] > div > div {{
      color: var(--osc-ink-2) !important;
      font-family: var(--osc-font);
    }}

    /* Sidebar collapse / expand buttons */
    [data-testid="stSidebarCollapseButton"] button,
    button[data-testid="stExpandSidebarButton"] {{
      background: var(--osc-card) !important;
      border: 1px solid var(--osc-border-strong) !important;
      border-radius: var(--osc-radius-sm) !important;
      box-shadow: 0 1px 3px rgba(15,23,42,0.08) !important;
    }}
    [data-testid="stSidebarCollapseButton"] *,
    button[data-testid="stExpandSidebarButton"] * {{
      color: var(--osc-primary) !important;
    }}

    /* Honor reduced motion globally for any future transitions */
    @media (prefers-reduced-motion: reduce) {{
      *, *::before, *::after {{
        animation-duration: 0.001ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.001ms !important;
      }}
    }}
    </style>
    """
    st.html(css)


# ── Reusable rendering helpers ──────────────────────────────────────────────

def section_header(
    title: str,
    subtitle: str | None = None,
    eyebrow: str | None = None,
) -> None:
    """Render a refined section heading.

    Args:
        title: Main heading text.
        subtitle: Optional explanatory line beneath the heading.
        eyebrow: Optional small uppercased lead-in (e.g. "Step 01").
    """
    eyebrow_html = (
        f"<p class='osc-section-eyebrow'>{eyebrow}</p>" if eyebrow else ""
    )
    sub_html = (
        f"<p class='osc-section-sub'>{subtitle}</p>" if subtitle else ""
    )
    st.markdown(
        f"<div style='margin: 0.25rem 0 0.65rem 0;'>"
        f"{eyebrow_html}"
        f"<h3 class='osc-section-title'>{title}</h3>"
        f"{sub_html}"
        f"</div>",
        unsafe_allow_html=True,
    )


_BADGE_VARIANTS = {"neutral", "positive", "negative", "warn", "info", "accent"}


def badge(
    text: str,
    variant: Literal[
        "neutral", "positive", "negative", "warn", "info", "accent"
    ] = "neutral",
) -> str:
    """Return an inline HTML pill. Render via st.markdown(..., unsafe_allow_html=True).

    Args:
        text: Visible label.
        variant: One of neutral / positive / negative / warn / info / accent.
            'positive' and 'negative' are reserved for sign-of-change;
            decorative use should pick 'neutral' or 'info'.
    """
    v = variant if variant in _BADGE_VARIANTS else "neutral"
    return f"<span class='osc-badge osc-badge-{v}'>{text}</span>"


def metric_card(
    label: str,
    value: str,
    delta: str | None = None,
    delta_sign: Literal["pos", "neg", "neutral"] = "neutral",
    help_text: str | None = None,
) -> None:
    """Render a refined KPI card.

    Args:
        label: Small uppercased label (e.g. "Spot Price").
        value: Main numeric/text value (tabular-nums).
        delta: Optional change indicator string.
        delta_sign: pos → success green, neg → destructive red, neutral → gray.
            Only use pos/neg for genuine signs of change, never decoratively.
        help_text: Optional smaller description below the value.
    """
    delta_html = ""
    if delta:
        cls = {
            "pos":     "osc-card-delta-pos",
            "neg":     "osc-card-delta-neg",
            "neutral": "osc-card-delta-neutral",
        }.get(delta_sign, "osc-card-delta-neutral")
        arrow = ""
        if delta_sign == "pos":
            arrow = "<span aria-hidden='true'>▲</span> "
        elif delta_sign == "neg":
            arrow = "<span aria-hidden='true'>▼</span> "
        delta_html = f"<p class='osc-card-delta {cls}'>{arrow}{delta}</p>"
    help_html = (
        f"<p class='osc-card-help'>{help_text}</p>" if help_text else ""
    )
    st.markdown(
        f"<div class='osc-card'>"
        f"<p class='osc-card-label'>{label}</p>"
        f"<p class='osc-card-value'>{value}</p>"
        f"{delta_html}"
        f"{help_html}"
        f"</div>",
        unsafe_allow_html=True,
    )


def wordmark(brand: str = "STOCKPILE", suffix: str = "OPTIONS SCANNER") -> None:
    """Render the app's wordmark — primary-blue dot + name + tracked suffix."""
    st.markdown(
        f"<div class='osc-wordmark'>"
        f"<span class='osc-wordmark-dot' aria-hidden='true'></span>"
        f"<span class='osc-wordmark-brand'>{brand}</span>"
        f"<span class='osc-wordmark-sub'>· {suffix}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )


def disclaimer_chip(
    text: str = "Research tool · Not investment advice",
) -> str:
    """Return the disclaimer chip HTML (call from within a flex container)."""
    return (
        f"<span class='osc-disclaimer'>"
        f"<span class='osc-disclaimer-dot' aria-hidden='true'></span>{text}</span>"
    )


def empty_state(title: str, subtitle: str = "") -> None:
    """Render a friendlier placeholder than st.info for empty result panels."""
    sub_html = f"<p class='osc-empty-sub'>{subtitle}</p>" if subtitle else ""
    st.markdown(
        f"<div class='osc-empty'>"
        f"<p class='osc-empty-title'>{title}</p>"
        f"{sub_html}"
        f"</div>",
        unsafe_allow_html=True,
    )


def footer(version: str = "") -> None:
    """Render the page footer with brand + meta links."""
    v = f" · v{version}" if version else ""
    st.markdown(
        f"<div class='osc-footer'>"
        f"<span>STOCKPILE · OPTIONS SCANNER{v}</span>"
        f"<span>Data: Yahoo Finance · Schwab API · Built for clarity</span>"
        f"</div>",
        unsafe_allow_html=True,
    )


# ── Altair theme ────────────────────────────────────────────────────────────

def altair_theme() -> dict:
    """Return the Altair config dict — register with `alt.theme.register(...)`.

    Tightens the default chart aesthetic to the Analytics Dashboard palette:
    Inter labels, hairline axes, ample padding, tabular numerics, blue +
    amber as the primary palette and semantic red/green reserved for
    diverging signal.
    """
    p = PALETTE
    return {
        "config": {
            "background": p["card"],
            "padding": {"left": 6, "right": 12, "top": 8, "bottom": 6},
            "view": {"strokeWidth": 0, "fill": p["card"]},
            "title": {
                "font": "Inter, system-ui, sans-serif",
                "fontSize": 14,
                "fontWeight": 600,
                "color": p["ink_1"],
                "anchor": "start",
                "offset": 12,
                "subtitleFont": "Inter, system-ui, sans-serif",
                "subtitleColor": p["ink_3"],
                "subtitleFontSize": 11,
            },
            "axis": {
                "labelFont": "Inter, system-ui, sans-serif",
                "labelFontSize": 11,
                "labelColor": p["ink_3"],
                "labelFontWeight": 400,
                "titleFont": "Inter, system-ui, sans-serif",
                "titleFontSize": 11,
                "titleFontWeight": 500,
                "titleColor": p["ink_2"],
                "titlePadding": 8,
                "domainColor": p["border_strong"],
                "tickColor": p["border_strong"],
                "gridColor": p["border"],
                "gridDash": [2, 3],
                "gridOpacity": 0.7,
            },
            "legend": {
                "labelFont": "Inter, system-ui, sans-serif",
                "labelFontSize": 11,
                "labelColor": p["ink_2"],
                "titleFont": "Inter, system-ui, sans-serif",
                "titleFontSize": 11,
                "titleColor": p["ink_3"],
                "titleFontWeight": 500,
                "padding": 4,
                "symbolSize": 80,
            },
            "header": {
                "labelFont": "Inter, system-ui, sans-serif",
                "titleFont": "Inter, system-ui, sans-serif",
                "labelColor": p["ink_2"],
                "titleColor": p["ink_1"],
            },
            "range": {
                "category": [
                    p["primary"], p["accent"], p["secondary"],
                    p["success"], p["destructive"], p["muted_fg"],
                ],
                "diverging": [p["destructive"], "#CBD5E1", p["success"]],
            },
            "bar": {"fill": p["primary"]},
            "line": {"strokeWidth": 2, "color": p["primary"]},
            "circle": {"size": 60, "fill": p["primary"]},
            "point": {"size": 60, "filled": True, "fill": p["primary"]},
        }
    }


def register_altair_theme() -> None:
    """Register and enable the theme with Altair, version-safe.

    Altair 6 introduced a decorator-style `alt.theme.register(name, *, enable)`
    that returns a wrapper, while Altair 5 used a positional callable
    registration. Try the modern API first and fall back gracefully.
    """
    import altair as alt
    name = "stockpile_minimal"
    theme_dict = altair_theme()
    try:
        # Altair 6: decorator returning a registered theme.
        decorator = alt.theme.register(name, enable=True)
        decorator(lambda: theme_dict)
    except TypeError:
        try:
            # Altair 5 style
            alt.themes.register(name, lambda: theme_dict)
            alt.themes.enable(name)
        except AttributeError:
            pass
