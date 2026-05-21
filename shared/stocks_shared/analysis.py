"""Pure analysis functions — no API calls, no I/O."""

import re
from datetime import date, timedelta
from collections import defaultdict


def _norm_opt_symbol(symbol):
    """Strip adjustment-digit suffix from option ticker so adjusted symbols match originals.
    e.g. 'AMC1 12/16/2022 24.00 P' → 'AMC 12/16/2022 24.00 P'
    """
    return re.sub(r"^([A-Z]+)\d+(\s)", r"\1\2", symbol)


def detect_open_positions(transactions):
    """Return list of currently open short option positions (net contracts > 0)."""
    pos = defaultdict(lambda: {
        "contracts": 0, "premium": 0.0,
        "type": None, "strike": None, "expiration": None, "open_date": None,
    })
    for row in transactions:
        date_str, action, opt_type, symbol, strike, expiration, qty, _, _, amount, _ = row
        if opt_type == "Stock" or qty == "":
            continue
        key = _norm_opt_symbol(symbol)
        p = pos[key]
        p["type"] = opt_type
        p["strike"] = strike
        p["expiration"] = expiration
        amt = float(amount) if amount != "" else 0.0
        q = int(qty)
        if action in ("Sell to Open", "Buy to Open"):
            if p["contracts"] == 0:
                p["open_date"] = date_str  # record date of first open
            p["contracts"] += q
            p["premium"] += amt
        elif action in ("Buy to Close", "Sell to Close", "Expired", "Assigned",
                        "Exercised"):
            p["contracts"] -= q
            p["premium"] += amt
            if p["contracts"] == 0:
                p["open_date"] = None  # reset if fully closed

    return [{"symbol": s, **v} for s, v in pos.items() if v["contracts"] > 0]


def detect_suspicious_positions(transactions, open_positions):
    """Return list of warning strings for open positions that look suspicious.

    Three signals:
    1. Option expiration date has already passed (most reliable).
    2. Open call but net share count is 0 — shares were sold or called away.
    3. Open put where a stock buy at the put strike occurred near expiration —
       likely an unrecorded assignment.
    """
    if not open_positions:
        return []

    today = date.today()
    warnings = []

    net_shares = 0
    stock_buys = []  # [(date_obj, price_per_share), ...]
    for row in transactions:
        date_str, action, opt_type, _, _, _, qty, price, _, _, _ = row
        if opt_type != "Stock" or qty == "":
            continue
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", date_str)
        if not m:
            continue
        q = int(qty)
        d = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        if action == "Buy":
            net_shares += q
            if price != "":
                stock_buys.append((d, float(price)))
        elif action == "Sell":
            net_shares -= q

    for pos in open_positions:
        sym      = pos["symbol"]
        exp_str  = pos["expiration"] or ""
        opt_type = pos["type"]
        strike   = pos["strike"]

        exp_date = None
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", exp_str)
        if m:
            exp_date = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))

        # Signal 1: expiration already passed
        if exp_date and exp_date < today:
            warnings.append(
                f"open {opt_type} {sym} expired {exp_str} — "
                "check for missing Assigned or Expired transaction"
            )
            continue

        # Signal 2: open call but no shares held
        if opt_type == "Call" and net_shares <= 0:
            warnings.append(
                f"open {opt_type} {sym} but no shares held — "
                "check for missing Assigned transaction"
            )

        # Signal 3: open put with stock buy at the put strike near expiration
        if opt_type == "Put" and exp_date:
            for buy_date, buy_price in stock_buys:
                if (exp_date - timedelta(days=30)) <= buy_date <= (exp_date + timedelta(days=5)):
                    if abs(buy_price - strike) < 0.01:
                        warnings.append(
                            f"open {opt_type} {sym} — stock bought at strike "
                            f"${strike:.2f} on {buy_date:%m/%d/%Y}, "
                            "check for missing Assigned transaction"
                        )
                        break

    return warnings


def get_last_option(transactions, option_type):
    """Return stats for the most recently opened option of the given type.

    Finds the last Sell/Buy-to-Open for the given type, locates its close
    transaction, and returns the holding period plus key fields.
    Returns None if no such option exists in the transaction history.
    """
    opens = [
        row for row in transactions
        if row[2] == option_type
        and row[1] in ("Sell to Open", "Buy to Open")
        and row[6] != ""
    ]
    if not opens:
        return None

    # transactions are chronologically sorted; last entry is the most recent open
    last       = opens[-1]
    open_date  = last[0]
    symbol     = last[3]
    strike     = last[4]
    expiration = last[5]
    contracts  = int(last[6])
    premium    = round(float(last[9]), 2) if last[9] != "" else 0.0

    _CLOSE_ACTIONS = {"Buy to Close", "Sell to Close", "Expired", "Assigned",
                      "Exercised"}
    close_rows = sorted(
        (row[0], row[1]) for row in transactions
        if _norm_opt_symbol(row[3]) == _norm_opt_symbol(symbol)
        and row[1] in _CLOSE_ACTIONS
    )
    close_date   = close_rows[-1][0]  if close_rows else None
    close_action = close_rows[-1][1]  if close_rows else None

    def _parse(s):
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s or "")
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2))) if m else None

    days_open = None
    if open_date and close_date:
        od, cd = _parse(open_date), _parse(close_date)
        if od and cd:
            days_open = (cd - od).days

    # Disposition and ITM/OTM at close
    if close_action == "Assigned":
        disposition  = "Assigned"
        itm_at_close = True
    elif close_action == "Exercised":
        disposition  = "Exercised"
        itm_at_close = True
    elif close_action == "Expired":
        disposition  = "Expired"
        itm_at_close = False
    elif close_action in ("Buy to Close", "Sell to Close"):
        exp_date = _parse(expiration)
        cl_date  = _parse(close_date) if close_date else None
        if exp_date and cl_date and cl_date >= exp_date:
            disposition = "Closed at expiration"
        else:
            disposition = "Closed early"
        itm_at_close = None  # requires stock price at close; filled by caller
    else:
        disposition  = ""
        itm_at_close = None

    # Roll count: BTC transactions whose date matches a same-day STO of the same type
    sto_dates = {row[0] for row in transactions
                 if row[2] == option_type and row[1] in ("Sell to Open", "Buy to Open")}
    roll_count = sum(
        1 for row in transactions
        if row[2] == option_type
        and row[1] in ("Buy to Close", "Sell to Close")
        and row[0] in sto_dates
    )

    return {
        "strike":        strike,
        "expiration":    expiration,
        "open_date":     open_date,
        "close_date":    close_date,
        "days_open":     days_open,
        "contracts":     contracts,
        "premium":       premium,
        "disposition":   disposition,
        "itm_at_close":  itm_at_close,
        "roll_count":    roll_count,
        "price_at_open": None,  # filled by caller
        "price_at_close": None, # filled by caller for BTC cases
    }


def compute_avg_held_anchor(transactions):
    """Return (year, month, day) of the share-weighted average acquisition date
    for currently-held shares, or None if no shares are held.

    Stock Sell rows consume lots FIFO — so shares that have been sold no
    longer contribute to the weighted average.
    """
    lots = []  # [[date, shares_remaining], ...], chronological order
    for row in transactions:
        date_str, action, opt_type, _sym, _strike, _exp, qty, _, _, _, _ = row
        if opt_type != "Stock" or qty == "":
            continue
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", date_str)
        if not m:
            continue
        lot_date = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        q = int(qty)
        if action == "Buy":
            lots.append([lot_date, q])
        elif action == "Sell":
            remaining = q
            while remaining > 0 and lots:
                if lots[0][1] <= remaining:
                    remaining -= lots[0][1]
                    lots.pop(0)
                else:
                    lots[0][1] -= remaining
                    remaining = 0

    total = sum(shares for _, shares in lots)
    if total == 0:
        return None
    EPOCH = date(1899, 12, 30)
    weighted = round(sum((d - EPOCH).days * s for d, s in lots) / total)
    anchor = EPOCH + timedelta(days=weighted)
    return anchor.year, anchor.month, anchor.day


def compute_closed_avg_days(transactions):
    """Return FIFO-weighted average holding period in days across all sold lots.

    For a fully closed position this gives the actual weighted avg time each
    share was held, so annualized-yield formulas don't keep drifting as time
    passes after the close.  Returns None if no sells were matched to buys.
    """
    lots = []  # [[buy_date, shares_remaining], ...]
    total_share_days = 0
    total_shares = 0
    for row in transactions:
        date_str, action, opt_type, _sym, _strike, _exp, qty, _, _, _, _ = row
        if opt_type != "Stock" or qty == "":
            continue
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", date_str)
        if not m:
            continue
        lot_date = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        q = int(qty)
        if action == "Buy":
            lots.append([lot_date, q])
        elif action == "Sell":
            remaining = q
            while remaining > 0 and lots:
                if lots[0][1] <= remaining:
                    days = (lot_date - lots[0][0]).days
                    total_share_days += days * lots[0][1]
                    total_shares += lots[0][1]
                    remaining -= lots[0][1]
                    lots.pop(0)
                else:
                    days = (lot_date - lots[0][0]).days
                    total_share_days += days * remaining
                    total_shares += remaining
                    lots[0][1] -= remaining
                    remaining = 0
    if total_shares == 0:
        return None
    return round(total_share_days / total_shares)


def compute_status(transactions, open_positions):
    """Return (status, issues) where status is 'Closed', 'Consistent', or 'Inconsistent'.

    Checks:
    - Share count never goes negative in transaction history
    - Option contract counts never go negative for any symbol
    """
    issues = []

    if any(row[1] == "Transfer In" for row in transactions):
        issues.append("position includes a broker transfer — locate original buy transactions to resolve")

    shares = 0
    for row in transactions:
        _, action, opt_type, _sym, _, _, qty, _, _, _, _ = row
        if opt_type == "Stock" and qty != "":
            if action == "Buy":
                shares += int(qty)
            elif action == "Sell":
                shares -= int(qty)
            if shares < 0:
                issues.append("share count went negative")
                break

    option_net = defaultdict(int)
    for row in transactions:
        _, action, opt_type, symbol, _, _, qty, _, _, _, _ = row
        if opt_type in ("Call", "Put") and qty != "":
            key = _norm_opt_symbol(symbol)
            q = int(qty)
            if action in ("Sell to Open", "Buy to Open"):
                option_net[key] += q
            elif action in ("Buy to Close", "Sell to Close", "Expired", "Assigned",
                            "Exercised"):
                option_net[key] -= q
                if option_net[key] < 0:
                    issues.append(f"option contracts went negative for {key}")

    if issues:
        return "Inconsistent", issues
    if shares <= 0 and not open_positions:
        return "Closed", []
    return "Consistent", []
