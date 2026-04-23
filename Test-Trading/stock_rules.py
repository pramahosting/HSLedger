
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

DEFAULT_TEXT_FIELDS = [
    "Transaction", "Order Type", "Transaction Type",
    "Description", "Notes",
    "Stock Code", "Ticker", "Code", "Security Code", "ASX Code",
    "Stock Name", "Company", "Name", "Security Name",
    "Exchange", "Market",
]

TRANSACTION_COLS = ["Transaction", "Order Type", "Transaction Type"]
QUANTITY_COLS = ["Quantity", "Units"]
SYMBOL_COLS = ["Stock Code", "Ticker", "Code", "Security Code", "ASX Code"]


def _get_first(row: pd.Series, cols):
    for c in cols:
        if c in row.index:
            v = row.get(c)
            if v is not None and not (isinstance(v, float) and np.isnan(v)):
                return v
    return None

def _safe_text(x) -> str:
    if x is None:
        return ""
    if isinstance(x, float) and np.isnan(x):
        return ""
    return re.sub(r"\s+", " ", str(x)).strip()

def _safe_float(x):
    try:
        if pd.isna(x):
            return np.nan
        return float(x)
    except Exception:
        return np.nan

def load_rules(path: str) -> List[Dict[str, Any]]:
    if not path or not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        rules = json.load(f)
    if not isinstance(rules, list):
        raise ValueError("Rules JSON must be a list.")
    return sorted(rules, key=lambda r: int(r.get("priority", 0)), reverse=True)

def _row_any_field_text(row: pd.Series, text_fields: List[str]) -> str:
    vals = []
    for c in text_fields:
        if c in row.index:
            t = _safe_text(row.get(c))
            if t:
                vals.append(t)
    return " | ".join(vals)

def _contains_any(text: str, needles: List[str]) -> bool:
    txt = (text or "").lower()
    return any(str(n).lower() in txt for n in needles)

def _regex_any(text: str, patterns: List[str]) -> bool:
    txt = text or ""
    for p in patterns:
        try:
            if re.search(p, txt, flags=re.IGNORECASE):
                return True
        except re.error:
            continue
    return False

def match_rule(row: pd.Series, rule: Dict[str, Any], *, text_fields: List[str]) -> bool:
    cond = rule.get("if", {}) or {}
    if not isinstance(cond, dict):
        return False

    tx_code = _safe_text(_get_first(row, TRANSACTION_COLS)).upper()
    qty = _safe_float(_get_first(row, QUANTITY_COLS))
    symbol = _safe_text(_get_first(row, SYMBOL_COLS)).upper()
    big_text = _row_any_field_text(row, text_fields)

    if "transaction_in" in cond:
        allowed = {str(x).upper() for x in cond.get("transaction_in", [])}
        if tx_code not in allowed:
            return False

    if "symbol_in" in cond:
        allowed = {str(x).upper() for x in cond.get("symbol_in", [])}
        if symbol not in allowed:
            return False

    if "qty_lt" in cond and not (qty < float(cond["qty_lt"])):
        return False
    if "qty_gt" in cond and not (qty > float(cond["qty_gt"])):
        return False

    if "any_field_contains_any" in cond:
        needles = cond.get("any_field_contains_any", [])
        if not isinstance(needles, list) or not _contains_any(big_text, needles):
            return False

    if "any_field_regex_any" in cond:
        pats = cond.get("any_field_regex_any", [])
        if not isinstance(pats, list) or not _regex_any(big_text, pats):
            return False

    return True

def apply_stock_event_rules(
    df: pd.DataFrame,
    rules_path: str,
    *,
    text_fields: Optional[List[str]] = None,
    out_col: str = "_rule_applied",
    target_col: str = "event_type",
) -> Tuple[pd.DataFrame, List[str]]:
    work = df.copy()
    work[target_col] = ""
    tf = text_fields or DEFAULT_TEXT_FIELDS
    rules = load_rules(rules_path)
    warnings: List[str] = []

    if not rules:
        work[out_col] = ""
        return work, warnings

    applied = []
    for i, row in work.iterrows():
        matched_id = ""
        for rule in rules:
            then = str(rule.get("then", "")).strip()
            if not then:
                continue
            if match_rule(row, rule, text_fields=tf):
                work.at[i, target_col] = then
                matched_id = str(rule.get("id", "")) or "rule"
                break
        applied.append(matched_id)

    work[out_col] = applied
    empty_mask = work[target_col].astype(str).str.strip().eq("")
    if empty_mask.any():
        missing = int(empty_mask.sum())
        warnings.append(f"{missing} row(s) were not classified by rules and were marked UNKNOWN.")
        work.loc[empty_mask, target_col] = "UNKNOWN"

    return work, warnings
