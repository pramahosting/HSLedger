
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd


def safe_float(x):
    try:
        if pd.isna(x):
            return np.nan
        return float(x)
    except Exception:
        return np.nan


def as_au_date(x):
    if pd.isna(x):
        return None
    for dayfirst in (True, False):
        try:
            return pd.to_datetime(x, dayfirst=dayfirst, errors="raise")
        except Exception:
            pass
    return None


def pick_col(df: pd.DataFrame, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    return None


ALIASES = {
    "trade_date": ["Trade Date", "trade_date", "Date", "TradeDate"],
    "settlement_date": ["Settlement Date", "settlement_date", "SettlementDate"],
    "contract_note": ["Contract Note No.", "contract_note", "Transaction Ref", "Reference Number"],
    "exchange": ["Exchange", "exchange", "Market"],
    "symbol": ["Stock Code", "symbol", "ticker", "asset", "Ticker", "Code", "Security Code", "ASX Code"],
    "stock_name": ["Stock Name", "stock_name", "security_name", "Company", "Name", "Security Name"],
    "transaction": ["Transaction", "transaction", "tx_type", "Order Type", "Transaction Type"],
    "quantity": ["Quantity", "quantity", "qty", "Units"],
    "price": ["Price ($)", "price", "price_aud", "Price (AUD)", "Unit Price ($)"],
    "brokerage": ["Brokerage ($)", "brokerage", "brokerage_aud", "Brokerage (AUD)", "Brokerage (inc GST) ($)"],
    "gst": ["GST ($)", "gst", "gst_aud", "GST (AUD)", "GST on Brokerage ($)"],
    "contract_value": ["Contract Value ($)", "contract_value", "gross_value_aud", "Trade Value (AUD)", "Total Value ($)", "Consideration ($)"],
    "net_proceeds": ["Net Proceeds ($)", "net_proceeds", "net_cash_aud", "Total (AUD)", "Net Amount ($)", "Net Consideration ($)"],
    "description": ["Notes", "Description", "description", "raw_note"],
    "event_type": ["event_type"],
}


@dataclass
class Lot:
    acquired_at: pd.Timestamp
    qty: float
    cost_aud: float
    source_event: str


def fifo_consume(lots: List[Lot], qty_to_sell: float):
    remaining = qty_to_sell
    cost_base = 0.0
    allocs = []

    while remaining > 1e-12 and lots:
        lot = lots[0]
        take = min(lot.qty, remaining)
        taken_cost = lot.cost_aud * (take / lot.qty) if lot.qty > 0 else 0.0
        allocs.append(
            {
                "lot_acquired_at": lot.acquired_at,
                "lot_qty_taken": take,
                "lot_cost_taken_aud": taken_cost,
                "source_event": lot.source_event,
            }
        )
        cost_base += taken_cost
        lot.qty -= take
        lot.cost_aud -= taken_cost
        remaining -= take
        if lot.qty <= 1e-12:
            lots.pop(0)

    return cost_base, allocs, remaining


def fy_end_year(dt: pd.Timestamp) -> int:
    return int(dt.year + 1) if dt.month >= 7 else int(dt.year)


def normalise_commsec_ledger(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    resolved = {k: pick_col(out, v) for k, v in ALIASES.items()}
    missing = [k for k, v in resolved.items() if k not in {"settlement_date", "contract_note", "exchange", "stock_name", "price"} and v is None]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    renamed = {}
    for logical, src in resolved.items():
        if src is not None:
            renamed[src] = logical
    out = out.rename(columns=renamed)

    out["trade_date"] = out["trade_date"].apply(as_au_date)
    if "settlement_date" in out.columns:
        out["settlement_date"] = out["settlement_date"].apply(as_au_date)
    else:
        out["settlement_date"] = pd.NaT

    for c in ["quantity", "price", "brokerage", "gst", "contract_value", "net_proceeds"]:
        if c in out.columns:
            out[c] = out[c].apply(safe_float)
        else:
            out[c] = np.nan

    text_cols = ["symbol", "stock_name", "transaction", "description", "event_type"]
    for c in text_cols:
        if c in out.columns:
            out[c] = out[c].fillna("").astype(str).str.strip()

    out = out.sort_values(["trade_date", "settlement_date"], kind="mergesort").reset_index(drop=True)
    return out


def compute_stock_tax(
    df: pd.DataFrame,
    *,
    mode: str = "investor",
    carried_capital_losses: float = 0.0,
    carried_revenue_losses: float = 0.0,
):
    mode = (mode or "investor").lower().strip()
    if mode not in {"investor", "trader"}:
        raise ValueError("mode must be 'investor' or 'trader'")

    work = normalise_commsec_ledger(df)

    for c in ["event_type", "symbol", "quantity", "brokerage", "gst", "contract_value", "net_proceeds", "description"]:
        if c not in work.columns:
            raise ValueError(f"Missing normalized column: {c}")

    lots_by_symbol: Dict[str, List[Lot]] = {}
    equity_disposals = []
    income_rows = []
    option_rows = []
    warnings = []

    for i, row in work.iterrows():
        event_type = row["event_type"]
        symbol = row["symbol"]
        trade_date = row["trade_date"]
        qty = safe_float(row["quantity"])
        brokerage = 0.0 if pd.isna(row["brokerage"]) else float(row["brokerage"])
        gst = 0.0 if pd.isna(row["gst"]) else float(row["gst"])
        gross = 0.0 if pd.isna(row["contract_value"]) else float(row["contract_value"])
        net = 0.0 if pd.isna(row["net_proceeds"]) else float(row["net_proceeds"])
        desc = row.get("description", "")

        if trade_date is None or pd.isna(trade_date):
            warnings.append(f"Row {i}: missing trade date; skipped.")
            continue

        fee_total = brokerage + gst

        if event_type in {"CGT_BUY", "TRADE_BUY"}:
            if pd.isna(qty) or qty <= 0:
                warnings.append(f"Row {i}: {event_type} with invalid qty={qty}; skipped.")
                continue
            acquisition_cost = abs(net) if abs(net) > 0 else gross + fee_total
            lots_by_symbol.setdefault(symbol, [])
            lots_by_symbol[symbol].append(
                Lot(
                    acquired_at=trade_date,
                    qty=float(qty),
                    cost_aud=float(acquisition_cost),
                    source_event=event_type,
                )
            )
            continue

        if event_type in {"CGT_SELL", "TRADE_SELL"}:
            sell_qty = abs(float(qty)) if not pd.isna(qty) else np.nan
            if pd.isna(sell_qty) or sell_qty <= 0:
                warnings.append(f"Row {i}: {event_type} with invalid qty={qty}; skipped.")
                continue

            proceeds = abs(float(net)) if abs(net) > 0 else (gross - fee_total)
            lots = lots_by_symbol.setdefault(symbol, [])
            cost_base, allocs, uncovered = fifo_consume(lots, sell_qty)

            if uncovered > 1e-12:
                warnings.append(
                    f"Row {i}: sold {sell_qty:.8f} {symbol}, but only {sell_qty - uncovered:.8f} was available in lots."
                )

            raw_gain = proceeds - cost_base
            is_capital = event_type == "CGT_SELL" or mode == "investor"
            discount_amount = 0.0

            if is_capital:
                for a in allocs:
                    days = (trade_date - a["lot_acquired_at"]).days
                    alloc_proceeds = proceeds * (a["lot_qty_taken"] / sell_qty) if sell_qty > 0 else 0.0
                    alloc_gain = alloc_proceeds - a["lot_cost_taken_aud"]
                    if days > 365 and alloc_gain > 0:
                        discount_amount += alloc_gain * 0.5

            taxable_gain = raw_gain - discount_amount if is_capital else raw_gain
            equity_disposals.append(
                {
                    "row_index": i,
                    "trade_date": trade_date.date().isoformat(),
                    "fy_end_year": fy_end_year(trade_date),
                    "mode": mode,
                    "event_type": event_type,
                    "symbol": symbol,
                    "qty_disposed": round(sell_qty, 8),
                    "proceeds_aud": round(proceeds, 2),
                    "cost_base_aud": round(cost_base, 2),
                    "raw_gain_aud": round(raw_gain, 2),
                    "discount_amount_aud": round(discount_amount, 2),
                    "taxable_gain_aud": round(taxable_gain, 2),
                    "allocation_count": len(allocs),
                    "description": desc,
                }
            )
            continue

        if event_type in {"DIVIDEND", "INTEREST", "LENDING_INCOME"}:
            amount = float(net)
            income_rows.append(
                {
                    "row_index": i,
                    "trade_date": trade_date.date().isoformat(),
                    "fy_end_year": fy_end_year(trade_date),
                    "symbol": symbol,
                    "event_type": event_type,
                    "amount_aud": round(amount, 2),
                    "description": desc,
                }
            )
            if event_type == "DIVIDEND" and "franked" in str(desc).lower():
                warnings.append(
                    f"Row {i}: dividend marked franked, but no franking-credit field exists in the source file. "
                    f"Cash dividend included only; franking credit requires a separate source."
                )
            continue

        if event_type in {"CASH_DEPOSIT", "CASH_WITHDRAWAL"}:
            continue

        if event_type in {"OPTION_BUY", "OPTION_SELL"}:
            option_rows.append(
                {
                    "row_index": i,
                    "trade_date": trade_date.date().isoformat(),
                    "fy_end_year": fy_end_year(trade_date),
                    "symbol": symbol,
                    "event_type": event_type,
                    "quantity": 0.0 if pd.isna(qty) else float(qty),
                    "net_cash_aud": round(net, 2),
                    "description": desc,
                }
            )
            warnings.append(
                f"Row {i}: option transaction detected ({symbol}). Kept in manual-review output, not included in taxable total."
            )
            continue

        warnings.append(f"Row {i}: unsupported event_type={event_type}; skipped.")

    disposals_df = pd.DataFrame(equity_disposals)
    income_df = pd.DataFrame(income_rows)
    options_df = pd.DataFrame(option_rows)

    if disposals_df.empty:
        disposals_df = pd.DataFrame(
            columns=[
                "row_index", "trade_date", "fy_end_year", "mode", "event_type", "symbol",
                "qty_disposed", "proceeds_aud", "cost_base_aud", "raw_gain_aud",
                "discount_amount_aud", "taxable_gain_aud", "allocation_count", "description"
            ]
        )
    if income_df.empty:
        income_df = pd.DataFrame(columns=["row_index", "trade_date", "fy_end_year", "symbol", "event_type", "amount_aud", "description"])
    if options_df.empty:
        options_df = pd.DataFrame(columns=["row_index", "trade_date", "fy_end_year", "symbol", "event_type", "quantity", "net_cash_aud", "description"])

    years = sorted(set(disposals_df["fy_end_year"].dropna().astype(int).tolist()) | set(income_df["fy_end_year"].dropna().astype(int).tolist()))
    fy_rows = []

    cap_loss_carry = float(carried_capital_losses)
    rev_loss_carry = float(carried_revenue_losses)

    for fy in years:
        fy_disp = disposals_df[disposals_df["fy_end_year"] == fy].copy()
        fy_inc = income_df[income_df["fy_end_year"] == fy].copy()

        if mode == "investor":
            current_capital = float(fy_disp["taxable_gain_aud"].sum()) if not fy_disp.empty else 0.0
            positive = float(fy_disp.loc[fy_disp["taxable_gain_aud"] > 0, "taxable_gain_aud"].sum()) if not fy_disp.empty else 0.0
            losses = abs(float(fy_disp.loc[fy_disp["taxable_gain_aud"] < 0, "taxable_gain_aud"].sum())) if not fy_disp.empty else 0.0
            available_losses = cap_loss_carry + losses
            net_capital_gain = max(positive - available_losses, 0.0)
            cap_loss_carry = max(available_losses - positive, 0.0)
            ordinary_income = float(fy_inc["amount_aud"].sum()) if not fy_inc.empty else 0.0
            taxable_amount = ordinary_income + net_capital_gain
            fy_rows.append(
                {
                    "fy_end_year": fy,
                    "mode": mode,
                    "ordinary_income_aud": round(ordinary_income, 2),
                    "gross_capital_result_aud": round(current_capital, 2),
                    "net_capital_gain_aud": round(net_capital_gain, 2),
                    "capital_losses_carried_forward_aud": round(cap_loss_carry, 2),
                    "revenue_losses_carried_forward_aud": round(rev_loss_carry, 2),
                    "taxable_amount_aud": round(taxable_amount, 2),
                }
            )
        else:
            trading_result = float(fy_disp["raw_gain_aud"].sum()) if not fy_disp.empty else 0.0
            ordinary_income = float(fy_inc["amount_aud"].sum()) if not fy_inc.empty else 0.0
            current_revenue = trading_result + ordinary_income
            taxable_amount = max(current_revenue - rev_loss_carry, 0.0)
            rev_loss_carry = max(rev_loss_carry - current_revenue, 0.0) if current_revenue >= 0 else rev_loss_carry + abs(current_revenue)
            fy_rows.append(
                {
                    "fy_end_year": fy,
                    "mode": mode,
                    "ordinary_income_aud": round(ordinary_income, 2),
                    "gross_capital_result_aud": round(trading_result, 2),
                    "net_capital_gain_aud": 0.0,
                    "capital_losses_carried_forward_aud": round(cap_loss_carry, 2),
                    "revenue_losses_carried_forward_aud": round(rev_loss_carry, 2),
                    "taxable_amount_aud": round(taxable_amount, 2),
                }
            )

    fy_summary_df = pd.DataFrame(fy_rows)
    if fy_summary_df.empty:
        fy_summary_df = pd.DataFrame(
            columns=[
                "fy_end_year", "mode", "ordinary_income_aud", "gross_capital_result_aud",
                "net_capital_gain_aud", "capital_losses_carried_forward_aud",
                "revenue_losses_carried_forward_aud", "taxable_amount_aud"
            ]
        )

    summary_lines = []
    summary_lines.append("=== Australian Stock Tax Engine Prototype ===")
    summary_lines.append(f"Mode: {mode}")
    summary_lines.append(f"Rows processed: {len(work)}")
    summary_lines.append(f"Equity disposals processed: {len(disposals_df)}")
    summary_lines.append(f"Income rows processed: {len(income_df)}")
    summary_lines.append(f"Option rows flagged for review: {len(options_df)}")
    summary_lines.append("")
    if not fy_summary_df.empty:
        summary_lines.append("Financial year summary:")
        for _, r in fy_summary_df.iterrows():
            summary_lines.append(
                f"  FY{int(r['fy_end_year'])}: taxable_amount={float(r['taxable_amount_aud']):.2f}, "
                f"ordinary_income={float(r['ordinary_income_aud']):.2f}, "
                f"net_capital_gain={float(r['net_capital_gain_aud']):.2f}, "
                f"capital_losses_cf={float(r['capital_losses_carried_forward_aud']):.2f}"
            )
    else:
        summary_lines.append("No taxable rows found.")
    summary_lines.append("")
    summary_lines.append("Warnings:")
    if warnings:
        for w in warnings[:300]:
            summary_lines.append(f" - {w}")
        if len(warnings) > 300:
            summary_lines.append(f" - ... {len(warnings) - 300} more warnings")
    else:
        summary_lines.append(" - None")

    return {
        "normalised_ledger": work,
        "equity_disposals": disposals_df,
        "income_items": income_df,
        "options_manual_review": options_df,
        "fy_summary": fy_summary_df,
        "summary_text": "\n".join(summary_lines),
        "warnings": warnings,
    }
