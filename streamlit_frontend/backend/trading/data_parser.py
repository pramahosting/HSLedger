# backend/data_parser.py
import pandas as pd
import streamlit as st
import re

COLUMN_ALIASES = {
    "Date": ["date", "timestamp", "trade_date", "settlement_date", "trade date"],
    "Symbol": ["symbol", "pair", "instrument"],
    "Side": ["side", "type", "action", "buy/sell"],
    "Quantity": ["quantity", "qty", "amount", "volume"],
    "Price": ["price", "rate", "unit_price"],
    "Proceeds": ["proceeds", "total", "value", "received"],
    "Cost": ["cost", "cost_basis", "spent"],
    "Fee": ["fee", "commission", "charges"]
}

# ============================================================
# 🧹 Helper: Clean numeric values from messy financial text
# ============================================================
def clean_numeric_value(x):
    """
    Cleans and converts a string with $, commas, parentheses, or negatives
    into a proper float. Handles forms like:
      - "$123.45"
      - "(123.45)"
      - "$(123.45)"
      - "1,234.56"
      - "-123.45"
    """
    if pd.isna(x):
        return 0.0

    if isinstance(x, (int, float)):
        return round(float(x), 2)

    s = str(x).strip()

    # Detect and handle negatives wrapped in parentheses
    is_negative = "(" in s and ")" in s

    # Remove everything except numbers, minus, and decimal
    s = re.sub(r"[^0-9.\-]", "", s)

    # If parentheses were used, make it negative
    if is_negative and not s.startswith("-"):
        s = "-" + s

    try:
        return round(float(s), 2)
    except ValueError:
        return 0.0


def rename_duplicate_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename duplicate columns by appending .1, .2, etc."""
    cols = df.columns.tolist()
    seen = {}
    new_cols = []
    for col in cols:
        if col in seen:
            seen[col] += 1
            new_cols.append(f"{col}.{seen[col]}")
        else:
            seen[col] = 0
            new_cols.append(col)
    df.columns = new_cols
    return df


# ============================================================
# 📄 Main Parser
# ============================================================
def parse_trading_file(file_or_path):
    """Parse any trading CSV/JSON into standardized columns for downstream processing."""
    df = None

    # 1️⃣ Load file safely
    try:
        if hasattr(file_or_path, "read"):
            df = pd.read_csv(file_or_path)
        else:
            file_str = str(file_or_path).lower()
            if file_str.endswith(".csv"):
                df = pd.read_csv(file_or_path)
            elif file_str.endswith(".json"):
                df = pd.read_json(file_or_path)
            else:
                st.warning("Unknown file format — trying to read as CSV fallback.")
                df = pd.read_csv(file_or_path, on_bad_lines="skip")

        df = rename_duplicate_columns(df)

    except Exception as e:
        st.error(f"Failed to read file: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        st.warning("Uploaded file is empty or could not be parsed.")
        return pd.DataFrame()

    # 2️⃣ Replace spaces with underscores
    df.columns = [str(c).strip().replace(" ", "_") for c in df.columns]

    # 3️⃣ Initialize output DataFrame with all standard columns
    output = pd.DataFrame()

    for standard_col, possible_cols in COLUMN_ALIASES.items():
        # Try to find a match in current df
        match_col = next(
            (c for c in df.columns if any(k.lower() in c.lower() for k in possible_cols)),
            None
        )

        if match_col and match_col in df.columns:
            # Numeric columns
            if standard_col in ["Quantity", "Price", "Proceeds", "Cost", "Fee"]:
                output[standard_col] = df[match_col].apply(clean_numeric_value)
            else:
                output[standard_col] = df[match_col].astype(str)
        else:
            # If not found, create a safe default column
            default_value = 0.0 if standard_col in ["Quantity", "Price", "Proceeds", "Cost", "Fee"] else ""
            output[standard_col] = default_value

    # 4️⃣ Copy over any additional columns not in standard schema
    for col in df.columns:
        if col not in output.columns:
            output[col] = df[col]

    # 5️⃣ Globally clean numeric-looking columns (extra safety)
    numeric_like_cols = [
        c for c in output.columns
        if any(k in c.lower() for k in ["amount", "value", "price", "proceed", "cost", "fee", "total", "balance"])
    ]
    for c in numeric_like_cols:
        output[c] = output[c].apply(clean_numeric_value)

    # 6️⃣ Type normalization
    output["Date"] = pd.to_datetime(output["Date"], errors="coerce")
    output["Side"] = output["Side"].astype(str).str.upper().replace({"B": "BUY", "S": "SELL"})

    # 7️⃣ Reset index
    output = output.reset_index(drop=True)

    return output
