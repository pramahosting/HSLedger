# backend/reconciliation/reconcile_service.py

from typing import List, Dict
import pandas as pd
from backend.reconciliation.bank_normalizer import normalize_transactions
from backend.reconciliation import classifier
from backend.utils.file_utils import load_csv, validate_file
from backend.utils.logger import logger
import streamlit as st

def process_files(file_entries: List[Dict], show_progress=True) -> pd.DataFrame:
    """
    Load, normalize, and classify transactions from multiple uploaded files.
    Returns a single concatenated and classified DataFrame with GST toggle support.
    """
    normalized_list = []

    for entry in file_entries:
        bank = entry.get("bank_name") or "Unknown Bank"
        account = entry.get("account_number") or "Unknown Account"
        file_obj = entry.get("file")
        try:
            df = load_csv(file_obj)
        except Exception as e:
            logger.error("Skipping file for %s %s: %s", bank, account, e)
            continue

        if not validate_file(df):
            logger.warning("File validation failed for %s %s - skipping", bank, account)
            continue

        normalized = normalize_transactions(df, bank, account)
        normalized_list.append(normalized)

    if not normalized_list:
        return pd.DataFrame()

    # Combine all normalized files
    combined = pd.concat(normalized_list, ignore_index=True)

    # Ensure column names lowercase for classifier
    combined.columns = combined.columns.str.strip().str.lower()

    # Classify transactions with internal/external + GST toggle
    classified = classifier.classify_transactions(combined, show_progress=show_progress)

    return classified

