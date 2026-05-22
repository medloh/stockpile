"""Streamlit rendering helpers for the options-scanner tabs.

Modules here are allowed to call Streamlit primitives (st.altair_chart,
st.dataframe, st.markdown, st.session_state reads, etc.) but should
take data as arguments rather than building it themselves — the
chain fetches, scan triggers, and tab orchestration live in
`run_app.py` and the `tabs/` package.

Layering:
    compute/   pure NumPy/pandas (no Streamlit)
    display/   this package — render data to the page
    tabs/      tab orchestration (reads session_state, calls compute
               + display, lives in `tabs/` after Phase 3)
"""
