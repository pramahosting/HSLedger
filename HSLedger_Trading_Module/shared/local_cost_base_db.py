"""
local_cost_base_db.py — HSLedger Trading Module
Persistent local JSON database for historical equity BUY lots.

Lets users resolve "Missing Buys" permanently.  Every entry saved here
feeds the FIFO CGT engine on every subsequent run, reducing or eliminating
the Qty Unmatched shown in the Missing Buys Excel sheet.

Database file: data/local_cost_base_db.json (inside module root)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import date, datetime
from typing import Any

import pandas as pd

from shared.normaliser import _fingerprint, _parse_date, _safe_float, _t2


DB_VERSION = "1.0"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _lot_hash(code: str, trade_date: str, qty: float, price: float,
              broker: str, reference: str) -> str:
    """Stable SHA-256 fingerprint for a lot — used to prevent duplicates."""
    raw = f"{code}|{trade_date}|{qty:.6f}|{price:.6f}|{broker}|{reference}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _backup(path: str) -> None:
    """Write a timestamped backup before overwriting the database."""
    if not os.path.exists(path):
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.replace(".json", f".backup.{ts}.json")
    shutil.copy2(path, backup_path)


def _load_db(path: str) -> dict:
    ensure_local_db(path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_db(path: str, db: dict) -> None:
    _backup(path)
    db["updated_at"] = _now_iso()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, default=str)


# ── Public API ────────────────────────────────────────────────────────────────

def ensure_local_db(path: str) -> None:
    """
    Create the data/ folder and local_cost_base_db.json if they don't exist.
    Safe to call on every startup — no-op if the file already exists.
    """
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    if os.path.exists(path):
        return
    now = _now_iso()
    db: dict = {
        "version":          DB_VERSION,
        "created_at":       now,
        "updated_at":       now,
        "historical_lots":  [],
        "resolution_log":   [],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2)
    print(f"[local_cost_base_db] Initialised new database at {path}")


def load_local_lots(path: str) -> pd.DataFrame:
    """
    Read historical_lots from the JSON DB and return a canonical BUY DataFrame
    compatible with the normaliser / cost_base_loader schema.

    Output columns match the canonical schema so this DataFrame can be
    concatenated directly with history_df and fed to _build_initial_queues().

    Uses qty_remaining as qty so partially-consumed lots are handled correctly.
    """
    ensure_local_db(path)
    db = _load_db(path)
    lots = db.get("historical_lots", [])
    if not lots:
        return pd.DataFrame()

    rows = []
    for lot in lots:
        qty = float(lot.get("qty_remaining", lot.get("qty_original", 0)))
        if qty <= 0:
            continue
        trade_d = _parse_date(lot.get("trade_date"))
        if trade_d is None:
            continue

        code      = str(lot.get("code", "")).strip().upper()
        name      = str(lot.get("name", code)).strip()
        broker    = str(lot.get("broker", "local_cost_base_db")).strip()
        reference = str(lot.get("reference", "")).strip()
        price     = float(lot.get("unit_price", 0))
        brok      = float(lot.get("brokerage", 0))
        gst_val   = float(lot.get("gst", 0))
        notes     = str(lot.get("notes", f"Historical buy – {name}")).strip()
        lot_id    = str(lot.get("lot_id", _fingerprint(trade_d, code, qty, price)))

        sett_raw = lot.get("settlement_date")
        sett = _parse_date(sett_raw) if sett_raw else _t2(trade_d)

        rows.append({
            "trade_date":      trade_d,
            "settlement_date": sett,
            "broker":          broker,
            "asset_class":     "equity",
            "code":            code,
            "name":            name,
            "transaction":     "BUY",
            "qty":             qty,
            "direction":       "buy",
            "price":           price,
            "brokerage":       brok,
            "gst":             gst_val,
            "contract_value":  price * qty,
            "net_proceeds":    0.0,
            "description":     notes,
            "reference":       reference,
            "source_file":     "local_cost_base_db",
            "fingerprint":     lot_id,
        })

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows).sort_values("trade_date").reset_index(drop=True)
    print(f"[local_cost_base_db] Loaded {len(result)} lot(s) "
          f"across {result['code'].nunique()} asset(s)")
    return result


def add_historical_lot(
    path: str,
    lot_data: dict,
    disposal_date: date | None = None,
) -> dict:
    """
    Validate, deduplicate, and append a historical BUY lot to the database.

    Required lot_data keys
    ----------------------
    code         : ASX ticker (uppercase)
    trade_date   : original purchase date (any parseable format)
    qty          : quantity bought (must be > 0)
    unit_price   : purchase price per share (must be > 0)

    Optional lot_data keys
    ----------------------
    name, broker, settlement_date, brokerage, gst, reference, notes, source

    disposal_date : if supplied, purchase date must be <= disposal date

    Returns the added lot dict.
    Raises ValueError on validation failure or exact duplicate.
    """
    # ── Required field validation ─────────────────────────────────────────────
    code = str(lot_data.get("code", "")).strip().upper()
    if not code:
        raise ValueError("'code' (ASX ticker) is required")

    trade_d = _parse_date(lot_data.get("trade_date"))
    if trade_d is None:
        raise ValueError(f"Invalid 'trade_date': {lot_data.get('trade_date')!r}")

    qty = _safe_float(lot_data.get("qty", 0))
    if qty <= 0:
        raise ValueError(f"'qty' must be positive, got {qty}")

    price = _safe_float(lot_data.get("unit_price", 0))
    if price <= 0:
        raise ValueError(f"'unit_price' must be positive, got {price}")

    brok = _safe_float(lot_data.get("brokerage", 0))
    if brok < 0:
        raise ValueError(f"'brokerage' cannot be negative, got {brok}")

    gst_val = _safe_float(lot_data.get("gst", 0))
    if gst_val < 0:
        raise ValueError(f"'gst' cannot be negative, got {gst_val}")

    if disposal_date is not None and trade_d > disposal_date:
        raise ValueError(
            f"Purchase date {trade_d} is after disposal date {disposal_date}"
        )

    # ── Derived fields ────────────────────────────────────────────────────────
    broker    = str(lot_data.get("broker", "manual_entry")).strip()
    reference = str(lot_data.get("reference", "")).strip()
    name      = str(lot_data.get("name", code)).strip()
    notes     = str(lot_data.get("notes", "Added to resolve missing buy")).strip()
    source    = str(lot_data.get("source", "manual_missing_buy_entry")).strip()
    cpu       = round(price + (brok + gst_val) / qty, 8)

    sett_raw = lot_data.get("settlement_date")
    sett = _parse_date(sett_raw) if sett_raw else _t2(trade_d)
    trade_str = trade_d.isoformat()
    sett_str  = sett.isoformat() if sett else None

    lot_id = _lot_hash(code, trade_str, qty, price, broker, reference)
    now = _now_iso()

    new_lot: dict = {
        "lot_id":          lot_id,
        "code":            code,
        "name":            name,
        "broker":          broker,
        "trade_date":      trade_str,
        "settlement_date": sett_str,
        "qty_original":    qty,
        "qty_remaining":   qty,
        "unit_price":      price,
        "brokerage":       brok,
        "gst":             gst_val,
        "cost_per_unit":   cpu,
        "source":          source,
        "reference":       reference,
        "notes":           notes,
        "created_at":      now,
        "updated_at":      now,
    }

    db = _load_db(path)
    existing_ids = {lot["lot_id"] for lot in db.get("historical_lots", [])}
    if lot_id in existing_ids:
        raise ValueError(
            f"Duplicate lot: lot_id {lot_id!r} already exists "
            f"({qty} x {code} @ ${price:.4f} on {trade_str})"
        )

    db.setdefault("historical_lots", []).append(new_lot)
    _save_db(path, db)
    print(f"[local_cost_base_db] Added lot {lot_id}: "
          f"{qty} x {code} @ ${price:.4f} on {trade_str}")
    return new_lot


def add_lot_for_missing_buy(
    path: str,
    missing_flag: Any,      # MissingBuyFlag dataclass from equity_engine
    user_input: dict,
    allow_extra: bool = False,
) -> dict:
    """
    Add a historical lot specifically to resolve a MissingBuyFlag.

    Parameters
    ----------
    path          : Path to local_cost_base_db.json
    missing_flag  : MissingBuyFlag from equity_engine
    user_input    : {purchase_date, qty, unit_price, brokerage, gst,
                     broker, reference, notes}
    allow_extra   : If True, qty may exceed qty_unmatched — the surplus
                    becomes an open position.  Default False.

    Returns the added lot dict.
    """
    qty = _safe_float(user_input.get("qty", 0))
    if not allow_extra and qty > missing_flag.qty_unmatched + 1e-6:
        raise ValueError(
            f"qty {qty} exceeds unmatched qty {missing_flag.qty_unmatched:.4f}. "
            "Pass allow_extra=True to allow surplus (becomes open position)."
        )

    lot_data = {
        "code":          str(missing_flag.code).strip().upper(),
        "trade_date":    user_input.get("purchase_date"),
        "qty":           qty,
        "unit_price":    _safe_float(user_input.get("unit_price", 0)),
        "brokerage":     _safe_float(user_input.get("brokerage", 0)),
        "gst":           _safe_float(user_input.get("gst", 0)),
        "broker":        user_input.get("broker") or missing_flag.broker or "manual_entry",
        "reference":     user_input.get("reference", ""),
        "notes":         user_input.get("notes", "Added to resolve missing buy"),
        "name":          user_input.get("name", ""),
        "source":        "manual_missing_buy_entry",
    }
    return add_historical_lot(path, lot_data, disposal_date=missing_flag.disposal_date)


def log_resolution(path: str, before_after_data: dict) -> None:
    """
    Append a resolution_log entry with before/after qty_unmatched summary.

    Required before_after_data keys
    --------------------------------
    code                  : ASX ticker
    sell_date             : disposal date of the SELL being resolved
    qty_unmatched_before  : unmatched qty before adding lots
    qty_added             : total qty added this session
    qty_unmatched_after   : unmatched qty after adding lots
    lot_ids_added         : list of lot_id strings added
    """
    db = _load_db(path)
    entry = {
        "resolution_id":        str(uuid.uuid4()),
        "timestamp":            _now_iso(),
        "code":                 str(before_after_data.get("code", "")),
        "sell_date":            str(before_after_data.get("sell_date", "")),
        "qty_unmatched_before": float(before_after_data.get("qty_unmatched_before", 0)),
        "qty_added":            float(before_after_data.get("qty_added", 0)),
        "qty_unmatched_after":  float(before_after_data.get("qty_unmatched_after", 0)),
        "lot_ids_added":        list(before_after_data.get("lot_ids_added", [])),
    }
    db.setdefault("resolution_log", []).append(entry)
    _save_db(path, db)


def export_local_db_to_cost_base_csv(path: str, csv_path: str) -> None:
    """
    Export local JSON lots into cost_base_history.csv format for compatibility.
    Only active lots (qty_remaining > 0) are exported.
    """
    df = load_local_lots(path)
    if df.empty:
        print("[local_cost_base_db] No active lots to export.")
        return

    export_df = pd.DataFrame({
        "stock_code":    df["code"],
        "purchase_date": df["trade_date"].apply(
            lambda d: d.strftime("%d/%m/%Y") if hasattr(d, "strftime") else str(d)
        ),
        "quantity":  df["qty"],
        "unit_price": df["price"],
        "brokerage":  df["brokerage"],
        "gst":        df["gst"],
        "source":     df["broker"],
    })
    out_dir = os.path.dirname(csv_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    export_df.to_csv(csv_path, index=False)
    print(f"[local_cost_base_db] Exported {len(export_df)} lot(s) -> {csv_path}")


def get_resolution_log(path: str) -> list[dict]:
    """Return the full resolution_log list from the database."""
    ensure_local_db(path)
    db = _load_db(path)
    return db.get("resolution_log", [])


def get_lot_count(path: str) -> int:
    """Return count of stored historical lots (active and inactive)."""
    ensure_local_db(path)
    db = _load_db(path)
    return len(db.get("historical_lots", []))
