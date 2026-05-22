"""Chain-table cell styling and tooltip text.

Shared by the per-expiration chain view (`show_chain_table`) and the
ranked scan-results table (`show_df`). Two coupled concerns live
here:

1. The yellow warning highlight (`CELL_WARN`) used to flag wide
   spreads, low OI, and low daily volume rows.
2. The hover-help tooltips for the Bid/Ask, OI, Vol, and IV+pp
   column headers. `ivpp_help_for` is a small factory because the
   sign convention flips for buyers vs sellers — surfacing that in
   the tooltip itself saves users from having to remember it.
"""

from __future__ import annotations


CELL_WARN = "background-color: rgba(234,179,8,0.45)"

BID_HELP = ("Yellow: spread is wider than 1.5× the median for this table"
            " — higher execution cost.")

OI_HELP = ("Yellow: OI is below 2× the min OI filter"
           " — limited liquidity, harder to fill at a good price.")

VOL_HELP = "Yellow: fewer than 4 contracts traded today — very thin activity."


def ivpp_help_for(buy: bool, opt_type: str = "option") -> str:
    """Tooltip text for the IV+pp column, tailored to the user's scan.

    The number's sign is interpreted opposite for sellers vs buyers —
    a +5 pp call is great if you're SELLING it (rich premium
    collected) and bad if you're BUYING it (paying above the surface).
    The tooltip switches accordingly so the user doesn't have to
    remember the convention.
    """
    plural = {"call": "calls", "put": "puts", "both": "options"}.get(
        opt_type.lower(), "options"
    )
    if buy:
        # Buyer wants cheap → negative IV+pp.
        return (
            f"Percentage points the option's IV sits ABOVE (+) or BELOW (−)"
            f" the fitted volatility surface. You're BUYING {plural} — you want"
            f" NEGATIVE values (the option is cheap relative to its peers, so"
            f" you pay less than the surface implies). Look for −3 pp or lower;"
            f" near 0 sits on the surface; positive means you're paying above it."
        )
    # Seller wants rich → positive IV+pp.
    return (
        f"Percentage points the option's IV sits ABOVE (+) or BELOW (−)"
        f" the fitted volatility surface. You're SELLING {plural} — you want"
        f" POSITIVE values (the option is rich relative to its peers, so you"
        f" collect more than fair). Look for +5 pp or higher; under +3 pp is"
        f" noise; negative means the chain isn't paying a premium."
    )
