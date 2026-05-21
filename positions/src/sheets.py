"""Google Sheets API helpers, formatting, and tab management."""

import time
from pathlib import Path

# ── Module-level state set by configure() ─────────────────────────────────────

SPREADSHEET_ID: str | None = None
CREDS_PATH: Path | None = None
TOKEN_PATH: Path | None = None
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def configure(spreadsheet_id: str, creds_path: Path, token_path: Path) -> None:
    global SPREADSHEET_ID, CREDS_PATH, TOKEN_PATH
    SPREADSHEET_ID = spreadsheet_id
    CREDS_PATH = creds_path
    TOKEN_PATH = token_path


# ── Auth ──────────────────────────────────────────────────────────────────────

def get_service():
    from google.oauth2.credentials import Credentials
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    def _run_flow():
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
        creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json())
        return creds

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                TOKEN_PATH.write_text(creds.to_json())
            except RefreshError:
                print("  Token expired or revoked — re-authenticating...")
                TOKEN_PATH.unlink(missing_ok=True)
                creds = _run_flow()
        else:
            creds = _run_flow()
    return build("sheets", "v4", credentials=creds)


# ── Core API helpers ──────────────────────────────────────────────────────────

def _execute_with_retry(request, max_retries=5):
    import socket
    delay = 10
    for attempt in range(max_retries):
        try:
            return request.execute()
        except Exception as e:
            is_rate_limit = hasattr(e, "resp") and e.resp.status == 429
            is_transient = isinstance(e, (ConnectionResetError, ConnectionError,
                                          TimeoutError, socket.error))
            if (is_rate_limit or is_transient) and attempt < max_retries - 1:
                reason = "Rate limited" if is_rate_limit else "Connection error"
                print(f"  {reason}, retrying in {delay}s... ({e})")
                time.sleep(delay)
                delay *= 2
            else:
                raise


def get_sheet_id(service, tab_name):
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    for s in meta["sheets"]:
        if s["properties"]["title"] == tab_name:
            return s["properties"]["sheetId"]
    return None


def write_range(service, sheet_name, rng, values):
    _execute_with_retry(service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{sheet_name}!{rng}",
        valueInputOption="USER_ENTERED",
        body={"values": values},
    ))


def batch_write(service, sheet_name, ranges_dict):
    data = [
        {"range": f"{sheet_name}!{rng}", "values": vals}
        for rng, vals in ranges_dict.items()
    ]
    _execute_with_retry(service.spreadsheets().values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ))


def apply_fmt(service, sheet_id, requests):
    _execute_with_retry(service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"requests": requests},
    ))


def recreate_tab(service, tab_name):
    """Delete tab if it exists, then create it fresh. Returns new sheet_id."""
    found_id = get_sheet_id(service, tab_name)
    requests = []
    if found_id is not None:
        requests.append({"deleteSheet": {"sheetId": found_id}})
    requests.append({"addSheet": {"properties": {"title": tab_name}}})
    _execute_with_retry(service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID, body={"requests": requests}
    ))
    return get_sheet_id(service, tab_name)


# ── Formatting helpers ────────────────────────────────────────────────────────

def fmt_range(sheet_id, r1, c1, r2, c2, fmt_type, pattern):
    return {"repeatCell": {
        "range": {"sheetId": sheet_id,
                  "startRowIndex": r1, "endRowIndex": r2,
                  "startColumnIndex": c1, "endColumnIndex": c2},
        "cell": {"userEnteredFormat": {"numberFormat": {"type": fmt_type, "pattern": pattern}}},
        "fields": "userEnteredFormat.numberFormat",
    }}

def currency(sheet_id, r1, c1, r2, c2):
    return fmt_range(sheet_id, r1, c1, r2, c2, "CURRENCY", "$#,##0.00;[RED]-$#,##0.00")

def percent(sheet_id, r1, c1, r2, c2):
    return fmt_range(sheet_id, r1, c1, r2, c2, "PERCENT", "0.00%;[RED]-0.00%")

def plain_number(sheet_id, r1, c1, r2, c2):
    return fmt_range(sheet_id, r1, c1, r2, c2, "NUMBER", "#,##0")

def date_fmt(sheet_id, r1, c1, r2, c2):
    return fmt_range(sheet_id, r1, c1, r2, c2, "DATE", "MM/DD/YYYY")

def datetime_fmt(sheet_id, r1, c1, r2, c2):
    return fmt_range(sheet_id, r1, c1, r2, c2, "DATE_TIME", "MM/DD/YY HH:MM")

def right_align(sheet_id, r1, c1, r2, c2):
    return {"repeatCell": {
        "range": {"sheetId": sheet_id,
                  "startRowIndex": r1, "endRowIndex": r2,
                  "startColumnIndex": c1, "endColumnIndex": c2},
        "cell": {"userEnteredFormat": {"horizontalAlignment": "RIGHT"}},
        "fields": "userEnteredFormat.horizontalAlignment",
    }}

def red_text(sheet_id, r1, c1, r2, c2):
    return {"repeatCell": {
        "range": {"sheetId": sheet_id,
                  "startRowIndex": r1, "endRowIndex": r2,
                  "startColumnIndex": c1, "endColumnIndex": c2},
        "cell": {"userEnteredFormat": {
            "textFormat": {"foregroundColor": {"red": 0.8, "green": 0.0, "blue": 0.0}},
        }},
        "fields": "userEnteredFormat.textFormat.foregroundColor",
    }}

def green_if_positive(sheet_id, r1, c1, r2, c2):
    return {"addConditionalFormatRule": {
        "rule": {
            "ranges": [{"sheetId": sheet_id,
                        "startRowIndex": r1, "endRowIndex": r2,
                        "startColumnIndex": c1, "endColumnIndex": c2}],
            "booleanRule": {
                "condition": {"type": "NUMBER_GREATER", "values": [{"userEnteredValue": "0"}]},
                "format": {"textFormat": {"foregroundColor": {"red": 0.05, "green": 0.45, "blue": 0.13}}},
            }
        },
        "index": 0,
    }}

def yellow_bg(sheet_id, r1, c1, r2, c2):
    return {"repeatCell": {
        "range": {"sheetId": sheet_id,
                  "startRowIndex": r1, "endRowIndex": r2,
                  "startColumnIndex": c1, "endColumnIndex": c2},
        "cell": {"userEnteredFormat": {"backgroundColor": {"red": 1, "green": 0.976, "blue": 0.769}}},
        "fields": "userEnteredFormat.backgroundColor",
    }}

def light_bg(sheet_id, r1, c1, r2, c2):
    return {"repeatCell": {
        "range": {"sheetId": sheet_id,
                  "startRowIndex": r1, "endRowIndex": r2,
                  "startColumnIndex": c1, "endColumnIndex": c2},
        "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.800, "green": 0.949, "blue": 0.961}}},
        "fields": "userEnteredFormat.backgroundColor",
    }}

def section_header(sheet_id, row_idx):
    return {"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                  "startColumnIndex": 0, "endColumnIndex": 26},
        "cell": {"userEnteredFormat": {
            "backgroundColor": {"red": 0.773, "green": 0.882, "blue": 0.973},
            "textFormat": {"bold": True},
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat)",
    }}

def col_header(sheet_id, row_idx):
    return {"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                  "startColumnIndex": 0, "endColumnIndex": 26},
        "cell": {"userEnteredFormat": {
            "backgroundColor": {"red": 0.835, "green": 0.961, "blue": 0.89},
            "textFormat": {"bold": True},
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat)",
    }}

def title_row(sheet_id):
    return [
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": 0, "endColumnIndex": 26},
            "cell": {"userEnteredFormat": {
                "backgroundColor": {"red": 0.878, "green": 0.816, "blue": 0.929},
                "textFormat": {"bold": True, "fontSize": 14},
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat)",
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "ROWS",
                      "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 36},
            "fields": "pixelSize",
        }},
    ]

_STATUS_COLORS = {
    "Closed":       {"red": 0.55, "green": 0.55, "blue": 0.55},
    "Consistent":   {"red": 0.20, "green": 0.65, "blue": 0.33},
    "Inconsistent": {"red": 0.85, "green": 0.22, "blue": 0.22},
}

def status_cell_fmt(sheet_id, status):
    color = _STATUS_COLORS.get(status, _STATUS_COLORS["Consistent"])
    return {"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                  "startColumnIndex": 2, "endColumnIndex": 3},
        "cell": {"userEnteredFormat": {
            "backgroundColor": color,
            "textFormat": {"bold": True, "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}},
            "horizontalAlignment": "CENTER",
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
    }}


# ── Tab management ────────────────────────────────────────────────────────────

def clear_all_tabs(service):
    """Delete every tab in the spreadsheet, leaving only a temp placeholder."""
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    by_title = {s["properties"]["title"]: s["properties"]["sheetId"]
                for s in meta["sheets"]}

    requests = []
    if "__init__" not in by_title:
        requests.append({"addSheet": {"properties": {"title": "__init__"}}})
        ids_to_delete = list(by_title.values())
    else:
        ids_to_delete = [sid for t, sid in by_title.items() if t != "__init__"]

    for sid in ids_to_delete:
        requests.append({"deleteSheet": {"sheetId": sid}})

    if requests:
        _execute_with_retry(service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID, body={"requests": requests}
        ))
    print(f"  Cleared {len(ids_to_delete)} existing tab(s).")


def delete_placeholder(service):
    placeholder_id = get_sheet_id(service, "__init__")
    if placeholder_id is not None:
        _execute_with_retry(service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{"deleteSheet": {"sheetId": placeholder_id}}]}
        ))


def reorder_summary_tabs_first(service):
    for target_idx, tab_name in enumerate(["Summary-Open", "Summary-Closed", "Summary-Inconsistent"]):
        sid = get_sheet_id(service, tab_name)
        if sid is None:
            continue
        _execute_with_retry(service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{"updateSheetProperties": {
                "properties": {"sheetId": sid, "index": target_idx},
                "fields": "index",
            }}]}
        ))


# ── Summary tabs ──────────────────────────────────────────────────────────────

_STATUS_TAB = {
    "Consistent":   "Summary-Open",
    "Closed":       "Summary-Closed",
    "Inconsistent": "Summary-Inconsistent",
}


def _ensure_summary_tab(service, stab):
    if get_sheet_id(service, stab) is not None:
        return
    _execute_with_retry(service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"requests": [{"addSheet": {"properties": {"title": stab}}}]},
    ))
    sid = get_sheet_id(service, stab)

    if stab == "Summary-Inconsistent":
        write_range(service, stab, "A1:C1", [["Position", "Status", "Note"]])
        apply_fmt(service, sid, [col_header(sid, 0)])
        return

    def _gip(c1, c2):
        return {"addConditionalFormatRule": {"rule": {
            "ranges": [{"sheetId": sid, "startRowIndex": 2, "endRowIndex": 1000,
                        "startColumnIndex": c1, "endColumnIndex": c2}],
            "booleanRule": {
                "condition": {"type": "NUMBER_GREATER", "values": [{"userEnteredValue": "0"}]},
                "format": {"textFormat": {"foregroundColor": {"red": 0.05, "green": 0.45, "blue": 0.13}}},
            }}, "index": 0}}

    if stab == "Summary-Closed":
        # 12-column layout: A=Position, B=Stock Price, C=Mkt Val, D=Stock Gain,
        # E=All Call Results, F=All Put Results,
        # G=Dividends, H=Close-out Value, I=Avg Days Held,
        # J=Amount Invested, K=Overall P/L, L=Ann Yield
        group_row = ["Underlying Stock", "", "", "", "Calls", "Puts",
                     "Overall", "", "", "", "", ""]
        write_range(service, stab, "A1:L1", [group_row])
        headers = ["Position", "Stock\nPrice", "Underlying\nMkt Val", "Underlying\nGain",
                   "All Call\nResults", "All Put\nResults",
                   "Dividends", "Close-out\nValue", "Total Days\nHeld",
                   "Amount\nInvested", "Overall\nP/L", "Ann Yield"]
        write_range(service, stab, "A2:L2", [headers])
        apply_fmt(service, sid, [
            {"mergeCells": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 4}, "mergeType": "MERGE_ALL"}},
            {"mergeCells": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 6, "endColumnIndex": 12}, "mergeType": "MERGE_ALL"}},
            {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 4},
                "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.24, "green": 0.52, "blue": 0.78}, "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}, "horizontalAlignment": "CENTER"}},
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"}},
            {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 4, "endColumnIndex": 5},
                "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.18, "green": 0.58, "blue": 0.34}, "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}, "horizontalAlignment": "CENTER"}},
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"}},
            {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 5, "endColumnIndex": 6},
                "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.53, "green": 0.25, "blue": 0.63}, "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}, "horizontalAlignment": "CENTER"}},
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"}},
            {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 6, "endColumnIndex": 12},
                "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.30, "green": 0.30, "blue": 0.30}, "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}, "horizontalAlignment": "CENTER"}},
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"}},
            {"repeatCell": {
                "range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 0, "endColumnIndex": 12},
                "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.851, "green": 0.918, "blue": 0.957}, "textFormat": {"bold": True}, "wrapStrategy": "WRAP"}},
                "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy)"}},
            # Currency: B-H (1-7), J-K (9-10)
            {"repeatCell": {
                "range": {"sheetId": sid, "startRowIndex": 2, "endRowIndex": 1000, "startColumnIndex": 1, "endColumnIndex": 8},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00;[RED]-$#,##0.00"}}},
                "fields": "userEnteredFormat.numberFormat"}},
            {"repeatCell": {
                "range": {"sheetId": sid, "startRowIndex": 2, "endRowIndex": 1000, "startColumnIndex": 9, "endColumnIndex": 11},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00;[RED]-$#,##0.00"}}},
                "fields": "userEnteredFormat.numberFormat"}},
            # Total Days Held (I=col 8): integer
            {"repeatCell": {
                "range": {"sheetId": sid, "startRowIndex": 2, "endRowIndex": 1000, "startColumnIndex": 8, "endColumnIndex": 9},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "0"}}},
                "fields": "userEnteredFormat.numberFormat"}},
            # Ann Yield (L=col 11): percent
            {"repeatCell": {
                "range": {"sheetId": sid, "startRowIndex": 2, "endRowIndex": 1000, "startColumnIndex": 11, "endColumnIndex": 12},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}}},
                "fields": "userEnteredFormat.numberFormat"}},
            _gip(3, 4), _gip(4, 5), _gip(5, 6), _gip(6, 7), _gip(7, 8), _gip(10, 11), _gip(11, 12),
        ])
        return

    # Summary-Open: 18-column layout
    # A=Position, B=Stock Price, C=Mkt Val, D=Stock Gain,
    # E=Open Calls MV, F=Strike calls, G=Days Left calls, H=TV Ann Yield calls,
    # I=All Call Results, J=Open Puts MV, K=Strike puts, L=Days Left puts,
    # M=TV Ann Yield puts, N=All Put Results,
    # O=Dividends, P=Adj Cost Basis, Q=Close-out Value, R=Overall P/L
    group_row = ["Underlying Stock", "", "", "",
                 "Calls", "", "", "", "",
                 "Puts", "", "", "", "",
                 "Overall", "", "", ""]
    write_range(service, stab, "A1:R1", [group_row])
    headers = ["Position", "Stock\nPrice",
               "Underlying\nMkt Val", "Underlying\nGain",
               "Open\nCalls", "Strike", "Days\nLeft", "TV Ann\nYield", "All Call\nResults",
               "Open\nPuts", "Strike", "Days\nLeft", "TV Ann\nYield", "All Put\nResults",
               "Dividends", "Adj Cost\nBasis",
               "Close-out\nValue", "Overall\nP/L"]
    write_range(service, stab, "A2:R2", [headers])

    def _near_money(col_letter, col_idx):
        """Bold purple when strike is within 10% of the stock price in col B."""
        return {"addConditionalFormatRule": {"rule": {
            "ranges": [{"sheetId": sid, "startRowIndex": 2, "endRowIndex": 1000,
                        "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1}],
            "booleanRule": {
                "condition": {
                    "type": "CUSTOM_FORMULA",
                    "values": [{"userEnteredValue":
                        f"=AND({col_letter}3<>\"\",$B3<>\"\",ABS({col_letter}3-$B3)/$B3<=0.1)"}],
                },
                "format": {"textFormat": {
                    "bold": True,
                    "foregroundColor": {"red": 0.50, "green": 0.0, "blue": 0.70},
                }},
            }}, "index": 0}}

    apply_fmt(service, sid, [
        {"mergeCells": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0,  "endColumnIndex": 4},  "mergeType": "MERGE_ALL"}},
        {"mergeCells": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 4,  "endColumnIndex": 9},  "mergeType": "MERGE_ALL"}},
        {"mergeCells": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 9,  "endColumnIndex": 14}, "mergeType": "MERGE_ALL"}},
        {"mergeCells": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 14, "endColumnIndex": 18}, "mergeType": "MERGE_ALL"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 4},
            "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.24, "green": 0.52, "blue": 0.78}, "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}, "horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 4, "endColumnIndex": 9},
            "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.18, "green": 0.58, "blue": 0.34}, "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}, "horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 9, "endColumnIndex": 14},
            "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.53, "green": 0.25, "blue": 0.63}, "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}, "horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 14, "endColumnIndex": 18},
            "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.30, "green": 0.30, "blue": 0.30}, "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}, "horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"}},
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 0, "endColumnIndex": 26},
            "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.851, "green": 0.918, "blue": 0.957}, "textFormat": {"bold": True}, "wrapStrategy": "WRAP"}},
            "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy)"}},
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 2, "endRowIndex": 1000, "startColumnIndex": 1, "endColumnIndex": 18},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00;[RED]-$#,##0.00"}}},
            "fields": "userEnteredFormat.numberFormat"}},
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 2, "endRowIndex": 1000, "startColumnIndex": 6, "endColumnIndex": 7},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "0"}}},
            "fields": "userEnteredFormat.numberFormat"}},
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 2, "endRowIndex": 1000, "startColumnIndex": 7, "endColumnIndex": 8},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}}},
            "fields": "userEnteredFormat.numberFormat"}},
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 2, "endRowIndex": 1000, "startColumnIndex": 11, "endColumnIndex": 12},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "0"}}},
            "fields": "userEnteredFormat.numberFormat"}},
        {"repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 2, "endRowIndex": 1000, "startColumnIndex": 12, "endColumnIndex": 13},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}}},
            "fields": "userEnteredFormat.numberFormat"}},
        _gip(3, 4), _gip(8, 9), _gip(13, 14), _gip(14, 15), _gip(16, 17), _gip(17, 18),
        _near_money("F", 5), _near_money("K", 10),
    ])


def warning_label(sheet_id, start_row0, end_row0):
    """Orange background + bold for the suspicious-warning block on a position tab."""
    return {"repeatCell": {
        "range": {"sheetId": sheet_id,
                  "startRowIndex": start_row0, "endRowIndex": end_row0,
                  "startColumnIndex": 0, "endColumnIndex": 11},
        "cell": {"userEnteredFormat": {
            "textFormat": {"bold": True},
            "backgroundColor": {"red": 1.0, "green": 0.898, "blue": 0.6},
        }},
        "fields": "userEnteredFormat(textFormat,backgroundColor)",
    }}


def _write_summary_row(service, tab_name, status, issues,
                       show_calls=True, show_puts=True):
    stab = _STATUS_TAB[status]
    _ensure_summary_tab(service, stab)

    # Mirror layout.py build_sections row logic so cell refs stay valid when
    # call/put sections are absent and the income/returns rows shift upward.
    p = 19 if show_calls else 10
    if show_puts:
        i = p + 9
    elif show_calls:
        i = 19
    else:
        i = 10

    col_a = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"{stab}!A:A"
    ).execute().get("values", [])
    existing = [r[0] for r in col_a if r]
    row_num = existing.index(tab_name) + 1 if tab_name in existing else len(existing) + 1

    if status == "Inconsistent":
        write_range(service, stab, f"A{row_num}:C{row_num}",
                    [[tab_name, status, "; ".join(issues)]])
    elif status == "Closed":
        # 12-column layout for Summary-Closed
        # E=All Call Results (B15=Covered Call Results), F=All Put Results (B{p+5}=Put Results)
        # G=Dividends, H=Close-out Value, I=Avg Days Held,
        # J=Amount Invested (H{i+4}), K=Overall P/L, L=Ann Yield (K/J*(365/I))
        new_row = [
            tab_name,
            f"='{tab_name}'!B5",                                          # B: Stock Price
            f"='{tab_name}'!E7",                                          # C: Mkt Val
            f"='{tab_name}'!H4",                                          # D: Stock Gain
            f"='{tab_name}'!B15" if show_calls else 0,                    # E: All Call Results
            f"='{tab_name}'!B{p+5}" if show_puts else 0,                  # F: All Put Results
            f"='{tab_name}'!B{i+1}",                                      # G: Dividends
            f"='{tab_name}'!H{i+2}",                                      # H: Close-out Value
            f"='{tab_name}'!H7",                                          # I: Avg Days Held
            f"='{tab_name}'!H{i+1}",                                      # J: Amount Invested
            f"=D{row_num}+E{row_num}+F{row_num}+G{row_num}",             # K: Overall P/L
            f"=IFERROR(K{row_num}/J{row_num}*(365/I{row_num}),0)",       # L: Ann Yield
        ]
        write_range(service, stab, f"A{row_num}:L{row_num}", [new_row])
    else:
        new_row = [
            tab_name,
            f"='{tab_name}'!B5",                                         # B: Stock Price
            f"='{tab_name}'!E7",                                         # C: Mkt Val
            f"='{tab_name}'!H4",                                         # D: Stock Gain
            f"='{tab_name}'!B7",                                         # E: Calls MV
            f"='{tab_name}'!E11" if show_calls else "",                  # F: Strike (calls)
            f"='{tab_name}'!E16" if show_calls else "",                  # G: Days Left (calls)
            f"='{tab_name}'!H17" if show_calls else 0,                   # H: TV Ann Yield (calls)
            f"='{tab_name}'!B13+E{row_num}" if show_calls else 0,        # I: All Call Results
            f"='{tab_name}'!B8",                                         # J: Puts MV
            f"='{tab_name}'!E{p+1}" if show_puts else "",               # K: Strike (puts)
            f"='{tab_name}'!E{p+6}" if show_puts else "",               # L: Days Left (puts)
            f"='{tab_name}'!H{p+7}" if show_puts else 0,                # M: TV Ann Yield (puts)
            f"='{tab_name}'!B{p+3}+J{row_num}" if show_puts else 0,    # N: All Put Results
            f"='{tab_name}'!B{i+1}",                                     # O: Dividends
            f"='{tab_name}'!B4",                                         # P: Adj Cost Basis
            f"='{tab_name}'!H{i+1}",                                     # Q: Close-out Value
            f"=D{row_num}+I{row_num}+N{row_num}+O{row_num}",           # R: Overall P/L
        ]
        write_range(service, stab, f"A{row_num}:R{row_num}", [new_row])


def write_summary_totals(service, stab):
    summary_sheet_id = get_sheet_id(service, stab)
    if summary_sheet_id is None:
        return
    rows = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"{stab}!A:A"
    ).execute().get("values", [])

    data_rows = [i + 1 for i, r in enumerate(rows)
                 if r and r[0] not in ("", "Underlying Stock", "Position", "TOTALS")]

    if stab == "Summary-Open":
        meta = _execute_with_retry(service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID))
        delete_reqs = []
        for sheet in meta["sheets"]:
            if sheet["properties"]["sheetId"] == summary_sheet_id:
                for chart in sheet.get("charts", []):
                    delete_reqs.append({"deleteEmbeddedObject": {"objectId": chart["chartId"]}})
        if delete_reqs:
            _execute_with_retry(service.spreadsheets().batchUpdate(
                spreadsheetId=SPREADSHEET_ID, body={"requests": delete_reqs}
            ))

    if not data_rows:
        return
    last_data = max(data_rows)
    totals_row = last_data + 2

    if stab == "Summary-Closed":
        write_range(service, stab, f"A{last_data+1}:L{totals_row}", [[""] * 12, [""] * 12])
        total_row_data = [
            "TOTALS", "",
            f"=SUM(C3:C{last_data})", f"=SUM(D3:D{last_data})",
            f"=SUM(E3:E{last_data})", f"=SUM(F3:F{last_data})",
            f"=SUM(G3:G{last_data})", f"=SUM(H3:H{last_data})",
            "",
            f"=SUM(J3:J{last_data})",
            f"=SUM(K3:K{last_data})",
            "",
        ]
        write_range(service, stab, f"A{totals_row}:L{totals_row}", [total_row_data])
        apply_fmt(service, summary_sheet_id, [
            {"repeatCell": {
                "range": {"sheetId": summary_sheet_id,
                          "startRowIndex": totals_row - 1, "endRowIndex": totals_row,
                          "startColumnIndex": 0, "endColumnIndex": 12},
                "cell": {"userEnteredFormat": {
                    "textFormat": {"bold": True},
                    "backgroundColor": {"red": 0.851, "green": 0.918, "blue": 0.957},
                    "borders": {"top": {"style": "SOLID", "width": 2,
                                        "color": {"red": 0, "green": 0, "blue": 0}}},
                }},
                "fields": "userEnteredFormat(textFormat,backgroundColor,borders)",
            }},
            {"repeatCell": {
                "range": {"sheetId": summary_sheet_id,
                          "startRowIndex": totals_row - 1, "endRowIndex": totals_row,
                          "startColumnIndex": 1, "endColumnIndex": 8},
                "cell": {"userEnteredFormat": {
                    "numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00;[RED]-$#,##0.00"},
                }},
                "fields": "userEnteredFormat.numberFormat",
            }},
            {"repeatCell": {
                "range": {"sheetId": summary_sheet_id,
                          "startRowIndex": totals_row - 1, "endRowIndex": totals_row,
                          "startColumnIndex": 9, "endColumnIndex": 11},
                "cell": {"userEnteredFormat": {
                    "numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00;[RED]-$#,##0.00"},
                }},
                "fields": "userEnteredFormat.numberFormat",
            }},
        ])
        print(f"  Summary totals written to row {totals_row}.")
        return

    write_range(service, stab, f"A{last_data+1}:R{totals_row}", [[""] * 18, [""] * 18])
    total_row_data = [
        "TOTALS", "",
        f"=SUM(C3:C{last_data})", f"=SUM(D3:D{last_data})",
        f"=SUM(E3:E{last_data})", "", "", "",
        f"=SUM(I3:I{last_data})",
        f"=SUM(J3:J{last_data})", "", "", "",
        f"=SUM(N3:N{last_data})",
        f"=SUM(O3:O{last_data})", "",
        f"=SUM(Q3:Q{last_data})", f"=SUM(R3:R{last_data})",
    ]
    write_range(service, stab, f"A{totals_row}:R{totals_row}", [total_row_data])

    apply_fmt(service, summary_sheet_id, [
        {"repeatCell": {
            "range": {"sheetId": summary_sheet_id,
                      "startRowIndex": totals_row - 1, "endRowIndex": totals_row,
                      "startColumnIndex": 0, "endColumnIndex": 18},
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.851, "green": 0.918, "blue": 0.957},
                "borders": {"top": {"style": "SOLID", "width": 2,
                                    "color": {"red": 0, "green": 0, "blue": 0}}},
            }},
            "fields": "userEnteredFormat(textFormat,backgroundColor,borders)",
        }},
        {"repeatCell": {
            "range": {"sheetId": summary_sheet_id,
                      "startRowIndex": totals_row - 1, "endRowIndex": totals_row,
                      "startColumnIndex": 1, "endColumnIndex": 18},
            "cell": {"userEnteredFormat": {
                "numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00;[RED]-$#,##0.00"},
            }},
            "fields": "userEnteredFormat.numberFormat",
        }},
    ])
    print(f"  Summary totals written to row {totals_row}.")

    if stab == "Summary-Open":
        col_q = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range=f"{stab}!Q3:Q{last_data}"
        ).execute().get("values", [])
        has_slices = any(
            row and row[0] not in ("", "0", "0.0", "$0.00")
            for row in col_q
        )
        if not has_slices:
            print("  Skipping pie chart — no close-out values to plot.")
            return totals_row + 3
        _CHART_HEIGHT_PX = 400
        _execute_with_retry(service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{"addChart": {"chart": {
                "spec": {
                    "title": "Close-out Value by Position",
                    "pieChart": {
                        "legendPosition": "RIGHT_LEGEND",
                        "series": {"sourceRange": {"sources": [{
                            "sheetId": summary_sheet_id,
                            "startRowIndex": 2, "endRowIndex": last_data,
                            "startColumnIndex": 16, "endColumnIndex": 17,
                        }]}},
                        "domain": {"sourceRange": {"sources": [{
                            "sheetId": summary_sheet_id,
                            "startRowIndex": 2, "endRowIndex": last_data,
                            "startColumnIndex": 0, "endColumnIndex": 1,
                        }]}},
                    },
                },
                "position": {"overlayPosition": {
                    "anchorCell": {
                        "sheetId": summary_sheet_id,
                        "rowIndex": totals_row + 1,
                        "columnIndex": 0,
                    },
                    "widthPixels": 600,
                    "heightPixels": _CHART_HEIGHT_PX,
                }},
            }}}]}
        ))
        print("  Close-out Value pie chart added to Summary-Open.")
        # Labels go below the chart; 21px/row is the Sheets default row height
        return totals_row + 2 + (_CHART_HEIGHT_PX // 21) + 2


def write_run_timestamps(service, stab, labels_row, start_dt, end_dt):
    """Write Last Start / Last End label-value pairs below the chart on stab."""
    summary_sheet_id = get_sheet_id(service, stab)
    if summary_sheet_id is None:
        return
    fmt = "%Y-%m-%d %H:%M:%S"
    write_range(service, stab, f"A{labels_row}:B{labels_row + 1}", [
        ["Last Start", start_dt.strftime(fmt)],
        ["Last End",   end_dt.strftime(fmt)],
    ])
    apply_fmt(service, summary_sheet_id, [
        {"repeatCell": {
            "range": {
                "sheetId": summary_sheet_id,
                "startRowIndex": labels_row - 1, "endRowIndex": labels_row + 1,
                "startColumnIndex": 0, "endColumnIndex": 1,
            },
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
            "fields": "userEnteredFormat.textFormat",
        }},
    ])


def write_other_transactions_tab(service, other_rows):
    sheet_id = recreate_tab(service, "Other Transactions")
    headers = [["Date", "Action", "Symbol", "Description",
                "Quantity", "Price", "Fees & Comm", "Amount"]]
    data = headers + [
        [row.get("Date", ""), row.get("Action", ""), row.get("Symbol", ""),
         row.get("Description", ""), row.get("Quantity", ""), row.get("Price", ""),
         row.get("Fees & Comm", ""), row.get("Amount", "")]
        for row in other_rows
    ]
    write_range(service, "Other Transactions", f"A1:H{len(data)}", data)
    apply_fmt(service, sheet_id, [col_header(sheet_id, 0)])
    print(f"  Other Transactions: {len(other_rows)} rows written.")
