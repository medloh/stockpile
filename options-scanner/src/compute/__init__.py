"""Pure compute helpers used by the Streamlit tabs.

Functions in this package are NumPy/pandas only — no Streamlit
imports, no session-state reads, no I/O. They take DataFrames and
scalars in, return DataFrames / dicts / primitives out. This keeps
them callable from notebooks, tests, and (eventually) a CLI agent
path without booting Streamlit.
"""
