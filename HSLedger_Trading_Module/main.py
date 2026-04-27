"""
main.py — HSLedger Trading ModuleS
Master orchestrator. Wires all sub-modules together in the correct order.

Local Windows paths (PyCharm):
  Input:   C:\\Users\\alfsj\\PycharmProjects\\HSLedger_Trading_Module\\trading\\input
  Output:  C:\\Users\\alfsj\\PycharmProjects\\HSLedger_Trading_Module\\trading\\output

These are set in config.py and used as defaults throughout.
Override them on the CLI with --input / --output, or pass them
directly to run_trading_pipeline() in code.

Pipeline:
  1. multi_file_merger   → load + classify + normalise + deduplicate → merged_df
  2. split by FY         → per-FY DataFrames
  3. cost_base_loader    → prepend historical lots from cost_base_history.csv
  4. equity_engine       → FIFO CGT calculation → disposals, income, summaries
  5. missing buy handler → flag or interactively resolve
  6. excel_exporter      → final .xlsx written to trading/output/

Usage (CLI — no arguments needed, uses config.py defaults):
    python main.py

    # Override input/output
    python main.py --input "C:/path/to/files" --output "C:/path/to/report.xlsx"

    # Specific FY
    python main.py --fy 2024-25

    # Interactive missing-buy prompt
    python main.py --interactive

    # Show resolved paths and exit
    python main.py --config

Usage (API — called from Streamlit or other code):
    from main import run_trading_pipeline
    result = run_trading_pipeline()                    # uses config.py defaults
    result = run_trading_pipeline(
        source="custom/input/path",
        output_path="custom/output/report.xlsx",
        target_fy="2024-25",
    )
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

# ── Ensure trading module root is on sys.path ─────────────────────────────────
_MODULE_ROOT = os.path.dirname(os.path.abspath(__file__))
if _MODULE_ROOT not in sys.path:
    sys.path.insert(0, _MODULE_ROOT)

from shared.multi_file_merger import load_and_merge, split_by_fy, split_by_asset_class, LoadReport
from shared.cost_base_loader import load_cost_base_history
from shared.deduplicator import deduplicate
from shared.local_cost_base_db import (
    ensure_local_db, load_local_lots,
    add_lot_for_missing_buy, log_resolution, get_resolution_log,
)
from equity.equity_engine import (
    compute_cgt, disposals_to_df, income_to_df, missing_to_df, summary_to_df,
    option_flags_to_df,
    DisposalRow, IncomeRow, FYSummary, MissingBuyFlag, OptionFlag, Lot, _fy,
)
from output.excel_exporter import export_to_excel
from config import (
    INPUT_DIR_STR,
    OUTPUT_DIR_STR,
    COST_BASE_HISTORY_STR,
    DEFAULT_REPORT_PATH,
    DEFAULT_TARGET_FY,
    CARRY_FORWARD_LOSSES,
    LOCAL_COST_BASE_DB_STR,
    print_config,
)


# ── Result container ──────────────────────────────────────────────────────────

@dataclass
class TradingPipelineResult:
    """All outputs from a completed pipeline run."""
    disposals:       list[DisposalRow]         = field(default_factory=list)
    income_events:   list[IncomeRow]           = field(default_factory=list)
    fy_summaries:    dict[str, FYSummary]      = field(default_factory=dict)
    missing_flags:   list[MissingBuyFlag]      = field(default_factory=list)
    option_flags:    list[OptionFlag]          = field(default_factory=list)
    open_positions:  dict[str, deque[Lot]]     = field(default_factory=dict)
    duplicates_df:   pd.DataFrame              = field(default_factory=pd.DataFrame)
    load_report:     LoadReport | None         = None
    excel_bytes:     bytes | None              = None
    excel_path:      str | None               = None

    @property
    def disposals_df(self) -> pd.DataFrame:
        return disposals_to_df(self.disposals)

    @property
    def income_df(self) -> pd.DataFrame:
        return income_to_df(self.income_events)

    @property
    def missing_df(self) -> pd.DataFrame:
        return missing_to_df(self.missing_flags)

    @property
    def option_flags_df(self) -> pd.DataFrame:
        return option_flags_to_df(self.option_flags)

    @property
    def summary_df(self) -> pd.DataFrame:
        return summary_to_df(self.fy_summaries)

    def print_summary(self) -> None:
        print(f"\n{'='*65}")
        print(f"  HSLedger -- Trading Module Results")
        print(f"{'='*65}")
        for s in sorted(self.fy_summaries.values(), key=lambda x: x.financial_year):
            print(f"\n  FY {s.financial_year}")
            print(f"    Brokers:             {', '.join(s.brokers) or '—'}")
            print(f"    Disposals:           {s.disposal_count}")
            print(f"    Total Proceeds:      ${s.total_proceeds:>12,.2f}")
            print(f"    Total Cost Base:     ${s.total_cost_base:>12,.2f}")
            print(f"    Gross Gains:         ${s.gross_gains:>12,.2f}")
            print(f"    Gross Losses:        ${s.gross_losses:>12,.2f}")
            print(f"    CGT Discount:        ${s.cgt_discount_applied:>12,.2f}")
            print(f"    Net Taxable Gain:    ${s.net_after_carryforward:>12,.2f}")
            print(f"    Income:              ${s.total_income:>12,.2f}")
            if s.missing_buys:
                print(f"    ! Missing Buys:    {len(s.missing_buys)} sells unmatched")
        if self.option_flags:
            print(f"\n  ! Unresolved options:  {len(self.option_flags)} positions need outcome")
        if self.open_positions:
            total_lots = sum(len(q) for q in self.open_positions.values())
            print(f"\n  Open positions:        {len(self.open_positions)} assets / {total_lots} lots")
        if self.excel_path:
            print(f"\n  Report saved:          {self.excel_path}")
        print(f"{'='*65}\n")


# ── History → initial FIFO queues ─────────────────────────────────────────────

def _build_initial_queues(history_df: pd.DataFrame) -> dict[str, deque[Lot]]:
    """Convert historical cost-base rows into pre-populated FIFO queues."""
    from collections import defaultdict
    queues: dict = defaultdict(deque)
    if history_df.empty:
        return queues
    hist = history_df.sort_values("trade_date")
    for _, row in hist.iterrows():
        code  = str(row.get("code", "")).strip().upper()
        qty   = float(row.get("qty", 0))
        price = float(row.get("price", 0))
        brok  = float(row.get("brokerage", 0))
        gst   = float(row.get("gst", 0))
        td    = row.get("trade_date")
        sd    = row.get("settlement_date")
        if not code or qty <= 0 or td is None:
            continue
        cpu = price + (brok + gst) / qty
        queues[code].append(Lot(
            code=code, qty=qty, cost_per_unit=cpu,
            trade_date=td if isinstance(td, date) else td.date() if hasattr(td, "date") else td,
            settlement_date=sd if sd and not pd.isna(sd) else None,
            broker=str(row.get("broker", "history")),
        ))
    return dict(queues)


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_trading_pipeline(
    source:                  str | list[str] | None = None,
    cost_base_history_path:  str | None = None,
    local_db_path:           str | None = None,
    output_path:             str | None = None,
    target_fy:               str | None = None,
    carry_forward_losses:    dict[str, float] | None = None,
    interactive_missing:     bool = False,
    source_priority:         list[str] | None = None,
    skip_crypto:             bool = True,
) -> TradingPipelineResult:
    """
    Full pipeline: load → normalise → dedup → CGT → export.

    All parameters fall back to config.py values when not supplied.

    Parameters
    ----------
    source                  : Input folder or file path(s).
    cost_base_history_path  : Path to cost_base_history.csv.
    local_db_path           : Path to local_cost_base_db.json.
                              Default: data/local_cost_base_db.json
    output_path             : Where to write the .xlsx report.
    target_fy               : Only process this FY e.g. "2024-25".
                              Pass None explicitly to process all years.
    carry_forward_losses    : dict {FY: loss_amount} from prior years.
    interactive_missing     : Prompt stdin for missing buys. Default: False
    source_priority         : File priority order for deduplication.
    skip_crypto             : Skip crypto files. Default: True

    Returns
    -------
    TradingPipelineResult
    """
    # ── Apply config defaults for any unset parameters ────────────────────────
    if source is None:
        source = INPUT_DIR_STR
    if cost_base_history_path is None:
        cost_base_history_path = COST_BASE_HISTORY_STR
    if local_db_path is None:
        local_db_path = LOCAL_COST_BASE_DB_STR
    if output_path is None:
        output_path = DEFAULT_REPORT_PATH
    if target_fy is None:
        target_fy = DEFAULT_TARGET_FY
    if carry_forward_losses is None:
        carry_forward_losses = CARRY_FORWARD_LOSSES.copy()

    # Ensure local DB exists (no-op if already created)
    ensure_local_db(local_db_path)

    # Ensure output directory exists
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    print(f"\n[pipeline] Input:   {source}")
    print(f"[pipeline] Output:  {output_path}")
    print(f"[pipeline] FY:      {target_fy or 'all years'}")

    result = TradingPipelineResult()

    # ── Step 1: Load historical lots (CSV + local JSON DB) ────────────────────
    history_df = pd.DataFrame()
    if cost_base_history_path and os.path.exists(cost_base_history_path):
        history_df = load_cost_base_history(cost_base_history_path)
    elif cost_base_history_path:
        print(f"[pipeline] History file not found at '{cost_base_history_path}' — skipping.")

    local_history_df = load_local_lots(local_db_path)

    # Combine CSV history + local DB lots into one set of initial FIFO queues
    combined_history_dfs = [df for df in [history_df, local_history_df] if not df.empty]
    if combined_history_dfs:
        combined_history_df = pd.concat(combined_history_dfs, ignore_index=True)
        initial_queues = _build_initial_queues(combined_history_df)
        print(f"[pipeline] Initial queues: {len(initial_queues)} asset(s) pre-seeded "
              f"({sum(len(q) for q in initial_queues.values())} lot(s) total)")
    else:
        initial_queues = {}

    # ── Step 2: Load and merge all broker files ────────────────────────────────
    merged_df, report = load_and_merge(
        source                 = source,
        cost_base_history_path = None,   # history loaded separately above
        source_priority        = source_priority,
        skip_crypto            = skip_crypto,
    )
    result.load_report = report

    if merged_df.empty:
        print("[pipeline] No data to process after loading files.")
        print(f"[pipeline] Make sure broker files are in: {source}")
        return result

    # ── Step 3: Separate equity data ──────────────────────────────────────────
    by_asset  = split_by_asset_class(merged_df)
    equity_df = by_asset.get("equity", pd.DataFrame())

    if equity_df.empty:
        print("[pipeline] No equity transactions found.")
        return result

    # ── Step 4: Filter to target FY (keep prior-year buys for FIFO) ───────────
    if target_fy:
        fy_splits      = split_by_fy(equity_df)
        all_fys_sorted = sorted(fy_splits.keys())
        keep_fys       = [fy for fy in all_fys_sorted if fy <= target_fy]
        if not keep_fys:
            print(f"[pipeline] No data found for FY {target_fy}.")
            print(f"[pipeline] Available FYs: {all_fys_sorted}")
            return result
        equity_df = pd.concat([fy_splits[fy] for fy in keep_fys], ignore_index=True)

    # ── Step 5: Run CGT engine ────────────────────────────────────────────────
    print(f"[pipeline] Running CGT engine on {len(equity_df)} broker rows...")

    disposals, income_events, fy_summaries, open_queues, missing_flags, option_flags = compute_cgt(
        df                   = equity_df,
        carry_forward_losses = carry_forward_losses,
        initial_queues       = initial_queues or None,
        interactive_missing  = interactive_missing,
    )

    result.disposals      = disposals
    result.income_events  = income_events
    result.fy_summaries   = fy_summaries
    result.missing_flags  = missing_flags
    result.option_flags   = option_flags
    result.open_positions = open_queues

    # ── Step 6: Report missing buys and option flags ──────────────────────────
    if missing_flags:
        print(f"\n[pipeline] !  {len(missing_flags)} unmatched sell(s) -- no matching buy found:")
        for m in missing_flags:
            print(f"  {m.disposal_date} | {m.code} | qty={m.qty_unmatched:.4f} | broker={m.broker}")
        print(f"\n  -> Add missing purchase details to:")
        print(f"    {cost_base_history_path}")
        print(f"  -> Or resolve via the Streamlit UI")
    if option_flags:
        print(f"\n[pipeline] !  {len(option_flags)} option position(s) with no close event:")
        for o in option_flags:
            print(f"  {o.option_date} | {o.code} | qty={o.qty:.0f} | premium=${o.premium_paid:.4f}")
        print(f"\n  -> Specify outcome (expired/sold/exercised) via the Streamlit UI")

    # ── Step 7: Export Excel to trading/output/ ───────────────────────────────
    print(f"\n[pipeline] Writing report -> {output_path}")
    resolution_log = get_resolution_log(local_db_path) if local_db_path else []
    excel_bytes = export_to_excel(
        disposals       = disposals,
        income_events   = income_events,
        fy_summaries    = fy_summaries,
        missing_flags   = missing_flags,
        open_positions  = open_queues,
        duplicates_df   = result.duplicates_df,
        resolution_log  = resolution_log,
        output_path     = output_path,
    )
    result.excel_bytes = excel_bytes
    result.excel_path  = output_path

    result.print_summary()
    return result


# ── CLI entry point ───────────────────────────────────────────────────────────

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="HSLedger Trading Module — Equity CGT Calculator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Default paths (from config.py):
  Input:   {INPUT_DIR_STR}
  Output:  {DEFAULT_REPORT_PATH}
  History: {COST_BASE_HISTORY_STR}

Examples:
  # Run with all defaults (reads from trading/input, writes to trading/output)
  python main.py

  # Specific FY only
  python main.py --fy 2024-25

  # Override input folder
  python main.py --input "C:/Users/alfsj/Downloads/broker_exports"

  # Override output path
  python main.py --output "C:/Users/alfsj/Desktop/MyTaxReport.xlsx"

  # Interactive missing-buy prompt
  python main.py --interactive

  # Show resolved config paths and exit
  python main.py --config
        """
    )
    parser.add_argument(
        "--input", "-i",
        default=INPUT_DIR_STR,
        help=f"Folder or file path(s) with broker transaction files "
             f"(default: {INPUT_DIR_STR})",
    )
    parser.add_argument(
        "--history", "-H",
        default=COST_BASE_HISTORY_STR,
        help=f"Path to cost_base_history.csv "
             f"(default: {COST_BASE_HISTORY_STR})",
    )
    parser.add_argument(
        "--output", "-o",
        default=DEFAULT_REPORT_PATH,
        help=f"Output Excel report path "
             f"(default: {DEFAULT_REPORT_PATH})",
    )
    parser.add_argument(
        "--fy", "-f",
        default=DEFAULT_TARGET_FY,
        help=f"Target financial year e.g. '2024-25' (default: {DEFAULT_TARGET_FY}). "
             f"Pass 'all' to process every year in the files.",
    )
    parser.add_argument(
        "--interactive", "-I",
        action="store_true",
        help="Prompt for missing buy details interactively on stdin",
    )
    parser.add_argument(
        "--carry-forward", "-c",
        default=None,
        help='JSON dict of carry-forward losses e.g. \'{"2023-24": 5000}\'',
    )
    parser.add_argument(
        "--local-db",
        default=LOCAL_COST_BASE_DB_STR,
        help=f"Path to local_cost_base_db.json "
             f"(default: {LOCAL_COST_BASE_DB_STR})",
    )
    parser.add_argument(
        "--resolve-missing",
        action="store_true",
        help=(
            "After the initial CGT run, prompt for missing buy details, "
            "save them permanently to local_cost_base_db.json, then re-run "
            "and print a before/after resolution summary."
        ),
    )
    parser.add_argument(
        "--config",
        action="store_true",
        help="Print resolved config paths and exit",
    )

    args = parser.parse_args()

    if args.config:
        print_config()
        return

    cf_losses = CARRY_FORWARD_LOSSES.copy()
    if args.carry_forward:
        import json
        cf_losses = json.loads(args.carry_forward)

    # "all" → process every FY
    target_fy = None if args.fy and args.fy.lower() == "all" else args.fy

    # Support space-separated multiple input paths
    source: str | list[str] = args.input
    if args.input and " " in args.input and not os.path.exists(args.input):
        source = args.input.split()

    pipeline_kwargs: dict = dict(
        source                 = source,
        cost_base_history_path = args.history,
        local_db_path          = args.local_db,
        output_path            = args.output,
        target_fy              = target_fy,
        carry_forward_losses   = cf_losses,
        interactive_missing    = args.interactive,
    )

    result = run_trading_pipeline(**pipeline_kwargs)

    if args.resolve_missing and result.missing_flags:
        _resolve_missing_cli(result, args.local_db, pipeline_kwargs)


def _resolve_missing_cli(
    result: TradingPipelineResult,
    local_db_path: str,
    pipeline_kwargs: dict,
) -> None:
    """
    Interactive CLI loop: prompt user for missing buy details, save to local DB,
    re-run the pipeline, print before/after progress table.
    """
    from shared.normaliser import _parse_date, _safe_float

    line = "─" * 65
    print(f"\n{line}")
    print(f"  MISSING BUY RESOLUTION  ({len(result.missing_flags)} unmatched sell(s))")
    print(f"{line}")
    print("  These SELL transactions have no matching BUY lots in the data.")
    print("  Enter historical purchase details to resolve them permanently.")
    print(f"{line}\n")

    session_lots: list[str] = []   # lot_ids added this session

    for flag in result.missing_flags:
        print(f"\n  Asset:        {flag.code}")
        print(f"  Sell date:    {flag.disposal_date.strftime('%d/%m/%Y')}")
        print(f"  Qty missing:  {flag.qty_unmatched:,.4f} shares")
        if flag.proceeds_per_unit:
            print(f"  Sale price:   ${flag.proceeds_per_unit:.4f}/share")
        if flag.broker:
            print(f"  Broker:       {flag.broker}")
        if flag.reference:
            print(f"  Reference:    {flag.reference}")
        print()

        qty_remaining = flag.qty_unmatched

        while qty_remaining > 1e-6:
            print(f"  Qty still needed: {qty_remaining:,.4f}")
            try:
                date_str = input("  Purchase date (dd/mm/yyyy) [Enter to skip]: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\n  Cancelled.")
                return

            if not date_str:
                print("  Skipped — this sell will remain flagged as unmatched.")
                break

            purchase_date = _parse_date(date_str)
            if purchase_date is None:
                print(f"  Cannot parse '{date_str}' — try dd/mm/yyyy format.")
                continue
            if purchase_date > flag.disposal_date:
                print(f"  Purchase date {purchase_date} must be before sell date {flag.disposal_date}.")
                continue

            try:
                qty_str   = input(f"  Quantity purchased (need {qty_remaining:,.0f}): ").strip()
                price_str = input("  Purchase price per unit ($): ").strip()
                brok_str  = input("  Brokerage ($) [Enter for 0]: ").strip() or "0"
                gst_str   = input("  GST ($) [Enter for 0]: ").strip() or "0"
                broker_str = input(f"  Broker [Enter for '{flag.broker or 'manual_entry'}']: ").strip()
                ref_str   = input("  Reference [Enter for blank]: ").strip()
                notes_str = input("  Notes [Enter for default]: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\n  Cancelled.")
                return

            qty   = _safe_float(qty_str)
            price = _safe_float(price_str)
            brok  = _safe_float(brok_str)
            gst   = _safe_float(gst_str)

            if qty <= 0 or price <= 0:
                print("  Invalid quantity or price — try again.")
                continue

            allow_extra = qty > qty_remaining + 1e-6

            user_input = {
                "purchase_date": purchase_date,
                "qty":           qty,
                "unit_price":    price,
                "brokerage":     brok,
                "gst":           gst,
                "broker":        broker_str or flag.broker or "manual_entry",
                "reference":     ref_str,
                "notes":         notes_str or "Added via --resolve-missing CLI",
            }

            try:
                added_lot = add_lot_for_missing_buy(
                    local_db_path, flag, user_input, allow_extra=allow_extra
                )
                session_lots.append(added_lot["lot_id"])
                cpu = added_lot["cost_per_unit"]
                print(f"\n  Saved: {qty} x {flag.code} @ ${price:.4f} "
                      f"(cost/unit ${cpu:.4f}) on {purchase_date.strftime('%d/%m/%Y')}")
                qty_remaining = max(0.0, qty_remaining - qty)
                if allow_extra:
                    print(f"  Note: {qty - min(qty, flag.qty_unmatched):.4f} shares "
                          f"added as open position (surplus beyond unmatched qty).")
            except ValueError as exc:
                print(f"  Error: {exc}")
                continue

        log_resolution(local_db_path, {
            "code":                 flag.code,
            "sell_date":            flag.disposal_date.isoformat(),
            "qty_unmatched_before": flag.qty_unmatched,
            "qty_added":            flag.qty_unmatched - max(0.0, qty_remaining),
            "qty_unmatched_after":  max(0.0, qty_remaining),
            "lot_ids_added":        session_lots,
        })

    if not session_lots:
        print("\n  No lots added — re-running is not needed.")
        return

    print(f"\n{line}")
    print(f"  Re-running pipeline with {len(session_lots)} newly saved lot(s)...")
    print(f"{line}")

    rerun_kwargs = {**pipeline_kwargs}
    result2 = run_trading_pipeline(**rerun_kwargs)

    # ── Print before/after progress table ────────────────────────────────────
    before_map: dict[tuple, float] = {
        (m.code, m.disposal_date): m.qty_unmatched
        for m in result.missing_flags
    }
    after_map:  dict[tuple, float] = {
        (m.code, m.disposal_date): m.qty_unmatched
        for m in result2.missing_flags
    }

    print(f"\n{'─'*95}")
    print(f"  {'Code':<8} {'Sell Date':<14} {'Before':>10} {'Added':>10} {'After':>10}  {'Status'}")
    print(f"{'─'*95}")
    for (code, sell_date), before in sorted(before_map.items()):
        after   = after_map.get((code, sell_date), 0.0)
        added   = before - after
        if after == 0.0:
            status = "Resolved"
        elif after < before:
            status = "Partially Resolved"
        else:
            status = "Unresolved"
        print(f"  {code:<8} {sell_date.strftime('%d/%m/%Y'):<14} "
              f"{before:>10.4f} {added:>10.4f} {after:>10.4f}  {status}")
    print(f"{'─'*95}\n")


if __name__ == "__main__":
    _cli()
