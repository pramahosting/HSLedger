"""
config.py — HSLedger Trading Module
Central path configuration for local Windows development (PyCharm).

All paths are resolved with pathlib so they work on both Windows and macOS/Linux.
Change PROJECT_ROOT if your PyCharm project is in a different location.
"""

from __future__ import annotations
import os
from pathlib import Path

# ── Project root ──────────────────────────────────────────────────────────────
# Absolute path to the HSLedger_Trading_Module project folder.
# Detected automatically from this file's location — no manual editing needed
# as long as config.py stays inside the trading/ folder.
TRADING_MODULE_ROOT = Path(__file__).resolve().parent        # .../trading/
PROJECT_ROOT        = TRADING_MODULE_ROOT.parent             # .../HSLedger_Trading_Module/

# ── Input / output paths ──────────────────────────────────────────────────────
# These match the exact local Windows paths used in PyCharm.
# Both paths are resolved through TRADING_MODULE_ROOT so they work automatically
# regardless of which machine the project is opened on, as long as the folder
# structure (trading/input and trading/output) is kept intact.

# Broker transaction files go here:
#   C:\Users\alfsj\PycharmProjects\HSLedger_Trading_Module\trading\inputs
INPUT_DIR = TRADING_MODULE_ROOT / "inputs"

# Excel reports are written here:
#   C:\Users\alfsj\PycharmProjects\HSLedger_Trading_Module\trading\output
OUTPUT_DIR = TRADING_MODULE_ROOT / "output"

# Default cost base history file (place inside input folder)
COST_BASE_HISTORY_PATH = INPUT_DIR / "cost_base_history.csv"

# Default output report filename (written into OUTPUT_DIR)
DEFAULT_REPORT_NAME = "HSLedger_Equity_CGT_Report.xlsx"

# Local persistent database for manually-entered historical BUY lots
LOCAL_COST_BASE_DB = TRADING_MODULE_ROOT / "data" / "local_cost_base_db.json"

# ── Resolved absolute paths (as strings — for code that expects str) ──────────
INPUT_DIR_STR            = str(INPUT_DIR)
OUTPUT_DIR_STR           = str(OUTPUT_DIR)
COST_BASE_HISTORY_STR    = str(COST_BASE_HISTORY_PATH)
DEFAULT_REPORT_PATH      = str(OUTPUT_DIR / DEFAULT_REPORT_NAME)
LOCAL_COST_BASE_DB_STR   = str(LOCAL_COST_BASE_DB)

# ── CGT engine settings ───────────────────────────────────────────────────────
DEFAULT_TARGET_FY         = "2024-25"    # override with --fy on CLI
CARRY_FORWARD_LOSSES      = {}           # e.g. {"2023-24": 5000.0}
BROKER_CONFIDENCE_THRESHOLD = 0.50      # files below this are skipped

# ── Ensure directories exist on import ───────────────────────────────────────
INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def print_config() -> None:
    """Print resolved paths — useful for debugging in PyCharm terminal."""
    print(f"\n{'─'*60}")
    print(f"  HSLedger Trading Module — Path Configuration")
    print(f"{'─'*60}")
    print(f"  Project root:   {PROJECT_ROOT}")
    print(f"  Trading root:   {TRADING_MODULE_ROOT}")
    print(f"  Input dir:      {INPUT_DIR}  {'✓ exists' if INPUT_DIR.exists() else '✗ missing'}")
    print(f"  Output dir:     {OUTPUT_DIR}  {'✓ exists' if OUTPUT_DIR.exists() else '✗ missing'}")
    print(f"  History file:   {COST_BASE_HISTORY_PATH}  "
          f"{'✓ found' if COST_BASE_HISTORY_PATH.exists() else '○ not found (optional)'}")
    print(f"  Local DB:       {LOCAL_COST_BASE_DB}  "
          f"{'✓ found' if LOCAL_COST_BASE_DB.exists() else '○ not found (auto-created on first run)'}")
    print(f"  Default report: {DEFAULT_REPORT_PATH}")
    print(f"{'─'*60}\n")


if __name__ == "__main__":
    print_config()
