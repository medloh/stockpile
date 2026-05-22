"""Options scanner — IV-surface ranking, GEX, spreads, and portfolio scans.

Internal layering:

    compute/   pure NumPy/pandas (no Streamlit)
    display/   Streamlit rendering helpers (+ display/cli.py for CLI)
    tabs/      tab orchestration (reads session_state, calls compute
               and display, wires up the scan flow)
    montecarlo/ trade simulator engine + metrics

Flat top-level modules (chain.py, spreads.py, fetch.py, iv_surface.py,
etc.) are data/math utilities consumed by all three layers.
"""
