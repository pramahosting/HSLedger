import streamlit as st
import pandas as pd
from backend.trading.data_parser import parse_trading_file
from backend.trading.report_presentation import generate_report_df
from backend.trading.trading_exporter import export_report_trading

# Increase max elements for large DataFrames
pd.set_option("styler.render.max_elements", 5000000)

def render():
    # --- Initialize session state ---
    if "trades_data" not in st.session_state:
        st.session_state.trades_data = None
    if "tax_df" not in st.session_state:
        st.session_state.tax_df = None
    if "per_symbol_df" not in st.session_state:
        st.session_state.per_symbol_df = None
    if "page_number_trades" not in st.session_state:
        st.session_state.page_number_trades = 1
    if "page_size" not in st.session_state:
        st.session_state.page_size = 50
    if "file_uploader_key" not in st.session_state:
        st.session_state.file_uploader_key = 0

    # --- Remove top padding ---
    st.markdown("""
        <style>
            .block-container { padding-top: 2rem; }
        </style>
    """, unsafe_allow_html=True)

    st.markdown("<h3 style='margin-top:0rem;margin-bottom:0rem'>📊 Trading Analysis</h3>", unsafe_allow_html=True)

    # --- File uploader ---
    uploaded = st.file_uploader(
        "Upload trading CSV/JSON",
        type=["csv", "json"],
        key=f"trading_file_uploader_{st.session_state.file_uploader_key}"
    )

    # --- Analyze / Reset buttons ---
    btn_col1, btn_col2, _, btn_col4 = st.columns([3, 2, 2, 1])
    with btn_col2:
        analyze_btn = st.button("📈 Analyze Trading", disabled=not uploaded)

        # --- Message below Analyze button ---
        if uploaded is None:
            st.markdown(
                """
                <div style='text-align: center; width: 100%; font-size: 16px; padding: 10px; 
                            border: 1px solid #d3d3d3; border-radius: 5px; background-color: #f0f8ff;'>
                    📂 Please upload a trading file to begin analysis.
                </div>
                """,
                unsafe_allow_html=True
            )

        if analyze_btn and uploaded:
            trades_df = parse_trading_file(uploaded)
            if trades_df is None or trades_df.empty:
                st.error("❌ No valid trading records found.")
                return

            # --- Generate Report ---
            per_symbol_df, totals_df, tax_df = generate_report_df(trades_df)

            # --- Convert Date columns if they exist ---
            if "Date" in trades_df.columns:
                trades_df["Date"] = pd.to_datetime(trades_df["Date"], errors="coerce")

            if tax_df is not None and "Date" in tax_df.columns:
                tax_df["Date"] = pd.to_datetime(tax_df["Date"], errors="coerce")

            # --- Save in session state consistently ---
            st.session_state.trades_data = trades_df
            st.session_state.tax_df = tax_df
            st.session_state.per_symbol_df = per_symbol_df

    with btn_col4:
        if st.button("🔄 Reset", disabled=st.session_state.trades_data is None):
            keys_to_clear = ["trades_data", "tax_df", "per_symbol_df", "page_number_trades"]
            for key in keys_to_clear:
                if key in st.session_state:
                    del st.session_state[key]
            st.session_state.file_uploader_key += 1
            st.rerun()

    # --- Trading Results Heading ---
    if any([st.session_state.trades_data is not None,
            st.session_state.tax_df is not None]):
        st.subheader("🔎 Trading Results")

    # --- Capital Gains & Tax Table (no pagination) ---
    if st.session_state.tax_df is not None and not st.session_state.tax_df.empty:
        df_total = st.session_state.tax_df.copy()

        # --- Remove any artificial "Grand Total" rows ---
        non_numeric_cols = df_total.select_dtypes(exclude='number').columns
        df_total = df_total[~(df_total[non_numeric_cols] == "Grand Total").all(axis=1)]

        # --- Filter rows: must have Buy or Sell Date ---
        df_total = df_total[df_total["Buy Date"].notnull() | df_total["Sell Date"].notnull()]

        def style_row(r):
            styles = []
            is_last_row = r.name == df_total.index[-1]  # Only absolute last row
            for col in df_total.columns:
                val = r[col]
                if is_last_row:
                    styles.append("font-weight: bold; background-color: #fff3cd")
                else:
                    styles.append("")
            return styles

        with st.expander("💰 Capital Gains / Tax Details", expanded=False):
            st.dataframe(df_total.style.apply(style_row, axis=1), use_container_width=True)

    # --- Classified Trades ---
    if st.session_state.trades_data is not None:
        with st.expander("📋 Classified Trades", expanded=False):
            df_total = st.session_state.trades_data.copy()
            total_rows = len(df_total)
            total_pages = (total_rows // st.session_state.page_size) + (1 if total_rows % st.session_state.page_size else 0)
            start_idx = (st.session_state.page_number_trades - 1) * st.session_state.page_size
            end_idx = start_idx + st.session_state.page_size
            df_page = df_total.iloc[start_idx:end_idx]

            st.dataframe(df_page, use_container_width=True)

            # Pagination
            pag_col1, pag_col2, pag_col3 = st.columns([1,1,1])
            with pag_col1:
                if st.button("⬅ Previous", key="trades_prev") and st.session_state.page_number_trades > 1:
                    st.session_state.page_number_trades -= 1
                    st.rerun()
            with pag_col3:
                if st.button("Next ➡", key="trades_next") and st.session_state.page_number_trades < total_pages:
                    st.session_state.page_number_trades += 1
                    st.rerun()
            with pag_col2:
                st.markdown(f"Page {st.session_state.page_number_trades} of {total_pages}")

    # --- Download Excel Report (without monthly summary) ---
    if st.session_state.trades_data is not None and st.session_state.tax_df is not None:
        excel_bytes = export_report_trading(
            st.session_state.trades_data,
            st.session_state.tax_df,
            None  # No monthly summary
        )
        st.download_button(
            label="📥 Download Full Excel",
            data=excel_bytes,
            file_name="trading_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
