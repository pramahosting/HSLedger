# frontend/components/trading_ui.py
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
    if "monthly_summary" not in st.session_state:
        st.session_state.monthly_summary = None
    if "page_number_tax" not in st.session_state:
        st.session_state.page_number_tax = 1
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

        if analyze_btn and uploaded:
            trades_df = parse_trading_file(uploaded)
            if trades_df is None or trades_df.empty:
                st.error("❌ No valid trading records found.")
                return
            st.session_state.trades_data = trades_df

            # --- Generate Report ---
            per_symbol_df, totals_df, tax_df = generate_report_df(trades_df)

            # --- Convert Date columns if they exist ---
            if "Date" in trades_df.columns:
                trades_df["Date"] = pd.to_datetime(trades_df["Date"], errors="coerce")
                trades_df["Month"] = trades_df["Date"].dt.to_period("M")
            if "Date" in tax_df.columns:
                tax_df["Date"] = pd.to_datetime(tax_df["Date"], errors="coerce")
                tax_df["Month"] = tax_df["Date"].dt.to_period("M")

            # --- Monthly Summary ---
            monthly_summary = []
            months = trades_df["Month"].dropna().unique() if "Month" in trades_df.columns else []
            for month in sorted(months):
                month_trades = trades_df[trades_df["Month"] == month] if "Month" in trades_df.columns else pd.DataFrame()
                month_tax = tax_df[tax_df["Month"] == month] if "Month" in tax_df.columns else pd.DataFrame()

                total_trades = len(month_trades)
                total_proceeds = month_trades["Proceeds"].sum() if "Proceeds" in month_trades.columns else 0
                total_cost = month_trades["Cost"].sum() if "Cost" in month_trades.columns else 0
                total_realized_gain = month_tax["Realized Gain"].sum() if not month_tax.empty else 0
                long_term_gain = month_tax.loc[month_tax["Long Term"], "Realized Gain"].sum() if not month_tax.empty else 0
                short_term_gain = month_tax.loc[~month_tax["Long Term"], "Realized Gain"].sum() if not month_tax.empty else 0
                total_tax = month_tax["Tax Payable"].sum() if not month_tax.empty else 0

                monthly_summary.append([
                    str(month),
                    total_trades,
                    total_proceeds,
                    total_cost,
                    total_realized_gain,
                    long_term_gain,
                    short_term_gain,
                    total_tax
                ])

            summary_df = pd.DataFrame(
                monthly_summary,
                columns=[
                    "Month",
                    "Total Trades",
                    "Total Proceeds",
                    "Total Cost",
                    "Total Realized Gain",
                    "Long-Term Gain",
                    "Short-Term Gain",
                    "Tax Payable"
                ]
            )

            totals = pd.DataFrame([[
                "Grand Total",
                summary_df["Total Trades"].sum(),
                summary_df["Total Proceeds"].sum(),
                summary_df["Total Cost"].sum(),
                summary_df["Total Realized Gain"].sum(),
                summary_df["Long-Term Gain"].sum(),
                summary_df["Short-Term Gain"].sum(),
                summary_df["Tax Payable"].sum()
            ]], columns=summary_df.columns)

            summary_df = pd.concat([summary_df, totals], ignore_index=True)

            # --- Save in session state consistently ---
            st.session_state.monthly_summary = summary_df

    with btn_col4:
        if st.button("🔄 Reset", disabled=st.session_state.trades_data is None):
            keys_to_clear = ["trades_data", "monthly_summary", "page_number_tax", "page_number_trades"]
            for key in keys_to_clear:
                if key in st.session_state:
                    del st.session_state[key]
            st.session_state.file_uploader_key += 1
            st.rerun()

    # --- Trading Results Heading ---
    if st.session_state.trades_data is not None or st.session_state.monthly_summary is not None:
        st.subheader("🔎 Trading Results")

    # --- Capital Gains & Tax Summary ---
    if st.session_state.monthly_summary is not None and not st.session_state.monthly_summary.empty:
        with st.expander("💰 Capital Gains and Tax Summary", expanded=False):
            df_total = st.session_state.monthly_summary.copy()
            total_rows = len(df_total)
            total_pages = (total_rows // st.session_state.page_size) + (1 if total_rows % st.session_state.page_size else 0)
            start_idx = (st.session_state.page_number_tax - 1) * st.session_state.page_size
            end_idx = start_idx + st.session_state.page_size
            df_page = df_total.iloc[start_idx:end_idx]

            def style_row(row):
                styles = [""] * len(row)
                if row["Month"] == "Grand Total":
                    styles = ["font-weight: bold; background-color: #fff3cd;"] * len(row)
                else:
                    if "Total Realized Gain" in row:
                        if row["Total Realized Gain"] > 0:
                            styles = ["background-color: #d4f7dc"] * len(row)
                        elif row["Total Realized Gain"] < 0:
                            styles = ["background-color: #f8d7da"] * len(row)
                return styles

            st.dataframe(df_page.style.apply(style_row, axis=1), use_container_width=True)

            # --- Pagination ---
            pag_col1, pag_col2, pag_col3 = st.columns([1,1,1])
            with pag_col1:
                if st.button("⬅ Previous", key="tax_prev") and st.session_state.page_number_tax > 1:
                    st.session_state.page_number_tax -= 1
                    st.rerun()
            with pag_col3:
                if st.button("Next ➡", key="tax_next") and st.session_state.page_number_tax < total_pages:
                    st.session_state.page_number_tax += 1
                    st.rerun()
            with pag_col2:
                st.markdown(f"Page {st.session_state.page_number_tax} of {total_pages}")

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

            # --- Pagination ---
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

    # --- Download Excel Report ---
    if st.session_state.trades_data is not None and st.session_state.monthly_summary is not None:
        excel_bytes = export_report_trading(
            st.session_state.trades_data,
            st.session_state.monthly_summary,
            st.session_state.monthly_summary
        )
        st.download_button(
            label="📥 Download Full Excel",
            data=excel_bytes,
            file_name="trading_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
