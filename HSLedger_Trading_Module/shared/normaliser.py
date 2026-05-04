"""
normaliser.py — HSLedger Trading Module
Converts raw broker DataFrames into a canonical internal schema.

Canonical row fields:
    trade_date        date
    settlement_date   date
    broker            str
    asset_class       "equity" | "crypto"
    code              str       (ASX ticker or crypto symbol)
    name              str
    transaction       str       (BUY | SELL | DIV | INT | LND | INC | OB | OS | OPT | DEP | WD | SS | SC)
    qty               float     (always positive)
    direction         "buy" | "sell" | "income" | "cash"
    price             float
    brokerage         float
    gst               float
    contract_value    float
    net_proceeds      float
    description       str
    reference         str
    source_file       str
    fingerprint       str       (for duplicate detection)
"""

from __future__ import annotations

import hashlib
import os
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from shared.detect_file_type import BROKER_FINGERPRINTS, detect

# ── Broker column maps (canonical logical name → actual column name) ──────────
BROKER_COL_MAPS: dict[str, dict[str, str]] = {
    "commsec": {
        "date":           "Trade Date",
        "settlement_date":"Settlement Date",
        "reference":      "Contract Note No.",
        "exchange":       "Exchange",
        "code":           "Stock Code",
        "name":           "Stock Name",
        "transaction":    "Transaction",
        "qty":            "Quantity",
        "price":          "Price ($)",
        "brokerage":      "Brokerage ($)",
        "gst":            "GST ($)",
        "contract_value": "Contract Value ($)",
        "net_proceeds":   "Net Proceeds ($)",
        "description":    "Description",
    },
    "nabtrade": {
        "date":           "Trade Date",
        "settlement_date":"Settlement Date",
        "reference":      "Reference Number",
        "code":           "ASX Code",
        "name":           "Description",
        "transaction":    "Transaction Type",
        "qty":            "Quantity",
        "price":          "Unit Price ($)",
        "brokerage":      "Brokerage (inc GST) ($)",
        "gst":            "GST on Brokerage ($)",
        "contract_value": "Contract Value ($)",
        "net_proceeds":   "Net Amount ($)",
        "description":    "Notes",
    },
    "stake": {
        "date":           "Trade Date",       # renamed from "Date" during load
        "settlement_date":"Settlement Date",  # computed as T+2 if missing
        "code":           "Ticker",
        "name":           "Company",
        "transaction":    "Order Type",
        "qty":            "Units",
        "price":          "Price (AUD)",
        "brokerage":      "Brokerage (AUD)",
        "gst":            "GST (AUD)",
        "contract_value": "Trade Value (AUD)",
        "net_proceeds":   "Total (AUD)",
        "description":    "Description",
        "exchange":       "Market",
    },
    "superhero": {
        "date":           "Trade Date",
        "settlement_date":"Settlement Date",
        "exchange":       "Market",
        "code":           "Code",
        "name":           "Name",
        "transaction":    "Transaction",
        "qty":            "Units",
        "price":          "Unit Price ($)",
        "contract_value": "Total Value ($)",
        "brokerage":      "Brokerage ($)",
        "gst":            "GST ($)",
        "net_proceeds":   "Net Amount ($)",
        "description":    "Description",
    },
    "selfwealth": {
        "date":           "Trade Date",
        "settlement_date":"Settlement Date",
        "reference":      "Transaction Ref",
        "exchange":       "Exchange",
        "code":           "Security Code",
        "name":           "Security Name",
        "transaction":    "Transaction Type",
        "qty":            "Units",
        "price":          "Price ($)",
        "contract_value": "Consideration ($)",
        "brokerage":      "Brokerage ($)",
        "gst":            "GST ($)",
        "net_proceeds":   "Net Consideration ($)",
        "description":    "Description",
    },
    "standard": {
        "date":           "date",
        "settlement_date":"settlement_date",
        "code":           "asset_code",
        "name":           "asset_name",
        "exchange":       "exchange",
        "transaction":    "transaction",
        "qty":            "quantity",
        "price":          "unit_price",
        "contract_value": "trade_value",
        "brokerage":      "brokerage",
        "gst":            "gst",
        "net_proceeds":   "net_proceeds",
        "description":    "description",
    },
}

# ── Transaction type normalisation map ────────────────────────────────────────
TXN_NORM: dict[str, str] = {
    # CommSec long-term
    "B": "BUY", "S": "SELL",
    # Generic
    "BUY": "BUY", "SELL": "SELL",
    "BUY ": "BUY", "SELL ": "SELL",
    # Options
    "OB": "OB", "OS": "OS", "OPT": "OPT",
    # Income
    "DIV": "DIV", "INT": "INT", "LND": "LND",
    "INC": "INC", "INCOME": "INC",
    # DRP (Dividend Reinvestment Plan)
    "DRP": "DRP", "DRIP": "DRP", "DIVIDEND REINVESTMENT": "DRP",
    # Cash
    "DEP": "DEP", "WD": "WD",
    "DEPOSIT": "DEP", "WITHDRAWAL": "WD",
    # Short selling
    "SS": "SS", "SC": "SC",
    "SHORT SELL": "SS", "SHORT SALE": "SS", "SELL SHORT": "SS",
    "SHORT COVER": "SC", "COVER SHORT": "SC", "BUY TO COVER": "SC",
}

INCOME_TYPES = {"DIV", "INT", "LND", "INC"}
CASH_TYPES   = {"DEP", "WD"}
BUY_TYPES    = {"BUY", "OB", "SC", "DRP"}
SELL_TYPES   = {"SELL", "OS", "OPT", "SS"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_date(val: Any) -> date | None:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if pd.isna(val):
        return None
    s = str(val).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d %b %Y", "%d %B %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _safe_float(val: Any, default: float = 0.0) -> float:
    if pd.isna(val):
        return default
    try:
        return float(str(val).replace(",", "").replace("$", "").strip())
    except (ValueError, TypeError):
        return default


def _t2(d: date) -> date:
    """Add 2 business days (simple approximation — no public holiday calendar)."""
    count, cur = 0, d
    while count < 2:
        cur = cur + timedelta(days=1)
        if cur.weekday() < 5:   # Mon-Fri
            count += 1
    return cur


def _fingerprint(trade_date: date | None, code: str, qty: float, price: float) -> str:
    """Stable hash for duplicate detection: date+code+qty+price."""
    raw = f"{trade_date}|{code.upper()}|{round(qty, 4)}|{round(price, 4)}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _normalise_txn(raw: str, qty: float) -> str:
    """Resolve raw transaction label to canonical type. TXN and OPT use qty sign."""
    t = str(raw).strip().upper()
    if t == "TXN":
        return "BUY" if qty > 0 else "SELL"
    # OPT with positive qty = opening buy (OB); negative/zero = close/lapse/exercise (OPT)
    if t == "OPT":
        return "OB" if qty > 0 else "OPT"
    return TXN_NORM.get(t, t)


_OPT_DESC_BUY  = {"option buy", "buy option", "option open", "open option", "option purchase"}
_OPT_DESC_SELL = {"option sell", "sell option", "option close", "close option", "option expired", "expired worthless"}
_OPT_DESC_EXE  = {"exercised", "exercise", "option exercise"}

_SHORT_DESC_SELL  = {"short sell", "short sale", "sell short"}
_SHORT_DESC_COVER = {"short cover", "cover short", "buy to cover"}

def _detect_option_from_desc(txn: str, desc: str) -> str:
    """
    Upgrade a generic BUY/SELL classification to OB/OS/OPT when the row
    description contains option-specific keywords.  Only fires for rows that
    are already classified as BUY or SELL so we never override explicit labels.
    """
    if txn not in ("BUY", "SELL"):
        return txn
    dl = desc.lower()
    if any(k in dl for k in _OPT_DESC_EXE):
        return "OPT"
    if any(k in dl for k in _OPT_DESC_SELL):
        return "OS"
    if any(k in dl for k in _OPT_DESC_BUY):
        return "OB"
    return txn


def _detect_short_from_desc(txn: str, desc: str) -> str:
    """
    Upgrade a generic BUY/SELL to SS/SC when the description contains
    short-selling keywords.  Only fires after option detection so option
    rows are never reclassified.
    """
    if txn not in ("BUY", "SELL"):
        return txn
    dl = desc.lower()
    if any(k in dl for k in _SHORT_DESC_SELL):
        return "SS"
    if any(k in dl for k in _SHORT_DESC_COVER):
        return "SC"
    return txn


def _direction(txn: str) -> str:
    if txn in BUY_TYPES:
        return "buy"
    if txn in SELL_TYPES:
        return "sell"
    if txn in INCOME_TYPES:
        return "income"
    return "cash"


# ── Core normaliser ───────────────────────────────────────────────────────────

def normalise(
    df: pd.DataFrame,
    broker: str,
    asset_class: str = "equity",
    source_file: str = "",
    col_map_override: dict[str, str] | None = None,
) -> pd.DataFrame:
    """
    Convert a raw broker DataFrame into the canonical internal schema.

    Parameters
    ----------
    df                : Raw DataFrame as loaded from the broker file
    broker            : Broker identifier (key in BROKER_COL_MAPS)
    asset_class       : "equity" or "crypto"
    source_file       : Original filename — stored on every row for traceability
    col_map_override  : Optional full override of the column mapping

    Returns
    -------
    DataFrame with canonical columns only. Rows that cannot be parsed
    (no valid date or code) are dropped with a warning printed.
    """
    col_map = col_map_override or BROKER_COL_MAPS.get(broker, BROKER_COL_MAPS["standard"])

    def get(row: pd.Series, logical: str, default: Any = None) -> Any:
        actual = col_map.get(logical)
        if actual is None or actual not in row.index:
            return default
        v = row[actual]
        return default if pd.isna(v) else v

    # ── Stake: rename "Date" → "Trade Date" and compute Settlement Date ────────
    if broker == "stake":
        if "Date" in df.columns and "Trade Date" not in df.columns:
            df = df.rename(columns={"Date": "Trade Date"})
        if "Settlement Date" not in df.columns and "Trade Date" in df.columns:
            df["_td_parsed"] = pd.to_datetime(df["Trade Date"], dayfirst=True, format="mixed", errors="coerce")
            df["Settlement Date"] = df["_td_parsed"].apply(
                lambda d: _t2(d.date()).strftime("%d/%m/%Y") if pd.notna(d) else None
            )
            df.drop(columns=["_td_parsed"], inplace=True)

    rows_out = []
    skipped  = 0

    for _, row in df.iterrows():
        raw_qty   = _safe_float(get(row, "qty", 0))
        trade_d   = _parse_date(get(row, "date"))
        sett_d    = _parse_date(get(row, "settlement_date"))
        code      = str(get(row, "code", "")).strip().upper()
        name      = str(get(row, "name", code)).strip()
        raw_txn   = str(get(row, "transaction", "")).strip()
        price     = _safe_float(get(row, "price", 0))
        brokerage = _safe_float(get(row, "brokerage", 0))
        gst       = _safe_float(get(row, "gst", 0))
        cv        = _safe_float(get(row, "contract_value", 0))
        net_proc  = _safe_float(get(row, "net_proceeds", 0))
        reference = str(get(row, "reference", "")).strip()
        desc      = str(get(row, "description", "")).strip()

        if trade_d is None or not code:
            skipped += 1
            continue

        txn  = _normalise_txn(raw_txn, raw_qty)
        txn  = _detect_option_from_desc(txn, desc)
        txn  = _detect_short_from_desc(txn, desc)
        qty  = abs(raw_qty)
        sett = sett_d if sett_d else (_t2(trade_d) if trade_d else None)

        rows_out.append({
            "trade_date":      trade_d,
            "settlement_date": sett,
            "broker":          broker,
            "asset_class":     asset_class,
            "code":            code,
            "name":            name,
            "transaction":     txn,
            "qty":             qty,
            "direction":       _direction(txn),
            "price":           price,
            "brokerage":       brokerage,
            "gst":             gst,
            "contract_value":  cv,
            "net_proceeds":    net_proc,
            "description":     desc,
            "reference":       reference,
            "source_file":     os.path.basename(source_file),
            "fingerprint":     _fingerprint(trade_d, code, qty, price),
        })

    if skipped:
        print(f"[normaliser] {broker}: skipped {skipped} rows (missing date or code)")

    return pd.DataFrame(rows_out) if rows_out else pd.DataFrame()


def load_and_normalise(path: str, col_map_override: dict[str, str] | None = None) -> pd.DataFrame:
    """
    Detect broker, load file, and return normalised DataFrame in one call.
    Raises ValueError if confidence is below threshold.
    """
    det = detect(path)

    if det["confidence"] < 0.50:
        msg = (
            f"Cannot identify broker for '{os.path.basename(path)}' "
            f"(confidence {det['confidence']:.0%}). "
            f"{det.get('fallback_suggestion', '')}"
        )
        raise ValueError(msg)

    broker      = det["broker"]
    asset_class = det["asset_class"]
    header_row  = det.get("header_row", 0)
    # col_format is the column-layout broker (may differ from broker when the
    # reference-prefix override fires — e.g. a NABtrade file using CommSec cols)
    col_format  = det.get("col_format", broker)
    ext         = os.path.splitext(path)[1].lower()

    if ext in (".xlsx", ".xlsm", ".xls"):
        df = pd.read_excel(path, header=header_row)
    elif ext == ".csv":
        df = pd.read_csv(path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    effective_col_map = col_map_override or BROKER_COL_MAPS.get(col_format, BROKER_COL_MAPS["standard"])
    return normalise(df, broker, asset_class, source_file=path, col_map_override=effective_col_map)
