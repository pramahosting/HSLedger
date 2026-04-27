"""
multi_file_merger.py — HSLedger Trading Module
Accepts a folder path or explicit list of files. For each file:
  1. Auto-classifies broker + asset class via detect_file_type.py
  2. Routes equity → normaliser, crypto → (placeholder)
  3. Merges all normalised rows into one DataFrame
  4. Runs deduplication
  5. Returns merged canonical DataFrame + load report

This is the primary entry point for multi-broker scenarios.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


def _print(msg: str) -> None:
    """Print with safe ASCII fallback for narrow Windows consoles."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"))

from shared.detect_file_type import detect, CONFIDENCE_THRESHOLD
from shared.normaliser import load_and_normalise
from shared.deduplicator import deduplicate, duplicate_report
from shared.cost_base_loader import load_cost_base_history

SUPPORTED_EXTENSIONS = {".xlsx", ".xlsm", ".xls", ".csv"}


@dataclass
class LoadReport:
    """Summary of what was loaded, skipped, and deduplicated."""
    loaded_files:     list[dict[str, Any]] = field(default_factory=list)
    skipped_files:    list[dict[str, Any]] = field(default_factory=list)
    total_rows:       int = 0
    duplicate_rows:   int = 0
    missing_buy_files: list[str] = field(default_factory=list)

    def print_summary(self) -> None:
        _print(f"\n{'='*60}")
        _print(f"  HSLedger -- Multi-File Load Report")
        _print(f"{'='*60}")
        _print(f"  Files loaded:   {len(self.loaded_files)}")
        for f in self.loaded_files:
            _print(f"    OK  {f['filename']:40s}  {f['broker']:12s}  {f['rows']:>5} rows")
        if self.skipped_files:
            _print(f"\n  Files skipped:  {len(self.skipped_files)}")
            for f in self.skipped_files:
                _print(f"    --  {f['filename']:40s}  {f['reason']}")
        _print(f"\n  Total rows:     {self.total_rows}")
        _print(f"  Duplicates rm:  {self.duplicate_rows}")
        _print(f"{'='*60}\n")


def _gather_files(source: str | list[str]) -> list[str]:
    """Return list of absolute file paths from a folder path or explicit list."""
    if isinstance(source, list):
        return [os.path.abspath(p) for p in source if os.path.isfile(p)]
    if os.path.isdir(source):
        return [
            os.path.join(source, f) for f in sorted(os.listdir(source))
            if os.path.splitext(f)[1].lower() in SUPPORTED_EXTENSIONS
        ]
    if os.path.isfile(source):
        return [os.path.abspath(source)]
    raise ValueError(f"Source not found: {source!r}")


def load_and_merge(
    source: str | list[str],
    cost_base_history_path: str | None = None,
    source_priority: list[str] | None = None,
    skip_crypto: bool = False,
) -> tuple[pd.DataFrame, LoadReport]:
    """
    Main entry point. Loads, classifies, normalises, deduplicates, merges.

    Parameters
    ----------
    source                  : Folder path, single file path, or list of file paths
    cost_base_history_path  : Path to cost_base_history.csv (optional)
    source_priority         : Ordered list of source filenames for dedup priority
    skip_crypto             : If True, crypto files are skipped (equity-only mode)

    Returns
    -------
    (merged_df, load_report)
        merged_df   : Canonical DataFrame with all rows, deduplicated
        load_report : LoadReport dataclass with file-level detail
    """
    paths  = _gather_files(source)
    report = LoadReport()
    frames: list[pd.DataFrame] = []

    # ── Historical cost base (always load first — lowest priority for dedup) ──
    if cost_base_history_path:
        hist_df = load_cost_base_history(cost_base_history_path)
        if not hist_df.empty:
            frames.append(hist_df)
            report.loaded_files.append({
                "filename": os.path.basename(cost_base_history_path),
                "broker":   "cost_base_history",
                "asset_class": "equity",
                "rows":     len(hist_df),
                "confidence": 1.0,
            })

    # ── Process each broker file ──────────────────────────────────────────────
    for path in paths:
        fname = os.path.basename(path)

        # Skip cost base history if passed again in folder
        if fname in ("cost_base_history.csv", "cost_base_history.xlsx"):
            continue

        det = detect(path)

        if det["confidence"] < CONFIDENCE_THRESHOLD:
            report.skipped_files.append({
                "filename": fname,
                "reason": (
                    f"Low confidence ({det['confidence']:.0%}). "
                    f"{det.get('fallback_suggestion', '')}"
                ),
            })
            _print(f"[merger] -- Skipped '{fname}': {report.skipped_files[-1]['reason']}")
            continue

        if skip_crypto and det["asset_class"] == "crypto":
            report.skipped_files.append({
                "filename": fname,
                "reason": "Crypto file skipped (equity-only mode)",
            })
            continue

        try:
            df = load_and_normalise(path)
        except Exception as e:
            report.skipped_files.append({"filename": fname, "reason": str(e)})
            _print(f"[merger] -- Failed '{fname}': {e}")
            continue

        if df.empty:
            report.skipped_files.append({"filename": fname, "reason": "No valid rows after normalisation"})
            continue

        frames.append(df)
        report.loaded_files.append({
            "filename":   fname,
            "broker":     det["broker"],
            "asset_class": det["asset_class"],
            "rows":       len(df),
            "confidence": det["confidence"],
        })
        _print(f"[merger] OK Loaded '{fname}': {det['broker']} ({det['asset_class']}) -- {len(df)} rows")

    if not frames:
        report.print_summary()
        return pd.DataFrame(), report

    # ── Merge all frames ──────────────────────────────────────────────────────
    merged = pd.concat(frames, ignore_index=True)
    report.total_rows = len(merged)

    # ── Deduplicate ───────────────────────────────────────────────────────────
    clean, dups = deduplicate(merged, source_priority=source_priority)
    report.duplicate_rows = len(dups)

    if not dups.empty:
        _print(f"\n[merger] Duplicate report:\n{duplicate_report(dups)}")

    report.total_rows = len(clean)
    report.print_summary()

    return clean, report


def split_by_asset_class(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Split merged DataFrame into equity and crypto sub-frames."""
    result = {}
    if "asset_class" not in df.columns:
        return {"equity": df}
    for ac in df["asset_class"].unique():
        result[ac] = df[df["asset_class"] == ac].reset_index(drop=True)
    return result


def split_by_fy(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Split a canonical DataFrame by ATO financial year (July 1 cutoff)."""
    if df.empty or "trade_date" not in df.columns:
        return {}

    def _fy(d) -> str:
        if pd.isna(d):
            return "unknown"
        if hasattr(d, "month"):
            return f"{d.year}-{str(d.year+1)[2:]}" if d.month >= 7 else f"{d.year-1}-{str(d.year)[2:]}"
        return "unknown"

    df = df.copy()
    df["_fy"] = df["trade_date"].apply(_fy)
    result = {fy: grp.drop(columns=["_fy"]).reset_index(drop=True)
              for fy, grp in df.groupby("_fy")}
    df.drop(columns=["_fy"], inplace=True)
    return result
