"""
deduplicator.py — HSLedger Trading Module
Fingerprint-based duplicate detection across merged broker files.

A duplicate is a row where (trade_date, code, qty, price) hash matches
another row already seen — most commonly when a user provides both a
broker export AND a manual cost_base_history file with overlapping rows.

Strategy:
  - KEEP the first occurrence (from highest-priority source)
  - FLAG all subsequent matches as duplicates
  - Return clean DataFrame + duplicate report
"""

from __future__ import annotations
from collections import defaultdict
import pandas as pd


def deduplicate(
    df: pd.DataFrame,
    source_priority: list[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Remove duplicate rows based on fingerprint column.

    Parameters
    ----------
    df               : Merged canonical DataFrame (must have 'fingerprint' and 'source_file' cols)
    source_priority  : Optional ordered list of source_file values; earlier = higher priority.
                       If not provided, order of rows in df is used as-is.

    Returns
    -------
    (clean_df, duplicates_df)
        clean_df      : df with duplicates removed
        duplicates_df : rows that were removed, with 'duplicate_of_source' column added
    """
    if "fingerprint" not in df.columns:
        # No fingerprints → nothing to deduplicate
        return df.copy(), pd.DataFrame()

    if source_priority:
        priority_map = {src: i for i, src in enumerate(source_priority)}
        df = df.copy()
        df["_sort_key"] = df["source_file"].map(lambda s: priority_map.get(s, 9999))
        df = df.sort_values("_sort_key").drop(columns=["_sort_key"]).reset_index(drop=True)

    seen:        dict[str, int]  = {}   # fingerprint → first row index
    keep_flags:  list[bool]      = []
    dup_sources: list[str]       = []

    for idx, row in df.iterrows():
        fp = row["fingerprint"]
        if fp in seen:
            keep_flags.append(False)
            first_src = df.at[seen[fp], "source_file"]
            dup_sources.append(first_src)
        else:
            seen[fp] = idx
            keep_flags.append(True)
            dup_sources.append("")

    df = df.copy()
    df["_keep"]         = keep_flags
    df["_dup_of_source"] = dup_sources

    clean_df = df[df["_keep"]].drop(columns=["_keep", "_dup_of_source"]).reset_index(drop=True)
    dup_df   = df[~df["_keep"]].copy()
    dup_df   = dup_df.rename(columns={"_dup_of_source": "duplicate_of_source"})
    dup_df   = dup_df.drop(columns=["_keep"]).reset_index(drop=True)

    if len(dup_df):
        print(f"[deduplicator] Removed {len(dup_df)} duplicate row(s) across {df['source_file'].nunique()} file(s)")

    return clean_df, dup_df


def duplicate_report(dup_df: pd.DataFrame) -> str:
    """Return a human-readable duplicate summary."""
    if dup_df.empty:
        return "No duplicates detected."
    lines = [f"Duplicates removed: {len(dup_df)}", ""]
    for _, row in dup_df.iterrows():
        lines.append(
            f"  {row.get('trade_date')} | {row.get('code')} | "
            f"qty={row.get('qty')} | price={row.get('price')} | "
            f"source='{row.get('source_file')}' -- duplicate of '{row.get('duplicate_of_source')}'"
        )
    return "\n".join(lines)
