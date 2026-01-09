import pandas as pd
import itertools
import streamlit as st
from .gst_calculator import calculate_gst  # Import GST function

# --- Transaction classifier ---
def classify_transactions(df: pd.DataFrame, show_progress=True) -> pd.DataFrame:
    df.columns = df.columns.str.strip().str.lower()
    df = df.reset_index(drop=True)

    # Ensure numeric with zero fallback
    df["debit"] = pd.to_numeric(df.get("debit", 0), errors="coerce").fillna(0)
    df["credit"] = pd.to_numeric(df.get("credit", 0), errors="coerce").fillna(0)

    # Description-based adjustment
    def desc_based_adjust(row):
        desc = str(row.get("description", "")).lower()
        amount = row.get("debit") - row.get("credit")
        if amount != 0:
            return row
        if any(keyword in desc for keyword in ["transfer to", "payment", "card", "bill", "invoice"]):
            row["debit"] = abs(amount) if amount <= 0 else row["debit"]
            row["credit"] = 0
        elif any(keyword in desc for keyword in ["direct credit", "salary", "refund", "fast transfer from"]):
            row["credit"] = abs(amount) if amount >= 0 else row["credit"]
            row["debit"] = 0
        return row

    df = df.apply(desc_based_adjust, axis=1)
    df["tmp_idx"] = df.index

    # Prepare debit and credit DataFrames for internal transfer matching
    debit_df = df[df["debit"] != 0][["tmp_idx", "date", "debit", "bank", "account"]].copy()
    credit_df = df[df["credit"] != 0][["tmp_idx", "date", "credit", "bank", "account"]].copy()

    debit_df = debit_df.rename(columns={
        "tmp_idx": "tmp_idx_debit", "date": "date_debit", "debit": "amount",
        "bank": "bank_debit", "account": "account_debit"
    })
    credit_df = credit_df.rename(columns={
        "tmp_idx": "tmp_idx_credit", "date": "date_credit", "credit": "amount",
        "bank": "bank_credit", "account": "account_credit"
    })

    merged = pd.merge(
        debit_df, credit_df,
        on="amount", how="outer", indicator=True
    )

    # Initialize classification columns
    df["classification"] = None
    df["pairid"] = None
    df["GL Account"] = None
    df["GST"] = 0.0
    df["GST Category"] = None
    df["Who"] = None

    pair_id_counter = itertools.count(1)
    matched_debits, matched_credits = set(), set()

    progress_bar = st.progress(0, text="Matching internal transfers...") if show_progress else None
    total = len(merged)

    for i, row in enumerate(merged.itertuples(index=False), 1):
        d_idx = getattr(row, "tmp_idx_debit", None)
        c_idx = getattr(row, "tmp_idx_credit", None)
        if d_idx in matched_debits or c_idx in matched_credits:
            continue
        if pd.isna(d_idx) or pd.isna(c_idx):
            continue
        account_debit = getattr(row, "account_debit", None)
        account_credit = getattr(row, "account_credit", None)
        if account_debit == account_credit:
            continue  # skip internal transfer within same account

        pid = f"PAIR{next(pair_id_counter):05d}"
        df.loc[int(d_idx), ["classification", "pairid"]] = ["🟢Internal", pid]
        df.loc[int(c_idx), ["classification", "pairid"]] = ["🟢Internal", pid]
        matched_debits.add(int(d_idx))
        matched_credits.add(int(c_idx))

        if progress_bar and i % 50 == 0:
            progress_bar.progress(min(i / total, 1.0), text="Matching internal transfers...")

    if progress_bar:
        progress_bar.progress(1.0, text="Matching complete ✅")

    # External classification
    mask_unclassified = df["classification"].isna()
    df.loc[mask_unclassified & (df["debit"] > 0), "classification"] = "🟡Outgoing"
    df.loc[mask_unclassified & (df["credit"] > 0), "classification"] = "🔵Incoming"
    df["classification"] = df["classification"].fillna("⚪Unclassified")

    # Pull month/year from date without changing type
    if "date" in df.columns:
        df["Month"] = df["date"].apply(lambda x: x.month if hasattr(x, "month") else None)
        df["Year"] = df["date"].apply(lambda x: x.year if hasattr(x, "year") else None)

    # --- Call external GST calculator ---
    df = calculate_gst(df)

    return df
