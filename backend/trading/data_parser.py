# backend/data_parser.py
import pandas as pd
import streamlit as st

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


def parse_trading_file(file_or_path):
    """Parse any trading CSV/JSON into standardized columns for downstream processing."""
    df = None

    # 1️⃣ Load file safely
    try:
        if hasattr(file_or_path, "read"):
            df = pd.read_csv(file_or_path)
        else:
            if str(file_or_path).lower().endswith(".csv"):
                df = pd.read_csv(file_or_path)
            else:
                df = pd.read_json(file_or_path)

        df = rename_duplicate_columns(df)

    except Exception as e:
        st.error(f"Failed to read file: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        st.warning("Uploaded file is empty or could not be parsed.")
        return pd.DataFrame()

    # 2️⃣ Replace spaces with underscores
    df.columns = [c.replace(" ", "_") for c in df.columns]

    # 3️⃣ Map standardized columns using aliases
    for standard_col, possible_cols in COLUMN_ALIASES.items():
        if standard_col not in df.columns:
            match_col = next(
                (c for c in df.columns if any(k.lower() in c.lower() for k in possible_cols)),
                None
            )
            if match_col:
                if standard_col in ["Quantity", "Price", "Proceeds", "Cost", "Fee"]:
                    df[standard_col] = pd.to_numeric(df[match_col], errors="coerce").fillna(0.0)
                else:
                    df[standard_col] = df[match_col].astype(str)
            else:
                # Fallback defaults
                df[standard_col] = 0.0 if standard_col in ["Quantity", "Price", "Proceeds", "Cost", "Fee"] else ""

    # 4️⃣ Standardize types
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Side"] = df["Side"].astype(str).str.upper().replace({"B": "BUY", "S": "SELL"})

    # 5️⃣ Reset index
    df = df.reset_index(drop=True)

    return df
