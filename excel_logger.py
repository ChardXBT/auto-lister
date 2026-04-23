"""
excel_logger.py
===============
Drop this file next to bot.py on your server.

It appends a new row to CS_GO_PnL_Tracker_Final.xlsx whenever an item sells.
Fills in: Item Name, Sold Price (USD)
Leaves blank for you to fill: Cost (USD), Link
All formula columns (Fee, Net Sold, Profit, ROI%) auto-calculate via existing formulas.

Usage (called automatically by the bot):
    from excel_logger import log_sale
    log_sale("MP9 | Sand Dashed 0.130889981985", sold_price_cents=410)
"""

import os
from pathlib import Path
from openpyxl import load_workbook
from openpyxl.styles import Font, Alignment, Border, Side
from datetime import datetime

# ── Config ─────────────────────────────────────────────────────────────────────
EXCEL_FILE  = os.getenv("EXCEL_PATH", "CS_GO_PnL_Tracker_Final.xlsx")
SHEET_NAME  = "CSGO PnL Tracker"

# Column positions (1-indexed), matching your sheet:
# A=Item Name, B=Cost, C=Sold Price, D=Fee Amount, E=Net Sold, F=Profit, G=ROI%, H=Link
COL_NAME   = 1  # A
COL_COST   = 2  # B
COL_SOLD   = 3  # C
COL_FEE    = 4  # D
COL_NET    = 5  # E
COL_PROFIT = 6  # F
COL_ROI    = 7  # G
COL_LINK   = 8  # H


# Your sheet's summary block starts at row 1149, so data rows are 3–1147
DATA_START_ROW = 3
DATA_END_ROW   = 1147  # hard cap — never write into or past the summary


def _find_next_row(sheet) -> int:
    """Return the first empty row within the data range (rows 3–1147)."""
    for row in range(DATA_END_ROW, DATA_START_ROW - 1, -1):
        if sheet.cell(row=row, column=COL_NAME).value is not None:
            next_row = row + 1
            if next_row > DATA_END_ROW:
                print(f"[excel_logger] WARNING: Data range is full (row {DATA_END_ROW}). Expand your sheet!")
                return DATA_END_ROW
            return next_row
    return DATA_START_ROW  # sheet is empty, start at row 3


def _copy_row_style(sheet, src_row: int, dst_row: int):
    """Copy border/alignment/number-format from src_row to dst_row."""
    for col in range(1, COL_LINK + 1):
        src = sheet.cell(row=src_row, column=col)
        dst = sheet.cell(row=dst_row, column=col)
        if src.has_style:
            dst.font      = src.font.copy()
            dst.alignment = src.alignment.copy()
            dst.border    = src.border.copy()
            if src.number_format:
                dst.number_format = src.number_format


def log_sale(item_name: str, sold_price_cents: int) -> bool:
    """
    Append a sold item row to the Excel tracker.

    Args:
        item_name:         The display name from config.json  (e.g. "MP9 | Sand Dashed …")
        sold_price_cents:  The reserve_price from config.json (bot stores prices in cents)

    Returns:
        True on success, False on error.
    """
    excel_path = Path(EXCEL_FILE)
    if not excel_path.exists():
        print(f"[excel_logger] ERROR: '{EXCEL_FILE}' not found — skipping log.")
        return False

    sold_price_usd = sold_price_cents / 100.0

    try:
        wb = load_workbook(excel_path)

        if SHEET_NAME not in wb.sheetnames:
            print(f"[excel_logger] ERROR: Sheet '{SHEET_NAME}' not found.")
            return False

        ws = wb[SHEET_NAME]

        # Find where to write
        next_row = _find_next_row(ws)

        # Copy styling from the row above so it looks consistent
        if next_row > 3:
            _copy_row_style(ws, next_row - 1, next_row)

        # Fee rate cell is always B1 in your sheet
        fee_ref = "$B$1"

        # Write data
        ws.cell(row=next_row, column=COL_NAME).value  = item_name
        ws.cell(row=next_row, column=COL_COST).value  = None   # you fill this in
        ws.cell(row=next_row, column=COL_SOLD).value  = sold_price_usd

        # Replicate existing formulas (mirrors your sheet's formula pattern)
        r = next_row
        ws.cell(row=r, column=COL_FEE).value    = f"=C{r}*{fee_ref}"
        ws.cell(row=r, column=COL_NET).value     = f"=C{r}-D{r}"
        ws.cell(row=r, column=COL_PROFIT).value  = f"=E{r}-B{r}"
        ws.cell(row=r, column=COL_ROI).value     = f"=F{r}/B{r}"
        ws.cell(row=r, column=COL_LINK).value    = None   # you fill this in

        # Number formatting to match existing rows
        ws.cell(row=r, column=COL_COST).number_format   = '#,##0.00'
        ws.cell(row=r, column=COL_SOLD).number_format   = '#,##0.00'
        ws.cell(row=r, column=COL_FEE).number_format    = '#,##0.00'
        ws.cell(row=r, column=COL_NET).number_format    = '#,##0.00'
        ws.cell(row=r, column=COL_PROFIT).number_format = '#,##0.00'
        ws.cell(row=r, column=COL_ROI).number_format    = '0.00%'

        wb.save(excel_path)
        print(f"[excel_logger] ✅ Logged sale → row {next_row}: '{item_name}' @ ${sold_price_usd:.2f}")
        return True

    except Exception as e:
        print(f"[excel_logger] ERROR writing to Excel: {e}")
        return False