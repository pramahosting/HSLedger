import streamlit as st
import pandas as pd

def show_temp_result(result_df: pd.DataFrame, title: str = "🧾 Temporary Raw Result Preview"):
    """
    Display temporary debug output for reconciliation results.

    Args:
        result_df (pd.DataFrame): The dataframe returned by reconciliation service.
        title (str): Optional section title.
    """
    if result_df is None or result_df.empty:
        st.warning("No data returned from reconciliation process.")
        return

    st.markdown(f"### {title}")
    st.write("Columns present:", list(result_df.columns))

    # Show first 50 rows for inspection
    st.dataframe(result_df.head(50))

    # Explicit check for missing 'Date' column
    if "date" not in result_df.columns and "Date" not in result_df.columns:
        st.error("⚠️ 'Date' column missing in reconciliation result! Check bank_normalizer output.")
    else:
        st.success("✅ 'Date' column found.")
