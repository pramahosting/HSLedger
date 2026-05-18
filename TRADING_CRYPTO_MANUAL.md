# HSLedger — Trading & Crypto Module Manual

---

## Table of Contents

1. [Overview](#1-overview)
2. [Prerequisites](#2-prerequisites)
3. [Project Structure](#3-project-structure)
4. [Stock Trading Module](#4-stock-trading-module)
   - [Input Files](#41-input-files)
   - [Run via CLI](#42-run-via-cli)
   - [Run via Python API](#43-run-via-python-api)
   - [Handling Missing Buys](#44-handling-missing-buys)
   - [Output](#45-output)
5. [Crypto Module](#5-crypto-module)
   - [Input Files](#51-input-files)
   - [How to Run](#52-how-to-run)
   - [Supported Exchanges](#53-supported-exchanges)
   - [Output](#54-output)
6. [Configuration Reference](#6-configuration-reference)
7. [Data Flow Diagrams](#7-data-flow-diagrams)
8. [Common Errors & Fixes](#8-common-errors--fixes)

---

## 1. Overview

HSLedger has two trading sub-systems that work independently:

| Module | Location | Purpose |
|---|---|---|
| **Stock (Equity)** | `HSLedger_Trading_Module/` | ASX broker CSV → FIFO CGT report (.xlsx) |
| **Crypto** | `streamlit_frontend/backend/trading/` | Exchange ledger CSV → FIFO CGT report |

Both apply **Australian Tax Office (ATO) CGT rules**:
- FIFO lot matching
- 50% CGT discount for assets held > 365 days
- Carry-forward loss support
- Staking / reward / airdrop income separated from capital gains

---

## 2. Prerequisites

```bash
# From the project root
cd /home/ammulap/PycharmProjects/HSLedger

# Activate the virtual environment
source .venv/bin/activate

# Verify dependencies are installed
pip install -r requirements.txt   # or check pyproject.toml
```

Required packages: `pandas`, `openpyxl`, `fastapi`, `sqlalchemy`, `streamlit`

---

## 3. Project Structure

```
HSLedger/
├── HSLedger_Trading_Module/          ← STOCK module
│   ├── config.py                     # paths & defaults
│   ├── main.py                       # CLI entry point + run_trading_pipeline()
│   ├── inputs/                       # drop broker CSVs here
│   ├── output/                       # Excel reports written here
│   ├── data/
│   │   └── local_cost_base_db.json   # persisted manual buy lots
│   ├── equity/
│   │   └── equity_engine.py          # FIFO CGT engine
│   ├── shared/
│   │   ├── normaliser.py             # broker CSV → standard schema
│   │   ├── detect_file_type.py       # auto-detect broker format
│   │   ├── deduplicator.py           # remove duplicate rows
│   │   ├── multi_file_merger.py      # merge multiple broker files
│   │   ├── cost_base_loader.py       # load historical lots from CSV
│   │   └── local_cost_base_db.json   # manually-entered historical lots
│   └── output/
│       └── excel_exporter.py         # write .xlsx report
│
└── streamlit_frontend/
    └── backend/trading/              ← CRYPTO module
        ├── crypto_normalizer.py      # exchange CSV → standard schema
        ├── crypto_cgt_engine.py      # FIFO CGT engine
        ├── capital_gains.py          # simpler stock gain calculator
        ├── tax_calculator.py         # ATO tax rules
        ├── report_presentation.py    # format results for display
        └── trading_exporter.py       # export to CSV/Excel
```

---

## 4. Stock Trading Module

### 4.1 Input Files

Place broker CSV exports inside:
```
HSLedger_Trading_Module/inputs/
```

**Supported brokers** (auto-detected by filename and headers):

| Broker | Typical filename pattern |
|---|---|
| CommSec | `commsec_*.csv` |
| NABtrade | `nabtrade_*.csv` |
| Stake | `stake_*.csv` |
| SelfWealth | `selfwealth_*.csv` |

**Optional: Historical cost base** — if you have shares purchased before the
broker export period, add them to:
```
HSLedger_Trading_Module/inputs/cost_base_history.csv
```

Required columns for cost_base_history.csv:
```
code, trade_date, qty, price, brokerage, gst, broker
```

Example row:
```
CBA, 15/06/2019, 100, 72.50, 19.95, 0.00, commsec
```

---

### 4.2 Run via CLI

```bash
cd HSLedger_Trading_Module

# Run with all defaults (reads inputs/, writes output/HSLedger_Equity_CGT_Report.xlsx)
python main.py

# Target a specific financial year
python main.py --fy 2024-25

# Process all financial years in the files
python main.py --fy all

# Override the input folder
python main.py --input "/path/to/my/broker/files"

# Override the output path
python main.py --output "/path/to/MyReport.xlsx"

# Carry forward a prior-year loss of $5,000 from 2023-24
python main.py --carry-forward '{"2023-24": 5000}'

# Interactive missing-buy prompt (ask on stdin for any unmatched sells)
python main.py --interactive

# Resolve missing buys, save to local DB, then re-run automatically
python main.py --resolve-missing

# Show all resolved paths and exit (useful for debugging)
python main.py --config
```

All CLI flags at a glance:

| Flag | Short | Default | Description |
|---|---|---|---|
| `--input` | `-i` | `inputs/` | Folder or file with broker CSVs |
| `--output` | `-o` | `output/HSLedger_Equity_CGT_Report.xlsx` | Output Excel path |
| `--history` | `-H` | `inputs/cost_base_history.csv` | Historical lots CSV |
| `--fy` | `-f` | `2024-25` | Target FY, or `all` |
| `--carry-forward` | `-c` | `{}` | JSON dict of prior-year losses |
| `--local-db` | | `data/local_cost_base_db.json` | Manual lots database |
| `--interactive` | `-I` | off | Prompt stdin for missing buys |
| `--resolve-missing` | | off | Resolve + re-run pipeline |
| `--config` | | | Print paths and exit |

---

### 4.3 Run via Python API

```python
from HSLedger_Trading_Module.main import run_trading_pipeline

# Minimal — uses config.py defaults
result = run_trading_pipeline()

# With overrides
result = run_trading_pipeline(
    source="path/to/broker/files",
    output_path="path/to/report.xlsx",
    target_fy="2024-25",
    carry_forward_losses={"2023-24": 5000.0},
    skip_crypto=True,          # ignore crypto rows in mixed files
    interactive_missing=False, # don't block on stdin
)

# Access results
print(result.disposals_df)       # DataFrame of all CGT disposal events
print(result.income_df)          # DataFrame of dividend/income events
print(result.summary_df)         # Per-FY summary (gains, losses, tax)
print(result.missing_df)         # Sells with no matching buy
print(result.open_positions)     # dict[symbol → deque[Lot]] still held
result.print_summary()           # Formatted console summary
```

`TradingPipelineResult` properties:

| Property | Type | Description |
|---|---|---|
| `disposals_df` | DataFrame | All matched sell events with CGT |
| `income_df` | DataFrame | Dividends, interest, DRP |
| `summary_df` | DataFrame | FY-level totals |
| `missing_df` | DataFrame | Unmatched sells |
| `open_positions` | dict | Remaining lots by symbol |
| `excel_bytes` | bytes | Raw Excel file (for HTTP responses) |
| `excel_path` | str | Path to written .xlsx |

---

### 4.4 Handling Missing Buys

A **missing buy** occurs when a SELL has no matching BUY lot in the data
(e.g. shares were purchased before your oldest broker export).

**Option A — Add to cost_base_history.csv** (permanent, recommended):
```csv
code, trade_date, qty, price, brokerage, gst, broker
BHP, 10/03/2018, 200, 28.50, 19.95, 0.00, commsec
```

**Option B — CLI interactive resolution**:
```bash
python main.py --resolve-missing
# The tool walks you through each unmatched sell,
# saves your answers to data/local_cost_base_db.json,
# then re-runs the pipeline automatically.
```

**Option C — Streamlit UI** (recommended for non-technical users):
Upload files through the trading UI — the missing buys panel lets you
enter purchase details without touching any files.

---

### 4.5 Output

The Excel report (`HSLedger_Equity_CGT_Report.xlsx`) contains these sheets:

| Sheet | Contents |
|---|---|
| **CGT Disposals** | Every matched sell: buy date, sell date, qty, proceeds, cost base, gain/loss, discount |
| **FY Summary** | Per-year totals: gross gains, gross losses, CGT discount, net taxable gain |
| **Income** | Dividends, DRP, interest |
| **Missing Buys** | Sells that could not be matched — need manual resolution |
| **Open Positions** | Lots still held at end of report period |
| **Duplicates** | Rows removed as cross-broker duplicates |
| **Resolution Log** | History of manually-entered lots from local DB |

---

## 5. Crypto Module

### 5.1 Input Files

The crypto module accepts CSV ledger exports from any exchange.
No fixed filename convention — the normalizer detects columns automatically.

The CSV must contain columns matching at least these logical fields
(exact names vary by exchange — see aliases in `crypto_normalizer.py`):

| Logical field | What it represents | Example column names |
|---|---|---|
| `datetime` | Transaction timestamp | `datetime_utc`, `date`, `timestamp` |
| `event_type` | Buy / Sell / Staking etc. | `type`, `transaction_type`, `action` |
| `asset` | Crypto symbol | `asset`, `coin`, `currency`, `symbol` |
| `quantity` | Amount of crypto | `quantity`, `qty`, `units`, `amount` |
| `amount_aud` | AUD value | `amount_aud`, `total_aud`, `aud_value`, `total` |
| `fee_aud` | Fee in AUD | `fee_aud`, `fee`, `commission` *(optional)* |

**Automatic fallbacks:**
- If `amount_aud` is missing but `price_aud` exists → calculated as `price_aud × quantity`
- If `asset` is missing but a pair column exists (e.g. `BTC/AUD`) → base asset extracted
- If `fee_aud` is missing → defaulted to 0

---

### 5.2 How to Run

The crypto engine is called programmatically (through the Streamlit UI or directly in Python):

```python
import pandas as pd
from streamlit_frontend.backend.trading.crypto_normalizer import normalize_ledger
from streamlit_frontend.backend.trading.crypto_cgt_engine import compute_cgt_fifo

# 1. Load your exchange CSV
raw_df = pd.read_csv("path/to/exchange_export.csv")

# 2. Normalise (maps any exchange format to standard internal columns)
norm_df, report = normalize_ledger(raw_df)

# Print what columns were detected
print(report.to_display_text())

# 3. Run FIFO CGT engine
disposals_df, income_df, open_positions = compute_cgt_fifo(
    norm_df,
    target_fy="2024-25",   # pass None for all years
)

# 4. Inspect results
print(disposals_df)     # every disposal event with gain/loss
print(income_df)        # staking, rewards, airdrops
print(open_positions)   # remaining lots by asset
```

**With manually-entered historical acquisitions** (shares you bought
before your oldest export):

```python
extra_acq = [
    {
        "acquisition_date": "15/03/2021",   # dd/mm/yyyy
        "asset": "BTC",
        "qty": 0.5,
        "total_cost_aud": 25000.00,
        "fee_aud": 50.00,
    }
]

disposals_df, income_df, open_positions = compute_cgt_fifo(
    norm_df,
    target_fy="2024-25",
    extra_acq=extra_acq,
)
```

**Collecting unmatched disposals** (sells with no matching buy):

```python
missing = {}
disposals_df, income_df, open_positions = compute_cgt_fifo(
    norm_df,
    target_fy="2024-25",
    _out_missing=missing,
)

# missing = {"BTC": [{"sell_date": date(...), "qty": 0.1, "proceeds_aud": 4500.0, "fy": "2024-25"}]}
for asset, flags in missing.items():
    for f in flags:
        print(f"{asset}: {f['qty']} units sold on {f['sell_date']} — no matching buy")
```

---

### 5.3 Supported Exchanges

Tested out-of-the-box:

| Exchange | Notes |
|---|---|
| **CoinSpot** | Full support |
| **Swyftx** | Full support |
| **Binance** | Full support |
| **Kraken** | Full support |
| **Coinbase** | Full support |
| **Independent Reserve** | Full support |
| **Generic** | Any CSV with standard column names |

**For unsupported exchanges**: Rename your CSV columns to match any of the
alias names listed in section 5.1, or raise an issue with a sample file.

---

### 5.4 Output

`compute_cgt_fifo` returns three objects:

**`disposals_df`** — one row per matched sell lot:

| Column | Description |
|---|---|
| `asset` | Crypto symbol (BTC, ETH, …) |
| `sell_date` | Date of disposal (dd/mm/yyyy) |
| `buy_date` | Date of matched acquisition |
| `qty` | Units disposed |
| `proceeds_aud` | Net proceeds in AUD (after fee) |
| `cost_base_aud` | Cost base for this lot |
| `capital_gain_aud` | Gross capital gain/loss |
| `discount_eligible` | True if held > 365 days and gain > 0 |
| `discounted_gain_aud` | After 50% CGT discount if eligible |
| `days_held` | Days between buy and sell |
| `acquisition_type` | BUY / STAKING / REWARD / AIRDROP |
| `fy` | Financial year (e.g. "2024-25") |

**`income_df`** — staking rewards, airdrops, mining income:

| Column | Description |
|---|---|
| `event_type` | STAKING / REWARD / AIRDROP |
| `asset` | Crypto symbol |
| `date` | Date received |
| `qty` | Units received |
| `amount_aud` | Fair-market value in AUD |
| `fy` | Financial year |

**`open_positions`** — dict of remaining unrealised lots:
```python
{
  "BTC": [{"date": date(2023,1,1), "qty": 0.2, "cost_per_unit": 25000.0, ...}],
  "ETH": [...],
}
```

---

## 6. Configuration Reference

**`HSLedger_Trading_Module/config.py`** — all stock module defaults:

```python
DEFAULT_TARGET_FY         = "2024-25"    # change to target a different year
CARRY_FORWARD_LOSSES      = {}           # e.g. {"2023-24": 5000.0}
BROKER_CONFIDENCE_THRESHOLD = 0.50      # files below this confidence are skipped

# Paths — auto-resolved, no manual editing needed
INPUT_DIR    = TRADING_MODULE_ROOT / "inputs"
OUTPUT_DIR   = TRADING_MODULE_ROOT / "output"
LOCAL_COST_BASE_DB = TRADING_MODULE_ROOT / "data" / "local_cost_base_db.json"
```

Verify all paths resolve correctly:
```bash
cd HSLedger_Trading_Module
python main.py --config
```

---

## 7. Data Flow Diagrams

### Stock Trading Pipeline

```
Broker CSVs (inputs/)
        │
        ▼
detect_file_type.py       ← identifies broker format (CommSec, NABtrade, …)
        │
        ▼
normaliser.py             ← maps broker columns to canonical schema
        │
        ▼
deduplicator.py           ← removes cross-broker duplicate rows
        │
        ▼
multi_file_merger.py      ← merges all broker files into one DataFrame
        │
        ├── cost_base_loader.py   ← prepends historical lots (CSV)
        └── local_cost_base_db   ← prepends manually-entered lots (JSON)
        │
        ▼
equity_engine.py          ← FIFO matching → disposals, income, summaries
        │
        ▼
excel_exporter.py         ← writes HSLedger_Equity_CGT_Report.xlsx
```

### Crypto Pipeline

```
Exchange CSV (any format)
        │
        ▼
crypto_normalizer.py      ← auto-detects columns, maps to _dt/_event/_asset/_qty/_amount_aud/_fee_aud
        │
        ▼
compute_cgt_fifo()        ← FIFO matching → disposals_df, income_df, open_positions
        │
        ▼
tax_calculator.py         ← applies 50% discount, FY filters
        │
        ▼
trading_exporter.py       ← CSV / Excel export
```

---

## 8. Common Errors & Fixes

### "Missing required fields: amount_aud"
Your exchange CSV doesn't have an AUD total column.
**Fix:** Either rename the column to `amount_aud`, or add a `price_aud` column —
the normalizer will calculate `amount_aud = price_aud × quantity` automatically.

### "No data to process after loading files"
The broker confidence score is below `BROKER_CONFIDENCE_THRESHOLD` (0.50).
**Fix:** Run `python main.py --config` to confirm the input path is correct,
then check that the CSV headers match a known broker format.

### "X unmatched sell(s) — no matching buy found"
Sells exist with no prior BUY lots in the data.
**Fix:** Add historical purchases to `inputs/cost_base_history.csv` or run
`python main.py --resolve-missing` to enter them interactively.

### Excel report is empty / only has headers
Target FY has no data in the uploaded files.
**Fix:** Run `python main.py --fy all` to see which years are available,
then set `DEFAULT_TARGET_FY` in `config.py` accordingly.

### "ValueError: Cannot parse date"
A date column contains mixed or unrecognised formats.
**Fix:** Ensure the date column is consistently formatted. The normalizer
tries multiple formats automatically but falls back to NaT on failure.
Rows with NaT dates are silently skipped by the engine.

### ImportError when calling from outside the module
**Fix:** The module root must be on `sys.path`:
```python
import sys
sys.path.insert(0, "/path/to/HSLedger/HSLedger_Trading_Module")
from main import run_trading_pipeline
```
