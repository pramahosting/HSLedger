# backend/report_presentation.py
import logging
import pandas as pd
from backend.trading.capital_gains import calculate as calculate_cg
from backend.trading.tax_calculator import calculate_tax
import streamlit as st

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def generate_report_df(trades_df: pd.DataFrame):
    """
    Returns (per_symbol_df, totals_df, tax_df)
    Adds Streamlit logging and numeric safety to avoid Series/list errors.
    """
    if trades_df is None or trades_df.empty:
        logging.warning("Input trades_df is empty")
        st.warning("Trades DataFrame is empty!")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    # Step 1: Capital Gains
    #logging.info("Step 1: Calculating capital gains")
    #st.info("✅ Step 1: Calculating capital gains...")
    per_symbol_df, totals_df = calculate_cg(trades_df)

    # Streamlit debug
    #st.info(f"per_symbol_df rows: {len(per_symbol_df)}")
    #st.write("Columns:", per_symbol_df.columns.tolist())
    #st.write("Sample rows (per_symbol_df):", per_symbol_df.head())

    # Ensure numeric columns
    for col in ["Quantity", "Proceeds", "Cost", "Realized Gain"]:
        if col not in per_symbol_df.columns:
            logging.warning(f"Column {col} missing, creating zeros")
            per_symbol_df[col] = 0.0
        else:
            per_symbol_df[col] = pd.to_numeric(per_symbol_df[col], errors="coerce").fillna(0.0)

    # Step 2: Tax Calculation
    #logging.info("Step 2: Calculating tax")
    #st.info("✅ Step 2: Calculating tax...")
    tax_df = calculate_tax(per_symbol_df)

    # Streamlit debug
    #st.info(f"tax_df rows: {len(tax_df)}")
    #st.write("Columns:", tax_df.columns.tolist())
    #st.write("Sample rows (tax_df):", tax_df.head())

    #logging.info("Report generation completed")
    #st.info("✅ Report generation completed")

    return per_symbol_df, totals_df, tax_df
