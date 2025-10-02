import pandas as pd
from backend.utils.date_utils import parsedate
from backend.utils.logger import logger


# ------------------------
# Bank Preset Mappings
# ------------------------
BANK_PRESETS = {
    "CBA": {
        "date": "Date",
        "description": "Description",
        "amount": "Amount",
        "balance": "Balance"
    },
    "ANZ": {
        "date": "Transaction Date",
        "description": "Transaction Details",
        "amount": "Amount ($)",
        "balance": "Balance ($)"
    },
    "Westpac": {
        "date": "Date",
        "description": "Transaction Description",
        "debit": "Debit",
        "credit": "Credit",
        "balance": "Balance"
    },
    "NAB": {
        "date": "Date",
        "description": "Description",
        "debit": "Debit",
        "credit": "Credit",
        "balance": "Balance"
    },
    "Macquarie": {
        "date": "Date",
        "description": "Transaction Details",
        "amount": "Amount",
        "balance": "Balance"
    },
    "HSBC": {
        "date": "Date",
        "description": "Transaction Details",
        "debit": "Money Out",
        "credit": "Money In",
        "balance": "Balance"
    },
    "BOQ": {
        "date": "Transaction Date",
        "description": "Description",
        "amount": "Transaction Amount",
        "balance": "Balance"
    },
    "ING": {
        "date": "Date",
        "description": "Transaction Description",
        "amount": "Amount",
        "balance": "Balance"
    },
    "Bendigo": {
        "date": "Transaction Date",
        "description": "Particulars",
        "debit": "Withdrawal",
        "credit": "Deposit",
        "balance": "Balance"
    },
    "Suncorp": {
        "date": "Date",
        "description": "Transaction Description",
        "amount": "Transaction Amount",
        "balance": "Balance"
    },
    "AMP": {
        "date": "Date",
        "description": "Description",
        "debit": "Debit",
        "credit": "Credit",
        "balance": "Balance"
    },
    "ME": {
        "date": "Transaction Date",
        "description": "Description",
        "amount": "Amount",
        "balance": "Balance"
    },
}


# ------------------------
# Helpers
# ------------------------
def _clean_columns(df):
    """Strip whitespace and lowercase all column headers for safe matching."""
    return {c.strip().lower(): c for c in df.columns}


def _find_column(df, keywords):
    """Heuristic finder with case-insensitive + space-tolerant match."""
    cols = _clean_columns(df)
    for k in keywords:
        k = k.strip().lower()
        if k in cols:
            return cols[k]
        for c_lower, c_orig in cols.items():
            if k in c_lower:
                return c_orig
    return None


def _match_column_case_insensitive(df, col_name):
    """Match preset column to actual DataFrame col (case-insensitive + space-tolerant)."""
    if not col_name:
        return None
    cols = _clean_columns(df)
    return cols.get(col_name.strip().lower(), None)


# ------------------------
# Normalizer Function
# ------------------------
def normalize_transactions(df: pd.DataFrame, bank_name: str, account_number: str) -> pd.DataFrame:
    """
    Normalize any bank CSV into canonical schema.
    """

    if df is None or df.empty:
        return pd.DataFrame(columns=[
            "transactionid", "date", "bsb", "accountnumber", "description",
            "debit", "credit", "balance", "type", "reference", "bank", "accounttype"
        ])

    df_local = df.copy()
    df_local.columns = [c.strip() for c in df_local.columns]

    # Log columns for debugging
    logger.info(f"Bank: {bank_name}, Available columns: {df_local.columns.tolist()}")

    preset = BANK_PRESETS.get(bank_name.strip().title()) or BANK_PRESETS.get(bank_name.strip().upper())

    if preset:
        logger.info(f"Using preset mapping for {bank_name}")
        date_col = _match_column_case_insensitive(df_local, preset.get("date"))
        desc_col = _match_column_case_insensitive(df_local, preset.get("description"))
        debit_col = _match_column_case_insensitive(df_local, preset.get("debit"))
        credit_col = _match_column_case_insensitive(df_local, preset.get("credit"))
        amount_col = _match_column_case_insensitive(df_local, preset.get("amount"))
        balance_col = _match_column_case_insensitive(df_local, preset.get("balance"))

        # Fallback if preset not found
        if not date_col:
            logger.warning(f"Preset date column not found. Falling back.")
            date_col = _find_column(df_local, ["date", "txn_date", "value_date"])
        if not desc_col:
            desc_col = _find_column(df_local, ["description", "details", "narrative", "memo"])
        if not debit_col:
            debit_col = _find_column(df_local, ["debit", "withdrawal", "money out"])
        if not credit_col:
            credit_col = _find_column(df_local, ["credit", "deposit", "money in"])
        if not amount_col:
            amount_col = _find_column(df_local, ["amount", "transaction amount", "value"])
        if not balance_col:
            balance_col = _find_column(df_local, ["balance", "running balance"])

    else:
        logger.info(f"Falling back to heuristic mapping for {bank_name}")
        date_col = _find_column(df_local, ["date", "txn_date", "value_date"])
        desc_col = _find_column(df_local, ["description", "details", "narrative", "memo"])
        debit_col = _find_column(df_local, ["debit", "withdrawal", "money out"])
        credit_col = _find_column(df_local, ["credit", "deposit", "money in"])
        amount_col = _find_column(df_local, ["amount", "transaction amount", "value"])
        balance_col = _find_column(df_local, ["balance", "running balance"])

    # --- Build normalized DataFrame ---
    df_out = pd.DataFrame()
    df_out["transactionid"] = df_local.index.astype(str)

    # Dates
    if date_col:
        df_out["date"] = df_local[date_col].apply(lambda x: parsedate(x))
    else:
        df_out["date"] = None

    df_out["bsb"] = None
    df_out["accountnumber"] = account_number
    df_out["description"] = df_local[desc_col] if desc_col else None

    # Debit / Credit / Amount
    if debit_col and credit_col:
        df_out["debit"] = pd.to_numeric(df_local[debit_col], errors="coerce").fillna(0)
        df_out["credit"] = pd.to_numeric(df_local[credit_col], errors="coerce").fillna(0)
    elif amount_col:
        df_out["debit"] = pd.to_numeric(df_local[amount_col], errors="coerce").apply(lambda x: abs(x) if x < 0 else 0)
        df_out["credit"] = pd.to_numeric(df_local[amount_col], errors="coerce").apply(lambda x: x if x > 0 else 0)
    else:
        df_out["debit"], df_out["credit"] = 0, 0

    # Balance
    if balance_col:
        df_out["balance"] = pd.to_numeric(df_local[balance_col], errors="coerce")
    else:
        df_out["balance"] = None

    # Metadata
    df_out["type"] = None
    df_out["reference"] = None
    df_out["bank"] = bank_name
    df_out["accounttype"] = None

    return df_out


