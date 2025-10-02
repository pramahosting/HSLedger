# backend/classifier.py

import pandas as pd
import itertools
import streamlit as st


def classify_transactions(df: pd.DataFrame, show_progress=True) -> pd.DataFrame:
    """
    Classify transactions into Internal, External Incoming, and External Outgoing.
    Optimised for large datasets with optional Streamlit progress bar.
    """

    # -------------------------------
    # Step 1: Normalise & prepare
    # -------------------------------
    df.columns = df.columns.str.strip().str.lower()
    df = df.reset_index(drop=True)

    # Ensure numeric
    df["debit"] = pd.to_numeric(df.get("debit", 0), errors="coerce").fillna(0)
    df["credit"] = pd.to_numeric(df.get("credit", 0), errors="coerce").fillna(0)

    # Add tmp index
    df["tmp_idx"] = df.index

    # -------------------------------
    # Step 2: Split debit & credit
    # -------------------------------
    debit_df = df[df["debit"] > 0][["tmp_idx", "date", "debit"]].copy()
    credit_df = df[df["credit"] > 0][["tmp_idx", "date", "credit"]].copy()
    debit_df = debit_df.rename(columns={"tmp_idx": "tmp_idx_debit", "date": "date_debit", "debit": "amount"})
    credit_df = credit_df.rename(columns={"tmp_idx": "tmp_idx_credit", "date": "date_credit", "credit": "amount"})

    # -------------------------------
    # Step 3: Match debits & credits
    # -------------------------------
    merged = pd.merge(debit_df, credit_df, on="amount", how="inner")

    merged["date_debit"] = pd.to_datetime(merged["date_debit"], errors="coerce")
    merged["date_credit"] = pd.to_datetime(merged["date_credit"], errors="coerce")

    merged = merged[(merged["date_credit"] - merged["date_debit"]).dt.days.abs() <= 2]

    # -------------------------------
    # Step 4: Classification
    # -------------------------------
    df["classification"] = None
    df["pairid"] = None

    pair_id_counter = itertools.count(1)
    matched_debits, matched_credits = set(), set()

    # Streamlit progress bar
    progress_bar = None
    if show_progress:
        progress_bar = st.progress(0, text="Matching internal transfers...")
    total = len(merged)

    for i, row in enumerate(merged.itertuples(index=False), 1):
        d_idx, c_idx = int(row.tmp_idx_debit), int(row.tmp_idx_credit)
        if d_idx in matched_debits or c_idx in matched_credits:
            continue
        pid = f"PAIR{next(pair_id_counter):05d}"
        df.loc[d_idx, ["classification", "pairid"]] = ["Internal", pid]
        df.loc[c_idx, ["classification", "pairid"]] = ["Internal", pid]
        matched_debits.add(d_idx)
        matched_credits.add(c_idx)

        # update progress
        if progress_bar and i % 50 == 0:
            progress_bar.progress(min(i / total, 1.0), text="Matching internal transfers...")

    if progress_bar:
        progress_bar.progress(1.0, text="Matching complete ✅")

    # -------------------------------
    # Step 5: External classification
    # -------------------------------
    mask_unclassified = df["classification"].isna()
    df.loc[mask_unclassified & (df["debit"] > 0), "classification"] = "External Outgoing"
    df.loc[mask_unclassified & (df["credit"] > 0), "classification"] = "External Incoming"
    df["classification"] = df["classification"].fillna("Unclassified")

    # -------------------------------
    # Step 6: Final clean + ordering
    # -------------------------------
    required_final = [
        "date", "transaction_id", "description",
        "debit", "credit", "bank_name", "account_number",
        "classification", "pairid"
    ]
    for col in required_final:
        if col not in df.columns:
            df[col] = None
    df = df[required_final]

    rename_map = {
        "date": "Date",
        "transaction_id": "TransactionID",
        "description": "Description",
        "debit": "Debit",
        "credit": "Credit",
        "bank_name": "Bank",
        "account_number": "Account",
        "classification": "Classification",
        "pairid": "PairID"
    }
    df = df.rename(columns=rename_map)

    return df
