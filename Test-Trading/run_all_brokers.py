
import sys
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from stock_rules import apply_stock_event_rules
from stock_tax_engine import compute_stock_tax

DATA_DIR = SCRIPT_DIR / "data-test"
OUT_BASE = SCRIPT_DIR / "output"
RULES_JSON = str((SCRIPT_DIR / "stock_event_rules.json").resolve())

BROKER_FILES = [
    ("CommSec",    DATA_DIR / "CommSec_FY2024-25.xlsx"),
    ("Stake",      DATA_DIR / "Stake_FY2024-25.xlsx"),
    ("Superhero",  DATA_DIR / "Superhero_FY2024-25.xlsx"),
    ("SelfWealth", DATA_DIR / "SelfWealth_FY2024-25.xlsx"),
    ("Nabtrade",   DATA_DIR / "Nabtrade_FY2024-25.xlsx"),
]


def load_input(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"Unsupported file type: {suffix}")


def run_broker(broker_name: str, input_path: Path):
    out_dir = OUT_BASE / broker_name
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_df = load_input(input_path)
    ruled_df, rule_warnings = apply_stock_event_rules(raw_df, RULES_JSON)

    preview_cols = list(raw_df.columns[:5]) + [c for c in ["event_type", "_rule_applied"] if c in ruled_df.columns]
    ruled_df[preview_cols].to_csv(out_dir / "rules_applied_preview.csv", index=False)

    result = compute_stock_tax(ruled_df)

    result["normalised_ledger"].to_csv(out_dir / "normalised_ledger.csv", index=False)
    result["equity_disposals"].to_csv(out_dir / "equity_disposals.csv", index=False)
    result["income_items"].to_csv(out_dir / "income_items.csv", index=False)
    result["options_manual_review"].to_csv(out_dir / "options_manual_review.csv", index=False)
    result["fy_summary"].to_csv(out_dir / "fy_summary.csv", index=False)

    summary_text = f"=== {broker_name} ===\n{result['summary_text']}"
    if rule_warnings:
        summary_text += "\n\nRule warnings:\n" + "\n".join(f" - {w}" for w in rule_warnings)
    (out_dir / "summary.txt").write_text(summary_text, encoding="utf-8")

    return result, rule_warnings


def main():
    OUT_BASE.mkdir(parents=True, exist_ok=True)
    combined_parts = ["=== Combined Multi-Broker Tax Summary ==="]

    for broker_name, path in BROKER_FILES:
        if not path.exists():
            print(f"SKIP {broker_name}: {path} not found")
            continue
        print(f"Processing {broker_name} ...")
        try:
            result, warnings = run_broker(broker_name, path)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            combined_parts.append(f"\n--- {broker_name}: ERROR ---\n{exc}")
            continue

        disp = len(result["equity_disposals"])
        inc = len(result["income_items"])
        unk = result["warnings"]
        print(f"  disposals={disp}  income={inc}  rule_warnings={len(warnings)}")

        section = f"\n--- {broker_name} ---\n{result['summary_text']}"
        if warnings:
            shown = warnings[:30]
            section += "\n\nRule warnings:\n" + "\n".join(f" - {w}" for w in shown)
            if len(warnings) > 30:
                section += f"\n  ... {len(warnings) - 30} more"
        combined_parts.append(section)

    combined_out = OUT_BASE / "combined_summary.txt"
    combined_out.write_text("\n".join(combined_parts), encoding="utf-8")
    print(f"\nDone. Outputs in: {OUT_BASE}")
    print(f"Combined summary: {combined_out}")


if __name__ == "__main__":
    main()
