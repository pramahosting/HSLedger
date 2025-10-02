import pandas as pd
from datetime import datetime
from backend.utils.date_utils import parsedate
from backend.utils.logger import logger


def _find_column(df, keywords):
    cols = {c.lower(): c for c in df.columns}
    for k in keywords:
        for c_lower, c_orig in cols.items():
            if k in c_lower:
                return c_orig
    return None


def normalize_transactions(df: pd.DataFrame, bank_name: str, account_number: str) -> pd.DataFrame:
    """
    Normalize various CSV formats into canonical DataFrame columns:
    - transaction_id, date (datetime), debit, credit, description, bank_name, account_number
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=[
            "transaction_id", "date", "debit", "credit", "description",
            "bank_name", "account_number"
        ])

    df_local = df.copy()

    # Heuristics to find columns
    date_col = _find_column(df_local, ["value_date", "date", "txn_date", "booking_date"])
    desc_col = _find_column(df_local, ["description", "narrative", "memo", "details"])
    id_col = _find_column(df_local, ["id", "ref", "transaction_id", "txn_id", "reference"])
    debit_col = _find_column(df_local, ["debit", "withdrawal", "outflow"])
    credit_col = _find_column(df_local, ["credit", "deposit", "inflow"])

    if date_col is None:
        date_col = df_local.columns[0]

    # Safe parse date
    def safe_parse_date(x):
        try:
            d = parsedate(x)  # use your custom parser
            return pd.to_datetime(d)
        except Exception:
            return pd.to_datetime(x, errors="coerce", dayfirst=True)

    canonical = pd.DataFrame()
    canonical["transaction_id"] = (
        df_local[id_col] if id_col and id_col in df_local.columns else df_local.index.astype(str)
    )
    canonical["date"] = df_local[date_col].apply(safe_parse_date)

    # Debit / Credit (default to 0.0 if missing)
    canonical["debit"] = (
        df_local[debit_col] if debit_col and debit_col in df_local.columns else 0.0
    )
    canonical["credit"] = (
        df_local[credit_col] if credit_col and credit_col in df_local.columns else 0.0
    )

    # Description
    canonical["description"] = (
        df_local[desc_col] if desc_col and desc_col in df_local.columns else ""
    )

    # Bank and account_number → always from frontend
    canonical["bank_name"] = bank_name
    canonical["account_number"] = account_number

    return canonical
