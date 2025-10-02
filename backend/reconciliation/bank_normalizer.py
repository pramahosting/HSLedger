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
# Heuristic column finder
# ------------------------
def _find_column(df, keywords):
    cols = {c.lower(): c for c in df.columns}
    for k in keywords:
        for c_lower, c_orig in cols.items():
            if k in c_lower:
                return c_orig
    return None


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

    preset = BANK_PRESETS.get(bank_name.strip().title()) or BANK_PRESETS.get(bank_name.strip().upper())

    if preset:
        logger.info(f"Using preset mapping for {bank_name}")
        date_col = preset.get("date")
        desc_col = preset.get("description")
        debit_col = preset.get("debit")
        credit_col = preset.get("credit")
        amount_col = preset.get("amount")
        balance_col = preset.get("balance")
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
        df_out["debit"] = df_local[amount_col].apply(lambda x: abs(x) if float(x) < 0 else 0)
        df_out["credit"] = df_local[amount_col].apply(lambda x: float(x) if float(x) > 0 else 0)
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
