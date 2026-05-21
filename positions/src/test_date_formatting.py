"""Tests that pin the date / datetime format invariants on the
position-tab layout.

Two bugs in one day prompted this file:
  1. Position Opened (E8) showed as a serial number (45343) instead of
     a date because no DATE format was applied.
  2. Last Updated (B6) showed as a serial number (46162.81875) because
     a CURRENCY format range was inadvertently extended to cover it.

Both happen when a cell holding a date/datetime VALUE is paired with
a non-date FORMAT. These tests assert the layout structure (the right
value goes to the right cell) and the format-request list (the right
cell gets a date format).
"""

import re

import pytest

import setup_tab
from layout import build_open_sections, build_closed_sections


# ── Helpers ───────────────────────────────────────────────────────────────────

def _stub_position(strike=200.0, expiration="2026-06-18", premium=4.50,
                   contracts=1, opt_type="Call"):
    """Minimal open-position dict used by build_open_sections."""
    return {
        "type": opt_type,
        "strike": strike,
        "expiration": expiration,
        "contracts": contracts,
        "premium": premium,
        "open_date": "2026-01-15",
        "price_at_open": 180.0,
    }


def _request_format_type(request: dict) -> str | None:
    """Extract the numberFormat.type from a Sheets API request dict.
    Returns None for requests that don't set a number format."""
    try:
        return (request["repeatCell"]["cell"]["userEnteredFormat"]
                ["numberFormat"]["type"])
    except (KeyError, TypeError):
        return None


def _request_covers_cell(request: dict, row_idx: int, col_idx: int) -> bool:
    """True if a `repeatCell` request's range contains the given
    0-indexed (row, col)."""
    try:
        rng = request["repeatCell"]["range"]
    except (KeyError, TypeError):
        return False
    return (rng["startRowIndex"] <= row_idx < rng["endRowIndex"] and
            rng["startColumnIndex"] <= col_idx < rng["endColumnIndex"])


def _effective_format_at(requests: list, row_idx: int, col_idx: int) -> str | None:
    """Walk the request list in order and return the LAST numberFormat
    type that covers the cell — mirrors how the Sheets API applies
    overlapping repeatCell requests (later writes win)."""
    fmt = None
    for r in requests:
        if _request_covers_cell(r, row_idx, col_idx):
            t = _request_format_type(r)
            if t is not None:
                fmt = t
    return fmt


# ── Layout: the right value lands in the right cell ──────────────────────────

class TestLastUpdatedLayout:
    """B6 must contain a date-time string in MM/DD/YY HH:MM format —
    that's what the DATE_TIME cell format expects to render."""

    DATETIME_RE = re.compile(r"^\d{2}/\d{2}/\d{2} \d{2}:\d{2}$")

    def test_open_section_places_last_updated_at_b6(self):
        sections = build_open_sections("AAPL", [], last_row=100)
        current_values = sections["A3:B8"]
        # current_values index 3 = display row 6 = B6
        label, value = current_values[3]
        assert label == "Last Updated"
        assert self.DATETIME_RE.match(value), \
            f"Last Updated value {value!r} should match MM/DD/YY HH:MM"

    def test_closed_section_places_last_updated_at_b6(self):
        sections = build_closed_sections("AAPL", [], last_row=100)
        current_values = sections["A3:B6"]
        label, value = current_values[3]
        assert label == "Last Updated"
        assert self.DATETIME_RE.match(value)


class TestPositionOpenedLayout:
    """E8 must contain a formula that resolves to a date — specifically
    MINIFS over the buy transactions."""

    def test_open_section_places_position_opened_at_e8(self):
        sections = build_open_sections("AAPL", [], last_row=100)
        stock_pos = sections["D3:E8"]
        # index 5 of the section = display row 8 = E8
        label, formula = stock_pos[5]
        assert label == "Position Opened"
        assert formula.startswith("="), \
            "Position Opened must be a formula, not a raw value"
        assert "MINIFS" in formula

    def test_closed_section_places_position_opened_at_e8(self):
        sections = build_closed_sections("AAPL", [], last_row=100)
        stock_pos = sections["D3:E8"]
        label, formula = stock_pos[5]
        assert label == "Position Opened"
        assert "MINIFS" in formula


class TestAdjCostBasisLayout:
    """B4 must contain the Adj Cost Basis formula — this row is paired
    with Avg Cost / Share at E4 under a blue highlight, so the cell
    can't drift back to B6 unnoticed."""

    def test_open_section_places_adj_cost_basis_at_b4(self):
        sections = build_open_sections("AAPL", [], last_row=100)
        current_values = sections["A3:B8"]
        label, formula = current_values[1]  # index 1 = display row 4 = B4
        assert label.startswith("** Adj Cost Basis")
        assert formula.startswith("=IFERROR(-SUM(J")
        # Must divide by E5 (Shares Held), not E4 (Avg Cost / Share)
        assert "/E5" in formula

    def test_closed_section_places_adj_cost_basis_at_b4(self):
        sections = build_closed_sections("AAPL", [], last_row=100)
        current_values = sections["A3:B6"]
        label, formula = current_values[1]
        assert label.startswith("** Adj Cost Basis")
        assert "/E5" in formula


class TestStockPositionLayout:
    """STOCK POSITION ordering: Avg Cost / Share at E4, Shares Held at
    E5. Total Invested and Market Value formulas depend on this order."""

    def test_avg_cost_at_e4(self):
        sections = build_open_sections("AAPL", [], last_row=100)
        stock_pos = sections["D3:E8"]
        assert stock_pos[1][0] == "Avg Cost / Share"
        assert "/E5" in stock_pos[1][1]  # divides by Shares Held at E5

    def test_shares_held_at_e5(self):
        sections = build_open_sections("AAPL", [], last_row=100)
        stock_pos = sections["D3:E8"]
        assert stock_pos[2][0] == "Shares Held"

    def test_market_value_uses_shares_at_e5(self):
        """Market Value = Shares × Stock Price = E5 × B5 (NOT E4 × B5)."""
        sections = build_open_sections("AAPL", [], last_row=100)
        stock_pos = sections["D3:E8"]
        assert stock_pos[4][0] == "Market Value"
        assert stock_pos[4][1] == "=E5*B5"


# ── Format requests: the right cell gets the right format ────────────────────

class TestDateFormats:
    """Each date/datetime cell must have a DATE or DATE_TIME format as
    its EFFECTIVE format (i.e. the last format request covering it).
    Catches the class of bug where a CURRENCY/NUMBER range silently
    overlaps a date cell."""

    # Standard layout: both calls and puts shown
    P = 19   # puts section starts at display row 19
    I = 28   # income section starts at display row 28
    TXN_ROW = 29

    SHEET_ID = 0  # arbitrary; tests don't hit the API

    @pytest.fixture
    def open_requests(self):
        return setup_tab.build_fmt_requests(
            self.SHEET_ID, "Consistent",
            self.P, self.I, self.TXN_ROW,
            show_calls=True, show_puts=True,
        )

    @pytest.fixture
    def closed_requests(self):
        return setup_tab.build_fmt_requests(
            self.SHEET_ID, "Closed",
            self.P, self.I, self.TXN_ROW,
            show_calls=True, show_puts=True,
        )

    # B6 — Last Updated
    def test_b6_last_updated_is_datetime_in_open(self, open_requests):
        assert _effective_format_at(open_requests, row_idx=5, col_idx=1) \
            == "DATE_TIME", "B6 (Last Updated) must end up as DATE_TIME"

    def test_b6_last_updated_is_datetime_in_closed(self, closed_requests):
        assert _effective_format_at(closed_requests, row_idx=5, col_idx=1) \
            == "DATE_TIME"

    # E8 — Position Opened
    def test_e8_position_opened_is_date_in_open(self, open_requests):
        assert _effective_format_at(open_requests, row_idx=7, col_idx=4) \
            == "DATE", "E8 (Position Opened) must end up as DATE"

    def test_e8_position_opened_is_date_in_closed(self, closed_requests):
        assert _effective_format_at(closed_requests, row_idx=7, col_idx=4) \
            == "DATE"

    # E13 — Date Opened in CALL section
    def test_e13_call_date_opened_is_date_in_open(self, open_requests):
        assert _effective_format_at(open_requests, row_idx=12, col_idx=4) \
            == "DATE"

    def test_e13_call_date_opened_is_date_in_closed(self, closed_requests):
        assert _effective_format_at(closed_requests, row_idx=12, col_idx=4) \
            == "DATE"

    # E14 — Date Closed in CLOSED CALL section
    def test_e14_call_date_closed_is_date_in_closed(self, closed_requests):
        assert _effective_format_at(closed_requests, row_idx=13, col_idx=4) \
            == "DATE"

    # E{p+3} = E22 — Date Opened in PUT section
    def test_put_date_opened_is_date_in_open(self, open_requests):
        # p=19, so p+3 = 22 (display), row_idx = 21
        assert _effective_format_at(open_requests, row_idx=21, col_idx=4) \
            == "DATE"

    def test_put_date_opened_is_date_in_closed(self, closed_requests):
        assert _effective_format_at(closed_requests, row_idx=21, col_idx=4) \
            == "DATE"

    # E{p+4} = E23 — Date Closed in CLOSED PUT section
    def test_put_date_closed_is_date_in_closed(self, closed_requests):
        assert _effective_format_at(closed_requests, row_idx=22, col_idx=4) \
            == "DATE"


# ── Format requests: non-date cells don't accidentally inherit DATE ──────────

class TestCurrencyCellsNotDateFormatted:
    """Sanity in the other direction — if we ever extend a date range
    onto a currency cell, the test should catch it."""

    P = 19
    I = 28
    TXN_ROW = 29
    SHEET_ID = 0

    @pytest.fixture
    def open_requests(self):
        return setup_tab.build_fmt_requests(
            self.SHEET_ID, "Consistent",
            self.P, self.I, self.TXN_ROW,
            show_calls=True, show_puts=True,
        )

    def test_b4_adj_cost_basis_is_currency(self, open_requests):
        assert _effective_format_at(open_requests, row_idx=3, col_idx=1) \
            == "CURRENCY"

    def test_b5_stock_price_is_currency(self, open_requests):
        assert _effective_format_at(open_requests, row_idx=4, col_idx=1) \
            == "CURRENCY"

    def test_e4_avg_cost_per_share_is_currency(self, open_requests):
        assert _effective_format_at(open_requests, row_idx=3, col_idx=4) \
            == "CURRENCY"

    def test_e5_shares_held_is_plain_number(self, open_requests):
        assert _effective_format_at(open_requests, row_idx=4, col_idx=4) \
            == "NUMBER"

    def test_e6_total_invested_is_currency(self, open_requests):
        assert _effective_format_at(open_requests, row_idx=5, col_idx=4) \
            == "CURRENCY"

    def test_e7_market_value_is_currency(self, open_requests):
        assert _effective_format_at(open_requests, row_idx=6, col_idx=4) \
            == "CURRENCY"
