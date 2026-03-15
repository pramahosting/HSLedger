# backend/trading/tax_calculator.py
import pandas as pd


def calculate_tax(per_symbol_df: pd.DataFrame, discount_rate: float = 0.5, tax_rate: float = 0.47):
    """
    Given per_symbol_df (realized lots), add:
      - Discounted Gain (50% if Long Term True)
      - Tax Payable (Discounted Gain * tax_rate)
    Returns tax_df (copy) with these columns and a totals row appended.
    """
    if per_symbol_df is None or per_symbol_df.empty:
        return pd.DataFrame()

    df = per_symbol_df.copy()

    # Ensure required columns exist
    if "Realized Gain" not in df.columns:
        df["Realized Gain"] = 0.0
    else:
        df["Realized Gain"] = pd.to_numeric(df["Realized Gain"], errors="coerce").fillna(0.0)

    if "Long Term" not in df.columns:
        df["Long Term"] = False

    # Compute Discounted Gain
    df["Discounted Gain"] = df.apply(
        lambda r: round(r["Realized Gain"] * discount_rate, 2) if r["Long Term"] else round(r["Realized Gain"], 2),
        axis=1
    )
    df["Tax Payable"] = (df["Discounted Gain"] * tax_rate).round(2)

    # Append totals row
    totals = {
        "Symbol": "TOTAL",
        "Sell Date": "",
        "Buy Date": "",
        "Quantity": "",
        "Proceeds": df["Proceeds"].sum() if "Proceeds" in df.columns else 0.0,
        "Cost": df["Cost"].sum() if "Cost" in df.columns else 0.0,
        "Realized Gain": df["Realized Gain"].sum(),
        "Holding Days": "",
        "Long Term": "",
        "Discounted Gain": df["Discounted Gain"].sum(),
        "Tax Payable": df["Tax Payable"].sum()
    }

    df_out = pd.concat([df, pd.DataFrame([totals])], ignore_index=True)

    # Reorder columns for clarity if they exist
    col_order = ["Symbol", "Sell Date", "Buy Date", "Quantity", "Proceeds", "Cost", "Realized Gain",
                 "Holding Days", "Long Term", "Discounted Gain", "Tax Payable"]
    df_out = df_out[[c for c in col_order if c in df_out.columns]]

    return df_out
