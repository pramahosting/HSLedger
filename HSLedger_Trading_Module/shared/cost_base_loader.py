"""
cost_base_loader.py — HSLedger Trading Module
Loads cost_base_history.csv and converts it to canonical normalised rows
that can be prepended to the main transaction DataFrame before CGT processing.

The historical rows are marked source_file="cost_base_history" and
direction="buy" so they flow through the FIFO engine as prior-year lots.

Expected CSV columns (flexible — mapped by keyword matching):
    stock_code | purchase_date | quantity | unit_price | brokerage | gst | source

Also handles the "missing buy" interactive prompt:
    When the CGT engine flags an unmatched sell, this module provides
    the prompt_missing_buy() function which collects the buy details
    interactively and returns a canonical row.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import pandas as pd

from shared.normaliser import _parse_date, _safe_float, _fingerprint, _t2


HISTORY_COL_ALIASES = {
    "code":        ["stock_code", "code", "asset_code", "ticker", "symbol"],
    "name":        ["stock_name", "name", "asset_name", "company"],
    "date":        ["purchase_date", "trade_date", "date", "acquisition_date"],
    "qty":         ["quantity", "qty", "units", "amount"],
    "price":       ["unit_price", "price", "cost_per_unit"],
    "brokerage":   ["brokerage", "commission", "fee"],
    "gst":         ["gst"],
    "source":      ["source", "broker", "notes"],
}


def _resolve_col(df_cols: list[str], aliases: list[str]) -> str | None:
    """Return first df column that matches any alias (case-insensitive)."""
    cols_lower = {c.lower(): c for c in df_cols}
    for alias in aliases:
        if alias.lower() in cols_lower:
            return cols_lower[alias.lower()]
    return None


def load_cost_base_history(path: str) -> pd.DataFrame:
    """
    Load cost_base_history.csv and return a canonical DataFrame
    (same schema as normalise() output) ready to prepend to the main data.

    Returns empty DataFrame if file not found (non-fatal).
    """
    if not path or not os.path.exists(path):
        print(f"[cost_base_loader] No history file at '{path}' — skipping.")
        return pd.DataFrame()

    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        df = pd.read_csv(path)
    elif ext in (".xlsx", ".xlsm"):
        df = pd.read_excel(path)
    else:
        print(f"[cost_base_loader] Unsupported history file type: {ext}")
        return pd.DataFrame()

    cols = list(df.columns)

    # Resolve column names flexibly
    def col(key: str) -> str | None:
        return _resolve_col(cols, HISTORY_COL_ALIASES[key])

    rows_out = []
    skipped  = 0

    for _, row in df.iterrows():
        def g(key: str, default: Any = None) -> Any:
            c = col(key)
            if c is None:
                return default
            v = row.get(c)
            return default if pd.isna(v) else v

        code      = str(g("code", "")).strip().upper()
        name      = str(g("name", code)).strip()
        trade_d   = _parse_date(g("date"))
        qty       = _safe_float(g("qty", 0))
        price     = _safe_float(g("price", 0))
        brokerage = _safe_float(g("brokerage", 0))
        gst       = _safe_float(g("gst", 0))
        source    = str(g("source", "cost_base_history")).strip()

        if not code or not trade_d or qty <= 0:
            skipped += 1
            continue

        sett = _t2(trade_d)

        rows_out.append({
            "trade_date":      trade_d,
            "settlement_date": sett,
            "broker":          source,
            "asset_class":     "equity",
            "code":            code,
            "name":            name,
            "transaction":     "BUY",
            "qty":             qty,
            "direction":       "buy",
            "price":           price,
            "brokerage":       brokerage,
            "gst":             gst,
            "contract_value":  price * qty,
            "net_proceeds":    0.0,
            "description":     f"Historical buy – {name}",
            "reference":       "",
            "source_file":     "cost_base_history",
            "fingerprint":     _fingerprint(trade_d, code, qty, price),
        })

    if skipped:
        print(f"[cost_base_loader] Skipped {skipped} invalid history rows")

    result = pd.DataFrame(rows_out)
    if not result.empty:
        result = result.sort_values("trade_date").reset_index(drop=True)
        print(f"[cost_base_loader] Loaded {len(result)} historical lots "
              f"across {result['code'].nunique()} assets")
    return result


def prompt_missing_buy(
    code: str,
    qty_needed: float,
    disposal_date: date,
    broker: str = "manual_entry",
    reference: str = "",
    sale_price: float = 0.0,
) -> pd.DataFrame | None:
    """
    Interactive terminal prompt for a SELL that has no matching BUY.

    Presents the sell details, asks for the original purchase details,
    and returns a canonical single-row DataFrame ready to inject into
    the FIFO queue.  Returns None if the user skips.

    Note: BUY positions with no matching SELL are NOT passed here —
    those are carried forward automatically as open positions.
    """
    line = "-" * 62

    print(f"\n{line}")
    print(f"  MISSING BUY -- Action Required")
    print(f"{line}")
    print(f"  Asset:        {code}")
    print(f"  Sell date:    {disposal_date.strftime('%d/%m/%Y')}")
    print(f"  Qty missing:  {qty_needed:,.4f} shares")
    if sale_price > 0:
        print(f"  Sale price:   ${sale_price:.4f} per share")
        held_hint = (
            "  CGT discount: 50% discount applies if held > 365 days before this sale."
        )
        print(held_hint)
    if broker:
        ref_str = f"  Ref: {reference}" if reference else ""
        print(f"  Broker:       {broker.title()}{('  ' + ref_str) if ref_str else ''}")
    print(f"{line}")
    print("  Enter the original purchase details.")
    print("  Press Enter (blank) on any field to skip this transaction.")
    print(f"{line}\n")

    try:
        date_str = input("  Purchase date (dd/mm/yyyy): ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n  Cancelled.")
        return None

    if not date_str:
        print("  Skipped -- this sell will remain flagged as unmatched.\n")
        return None

    trade_d = _parse_date(date_str)
    if trade_d is None:
        print(f"  Could not parse '{date_str}' -- skipped.\n")
        return None

    # Show discount hint based on entered date
    if sale_price > 0:
        held = (disposal_date - trade_d).days
        disc_note = (
            f"  (Held {held} days -- 50% CGT discount APPLIES)"
            if held > 365
            else f"  (Held {held} days -- below 365 days, no CGT discount)"
        )
        print(disc_note)

    try:
        qty_str   = input(f"  Quantity purchased (need >= {qty_needed:,.0f}): ").strip()
        price_str = input("  Purchase price per unit ($):                ").strip()
        brok_str  = input("  Brokerage ($)  [Enter for 0]:               ").strip() or "0"
        gst_str   = input("  GST ($)        [Enter for 0]:               ").strip() or "0"
        name_str  = input(f"  Asset name     [Enter for '{code}']:          ").strip() or code
    except (KeyboardInterrupt, EOFError):
        print("\n  Cancelled.")
        return None

    qty   = _safe_float(qty_str)
    price = _safe_float(price_str)
    brok  = _safe_float(brok_str)
    gst   = _safe_float(gst_str)

    if qty <= 0 or price <= 0:
        print("  Invalid quantity or price -- skipped.\n")
        return None

    cpu  = price + (brok + gst) / qty
    sett = _t2(trade_d)

    # Show gain/loss preview if we have the sale price
    if sale_price > 0:
        held   = (disposal_date - trade_d).days
        disc   = held > 365
        gross  = round((sale_price - cpu) * min(qty, qty_needed), 2)
        after  = round(gross * 0.5 if (disc and gross > 0) else gross, 2)
        gl     = "gain" if after >= 0 else "loss"
        print(f"\n  Cost/unit (incl. brokerage): ${cpu:.4f}")
        print(f"  Estimated capital {gl}:       ${abs(after):,.2f}"
              + (" (after 50% discount)" if disc and gross > 0 else ""))

    print(f"\n  Recorded: {qty:,.0f} x {code} @ ${price:.4f} on {trade_d.strftime('%d/%m/%Y')}\n")

    row = {
        "trade_date":      trade_d,
        "settlement_date": sett,
        "broker":          broker,
        "asset_class":     "equity",
        "code":            code.upper(),
        "name":            name_str,
        "transaction":     "BUY",
        "qty":             qty,
        "direction":       "buy",
        "price":           price,
        "brokerage":       brok,
        "gst":             gst,
        "contract_value":  price * qty,
        "net_proceeds":    0.0,
        "description":     f"Manual entry -- historical buy for {code}",
        "reference":       "MANUAL",
        "source_file":     "manual_entry",
        "fingerprint":     _fingerprint(trade_d, code, qty, price),
    }
    return pd.DataFrame([row])
