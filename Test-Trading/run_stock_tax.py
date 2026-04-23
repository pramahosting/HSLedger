
import argparse
import sys
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from stock_rules import apply_stock_event_rules
from stock_tax_engine import ALIASES, compute_stock_tax, pick_col, as_au_date

SCRIPT_NAME = Path(__file__).stem
DATA_DIR = SCRIPT_DIR / "data-test"
DEFAULT_RULES_JSON = str((SCRIPT_DIR / "stock_event_rules.json").resolve())

OUT_DIR = SCRIPT_DIR / "output" / SCRIPT_NAME
OUT_DIR.mkdir(parents=True, exist_ok=True)

OUT_NORMALISED = OUT_DIR / "OUT_normalised_ledger.csv"
OUT_EQUITY_DISPOSALS = OUT_DIR / "OUT_equity_disposals.csv"
OUT_INCOME = OUT_DIR / "OUT_income_items.csv"
OUT_OPTIONS = OUT_DIR / "OUT_options_manual_review.csv"
OUT_FY_SUMMARY = OUT_DIR / "OUT_fy_summary.csv"
OUT_VALIDATION = OUT_DIR / "OUT_validation_report.txt"
OUT_SUMMARY = OUT_DIR / "OUT_summary.txt"
OUT_RULES_PREVIEW = OUT_DIR / "OUT_rules_applied_preview.csv"


def _find_default_input() -> Path:
    """Auto-discover a CommSec file in data-test/, falling back to any xlsx/csv."""
    if DATA_DIR.exists():
        candidates = sorted(DATA_DIR.glob("*.xlsx")) + sorted(DATA_DIR.glob("*.xls")) + sorted(DATA_DIR.glob("*.csv"))
        commsec = [f for f in candidates if "commsec" in f.name.lower()]
        if commsec:
            return commsec[0]
        if candidates:
            return candidates[0]
    # Last resort: look in the script directory itself
    for pat in ("*.xlsx", "*.xls", "*.csv"):
        found = sorted(SCRIPT_DIR.glob(pat))
        if found:
            return found[0]
    return DATA_DIR / "CommSec_FY2024-25.xlsx"


def load_input(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"Unsupported input type: {suffix}")


def run_validation(df: pd.DataFrame) -> str:
    lines = ["=== VALIDATION REPORT ===", f"Rows: {len(df)}", f"Columns: {len(df.columns)}", ""]

    resolved = {k: pick_col(df, v) for k, v in ALIASES.items()}
    lines.append("Column alias resolution:")
    for k, v in resolved.items():
        lines.append(f"  {k:18} -> {v}")
    lines.append("")

    required = ["trade_date", "symbol", "transaction", "quantity", "brokerage", "gst",
                "contract_value", "net_proceeds", "description"]
    missing = [k for k in required if resolved.get(k) is None]
    if missing:
        lines.append("Missing required columns:")
        for m in missing:
            lines.append(f"  - {m}")
        return "\n".join(lines)

    date_col = resolved["trade_date"]
    bad_dates = int(df[date_col].apply(as_au_date).isna().sum())
    lines.append(f"Trade-date parse failures: {bad_dates}")

    tx_col = resolved["transaction"]
    qty_col = resolved["quantity"]
    tx_counts = df[tx_col].fillna("NULL").astype(str).value_counts(dropna=False)
    lines.append("")
    lines.append("Transaction counts:")
    for k, v in tx_counts.items():
        lines.append(f"  {k}: {int(v)}")

    buy_mask = df[tx_col].astype(str).str.upper().isin(["B", "OB"])
    sell_mask = df[tx_col].astype(str).str.upper().isin(["S", "OS"])
    txn_mask = df[tx_col].astype(str).str.upper().eq("TXN")

    qty = pd.to_numeric(df[qty_col], errors="coerce")
    lines.append("")
    lines.append(f"Buy rows with non-positive qty: {int((buy_mask & (qty <= 0)).sum())}")
    lines.append(f"Sell rows with non-positive qty: {int((sell_mask & (qty <= 0)).sum())}")
    lines.append(f"TXN rows with missing qty: {int((txn_mask & qty.isna()).sum())}")
    lines.append("")
    lines.append("Validation completed.")
    return "\n".join(lines)


def main():
    default_input = _find_default_input()

    parser = argparse.ArgumentParser(description="Run stock tax engine on a single broker file")
    parser.add_argument("input_file", nargs="?", help=f"Input file (default: auto-detected, currently {default_input})")
    parser.add_argument("--rules", default=DEFAULT_RULES_JSON, help="Path to stock event rules JSON")
    parser.add_argument("--mode", choices=["investor", "trader"], default="investor", help="Tax mode")
    parser.add_argument("--carried-capital-losses", type=float, default=0.0, help="Opening carried capital losses")
    parser.add_argument("--carried-revenue-losses", type=float, default=0.0, help="Opening carried revenue losses")
    args = parser.parse_args()

    input_path = Path(args.input_file) if args.input_file else default_input
    if not input_path.exists():
        raise FileNotFoundError(
            f"Cannot find input file: {input_path.resolve()}\n"
            f"Place the file in {DATA_DIR} or pass an explicit path as the first argument."
        )

    print(f"Input:  {input_path.resolve()}")
    print(f"Output: {OUT_DIR.resolve()}")

    raw_df = load_input(input_path)
    validation_text = run_validation(raw_df)
    OUT_VALIDATION.write_text(validation_text, encoding="utf-8")

    ruled_df, rule_warnings = apply_stock_event_rules(raw_df, args.rules)
    preview_cols = list(raw_df.columns[:5]) + [c for c in ["event_type", "_rule_applied"] if c in ruled_df.columns]
    ruled_df[preview_cols].to_csv(OUT_RULES_PREVIEW, index=False)

    result = compute_stock_tax(
        ruled_df,
        mode=args.mode,
        carried_capital_losses=args.carried_capital_losses,
        carried_revenue_losses=args.carried_revenue_losses,
    )

    result["normalised_ledger"].to_csv(OUT_NORMALISED, index=False)
    result["equity_disposals"].to_csv(OUT_EQUITY_DISPOSALS, index=False)
    result["income_items"].to_csv(OUT_INCOME, index=False)
    result["options_manual_review"].to_csv(OUT_OPTIONS, index=False)
    result["fy_summary"].to_csv(OUT_FY_SUMMARY, index=False)

    summary_text = result["summary_text"]
    if rule_warnings:
        summary_text += "\n\nRule warnings:\n" + "\n".join(f" - {w}" for w in rule_warnings)
    OUT_SUMMARY.write_text(summary_text, encoding="utf-8")

    print("DONE")
    print(f"- Validation:        {OUT_VALIDATION}")
    print(f"- Rules preview:     {OUT_RULES_PREVIEW}")
    print(f"- Normalised ledger: {OUT_NORMALISED}")
    print(f"- Equity disposals:  {OUT_EQUITY_DISPOSALS}")
    print(f"- Income items:      {OUT_INCOME}")
    print(f"- Options review:    {OUT_OPTIONS}")
    print(f"- FY summary:        {OUT_FY_SUMMARY}")
    print(f"- Summary:           {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
