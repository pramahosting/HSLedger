import pandas as pd
import re
from backend.utils.date_utils import parsedate
from backend.utils.logger import logger

# ------------------------
# Bank Preset Mappings (Major Australian Banks & Credit Unions)
# ------------------------
BANK_PRESETS = {
    # Big 4 Banks
    "CBA": {"date": "Date", "description": "Description", "amount": "Amount", "balance": "Balance"},
    "ANZ": {"date": "Transaction Date", "description": "Transaction Details", "amount": "Amount ($)", "balance": "Balance ($)"},
    "Westpac": {"date": "Date", "description": "Transaction Description", "debit": "Debit", "credit": "Credit", "balance": "Balance"},
    "NAB": {"date": "Date", "description": "Description", "debit": "Debit", "credit": "Credit", "balance": "Balance"},

    # Other banks
    "Macquarie": {"date": "Date", "description": "Transaction Details", "amount": "Amount", "balance": "Balance"},
    "HSBC": {"date": "Date", "description": "Transaction Details", "debit": "Money Out", "credit": "Money In", "balance": "Balance"},
    "BOQ": {"date": "Transaction Date", "description": "Description", "amount": "Transaction Amount", "balance": "Balance"},
    "ING": {"date": "Date", "description": "Transaction Description", "amount": "Amount", "balance": "Balance"},
    "Bendigo": {"date": "Transaction Date", "description": "Particulars", "debit": "Withdrawal", "credit": "Deposit", "balance": "Balance"},
    "Suncorp": {"date": "Date", "description": "Transaction Description", "amount": "Transaction Amount", "balance": "Balance"},
    "AMP": {"date": "Date", "description": "Description", "debit": "Debit", "credit": "Credit", "balance": "Balance"},
    "ME": {"date": "Transaction Date", "description": "Description", "amount": "Amount", "balance": "Balance"},

    # Major Credit Unions
    "Teachers Mutual Bank": {"date": "Date", "description": "Transaction Details", "debit": "Withdrawal", "credit": "Deposit", "balance": "Balance"},
    "CUA": {"date": "Date", "description": "Transaction Details", "debit": "Debit", "credit": "Credit", "balance": "Balance"},
    "Police Bank": {"date": "Date", "description": "Transaction Details", "debit": "Debit", "credit": "Credit", "balance": "Balance"},
    "Defence Bank": {"date": "Date", "description": "Transaction Details", "debit": "Debit", "credit": "Credit", "balance": "Balance"},
    "Heritage Bank": {"date": "Date", "description": "Transaction Details", "debit": "Debit", "credit": "Credit", "balance": "Balance"},
    "Greater Bank": {"date": "Date", "description": "Transaction Details", "debit": "Debit", "credit": "Credit", "balance": "Balance"},
    "Beyond Bank": {"date": "Date", "description": "Transaction Details", "debit": "Debit", "credit": "Credit", "balance": "Balance"},
    "Macarthur Credit Union": {"date": "Date", "description": "Transaction Details", "debit": "Debit", "credit": "Credit", "balance": "Balance"},
    "People's Choice Credit Union": {"date": "Date", "description": "Transaction Details", "debit": "Debit", "credit": "Credit", "balance": "Balance"},
    "Wide Bay Australia": {"date": "Date", "description": "Transaction Details", "debit": "Debit", "credit": "Credit", "balance": "Balance"},
}

# ------------------------
# Helpers
# ------------------------
def _clean_columns(df):
    return {c.strip().lower(): c for c in df.columns}

def _find_column(df, keywords):
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
    if not col_name:
        return None
    cols = _clean_columns(df)
    return cols.get(col_name.strip().lower(), None)

def clean_amount(value):
    if pd.isna(value):
        return 0.0
    val = str(value).strip()
    if val == "" or val.lower() in ["na", "null", "none"]:
        return 0.0
    is_negative = "(" in val and ")" in val
    val = re.sub(r"[^0-9.\-]", "", val)
    try:
        num = float(val)
        return -num if is_negative else num
    except ValueError:
        return 0.0

def detect_debit_credit(df, balance_col=None):
    numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
    if balance_col:
        numeric_cols = [c for c in numeric_cols if c != balance_col]
    debit_col = None
    credit_col = None
    for col in numeric_cols:
        sample = df[col].dropna().head(10)
        if sample.empty:
            continue
        if (sample < 0).any() and (sample > 0).any():
            debit_col = col
            credit_col = col
            break
        elif (sample < 0).any():
            debit_col = col
        elif (sample > 0).any():
            credit_col = col
    return debit_col, credit_col

# ------------------------
# Description-based classification
# ------------------------
DEBIT_KEYWORDS = ["transfer to", "payment", "withdrawal", "purchase", "card", "bpay", "tax"]
CREDIT_KEYWORDS = ["credit", "salary", "fast transfer from", "refund", "transfer from"]

def classify_amount(amount, description=None):
    if amount > 0:
        return 0.0, amount
    elif amount < 0:
        return abs(amount), 0.0
    else:
        if description:
            desc = str(description).lower()
            if any(k in desc for k in DEBIT_KEYWORDS):
                return abs(amount), 0.0
            elif any(k in desc for k in CREDIT_KEYWORDS):
                return 0.0, abs(amount)
        return 0.0, 0.0

# ------------------------
# Normalizer Function
# ------------------------
def normalize_transactions(df: pd.DataFrame, bank_name: str, account_number: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=[
            "Date","Bank","Account","Description","Debit","Credit"
        ])

    df_local = df.copy()
    df_local.columns = [c.strip() for c in df_local.columns]
    logger.info(f"Bank: {bank_name}, Available columns: {df_local.columns.tolist()}")

    preset = BANK_PRESETS.get(bank_name.strip().title()) or BANK_PRESETS.get(bank_name.strip().upper())

    date_col = _match_column_case_insensitive(df_local, preset.get("date") if preset else None)
    desc_col = _match_column_case_insensitive(df_local, preset.get("description") if preset else None)
    debit_col = _match_column_case_insensitive(df_local, preset.get("debit") if preset else None)
    credit_col = _match_column_case_insensitive(df_local, preset.get("credit") if preset else None)
    amount_col = _match_column_case_insensitive(df_local, preset.get("amount") if preset else None)
    balance_col = _match_column_case_insensitive(df_local, preset.get("balance") if preset else None)

    if not date_col:
        date_col = _find_column(df_local, ["date", "txn_date", "value_date"])
    if not desc_col:
        desc_col = _find_column(df_local, ["description", "details", "narrative", "memo"])
    if not debit_col or not credit_col:
        detected_debit, detected_credit = detect_debit_credit(df_local, balance_col)
        debit_col = debit_col or detected_debit
        credit_col = credit_col or detected_credit
        if not debit_col:
            debit_col = _find_column(df_local, ["debit", "withdrawal", "money out"])
        if not credit_col:
            credit_col = _find_column(df_local, ["credit", "deposit", "money in"])
    if not amount_col and not (debit_col and credit_col):
        amount_col = _find_column(df_local, ["amount", "transaction amount", "value"])

    if balance_col:
        if amount_col == balance_col:
            amount_col = None
        if debit_col == balance_col:
            debit_col = None
        if credit_col == balance_col:
            credit_col = None

    df_out = pd.DataFrame()

    if date_col:
        df_local[date_col] = df_local[date_col].astype(str).str.strip().str.replace("\ufeff", "")
        df_out["date"] = pd.to_datetime(
            df_local[date_col],
            dayfirst=True,
            errors="coerce",
            infer_datetime_format=True
        ).dt.strftime("%d/%m/%Y")
    else:
        df_out["date"] = None

    df_out["bank"] = bank_name
    df_out["account"] = account_number
    df_out["description"] = df_local[desc_col] if desc_col else None

    if debit_col and credit_col and debit_col != credit_col:
        df_out["debit"] = df_local[debit_col].apply(lambda x: max(clean_amount(x),0))
        df_out["credit"] = df_local[credit_col].apply(lambda x: max(clean_amount(x),0))
    elif amount_col:
        df_out["debit"], df_out["credit"] = zip(*df_local.apply(
            lambda row: classify_amount(clean_amount(row[amount_col]), row[desc_col] if desc_col else None),
            axis=1
        ))
    elif debit_col and credit_col and debit_col == credit_col:
        df_out["debit"], df_out["credit"] = zip(*df_local.apply(
            lambda row: classify_amount(clean_amount(row[debit_col]), row[desc_col] if desc_col else None),
            axis=1
        ))
    else:
        df_out["debit"], df_out["credit"] = 0,0

    logger.info(f"✅ Normalized {len(df_out)} rows for {bank_name} | Account {account_number}")
    return df_out
