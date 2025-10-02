import streamlit as st 
import pandas as pd
from backend.reconciliation import reconcile_service, exporter
from backend.utils.logger import logger

def render():
    # Increase max elements for large DataFrames
    pd.set_option("styler.render.max_elements", 5000000)

    # Initialize session state
    if "accounts" not in st.session_state:
        st.session_state.accounts = []
    if "reconciliation_results" not in st.session_state:
        st.session_state.reconciliation_results = None
    if "file_uploader_key" not in st.session_state:
        st.session_state.file_uploader_key = 0
    if "page_number" not in st.session_state:
        st.session_state.page_number = 1
    if "page_size" not in st.session_state:
        st.session_state.page_size = 50

    # Remove top padding
    st.markdown("""
        <style>
            .block-container { padding-top: 2rem; }
        </style>
    """, unsafe_allow_html=True)

    st.markdown(
        "<h3 style='margin-top:0rem;margin-bottom:0rem'>🏦Bank Transactions Reconciliation</h3>",
        unsafe_allow_html=True
    )

    # Columns for form & accounts
    col1, _, col2 = st.columns([5, 0.2, 5])

    # --- Add Bank Account Form ---
    with col1:
        st.markdown(
            "<h4 style='margin-top:0rem; margin-bottom:0rem; font-size:1.3rem;'>➕Add Bank Account & Upload Files</h4>",
            unsafe_allow_html=True
        )
        form_key = f"add_account_form_{st.session_state.file_uploader_key}"
        with st.form(key=form_key):
            bank_name = st.text_input("Bank Name", key=f"bank_name_input_{st.session_state.file_uploader_key}")
            account_number = st.text_input("Account Number", key=f"account_number_input_{st.session_state.file_uploader_key}")
            uploaded_files = st.file_uploader(
                "Upload CSV(s) for this account",
                type=["csv"],
                accept_multiple_files=True,
                key=f"uploaded_files_input_{st.session_state.file_uploader_key}"
            )
            submitted = st.form_submit_button("Add Account")
            if submitted:
                if not bank_name or not account_number or not uploaded_files:
                    st.error("Please provide bank name, account number and at least one CSV file.")
                else:
                    st.session_state.accounts.append({
                        "bank_name": bank_name,
                        "account_number": account_number,
                        "files": uploaded_files
                    })
                    st.session_state.file_uploader_key += 1
                    st.rerun()

    # --- Accounts Ready ---
    with col2:
        st.markdown(
            "<h4 style='margin-top:0;margin-bottom:2px;font-size:1.3rem;'>📋Accounts Ready</h4>",
            unsafe_allow_html=True
        )
        if st.session_state.accounts:
            for i, acc in enumerate(st.session_state.accounts, start=1):
                st.write(f"**{i}. {acc['bank_name']} — {acc['account_number']}**")
                st.write("Files: " + ", ".join([f.name for f in acc["files"]]))
        else:
            st.info("No accounts added yet.")

    # --- Run Agent and Reset Buttons on the same row ---
    st.markdown("<hr style='margin-top:5px; margin-bottom:5px;'>", unsafe_allow_html=True)
    btn_col1, btn_col2, btn_col3, btn_col4 = st.columns([3, 2, 2, 1])
    with btn_col2:  # Center column for Run Agent
        if st.button("🚀 Run Agent", disabled=len(st.session_state.accounts) == 0):
            file_entries = []
            for acc in st.session_state.accounts:
                for f in acc["files"]:
                    file_entries.append({
                        "bank_name": acc["bank_name"],
                        "account_number": acc["account_number"],
                        "file": f
                    })

            # Temporary progress message
            status_placeholder = st.empty()
            status_placeholder.info("Matching in progress ⏳")
            result_df = reconcile_service.process_files(file_entries)
            status_placeholder.empty()

            if result_df is None or result_df.empty:
                st.info("No transactions processed or all files invalid.")
                st.session_state.reconciliation_results = None
            else:
                st.session_state.reconciliation_results = result_df
                st.session_state.page_number = 1

    with btn_col4:  # Extreme right column for Reset
        if st.button("🔄 Reset", disabled=st.session_state.reconciliation_results is None):
            keys_to_clear = ["reconciliation_results", "page_number", "page_size"]
            for key in keys_to_clear:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()  # Refresh the page

    # --- Display Results ---
    if st.session_state.reconciliation_results is not None:
        st.subheader("🔎Reconciliation Results")
        df_total = st.session_state.reconciliation_results.copy()

        # --- Monthly Summary ---
        if "Date" in df_total.columns and not df_total["Date"].isna().all():
            df_total["Date"] = pd.to_datetime(df_total["Date"], errors="coerce")
            df_total["Month"] = df_total["Date"].dt.to_period("M")

            monthly_summary = []
            for month, group in df_total.groupby("Month"):
                internal_count = (group["Classification"] == "Internal").sum()
                incoming_count = (group["Classification"] == "External Incoming").sum()
                outgoing_count = (group["Classification"] == "External Outgoing").sum()
                total_income = group.loc[group["Classification"] == "External Incoming", "Credit"].sum()
                total_expense = group.loc[group["Classification"] == "External Outgoing", "Debit"].sum()
                monthly_summary.append([
                    str(month), internal_count, incoming_count, outgoing_count, total_income, total_expense
                ])

            summary_df = pd.DataFrame(
                monthly_summary,
                columns=[
                    "Month",
                    "Internal Transfers",
                    "External Incoming Count",
                    "External Outgoing Count",
                    "Total Incoming Income",
                    "Total Outgoing Expense"
                ]
            )

            totals = pd.DataFrame([[
                "Grand Total",
                summary_df["Internal Transfers"].sum(),
                summary_df["External Incoming Count"].sum(),
                summary_df["External Outgoing Count"].sum(),
                summary_df["Total Incoming Income"].sum(),
                summary_df["Total Outgoing Expense"].sum()
            ]], columns=summary_df.columns)

            summary_df = pd.concat([summary_df, totals], ignore_index=True)

            def highlight_total(row):
                if row["Month"] == "Grand Total":
                    return ["background-color: #fff3cd; font-weight: bold"] * len(row)
                return [""] * len(row)

            with st.expander("📊 Monthly Summary", expanded=False):
                st.dataframe(summary_df.style.apply(highlight_total, axis=1))

        # --- Detailed Table ---
        #st.markdown("##### 📄 Transaction Details")
        key_columns = ["Date", "TransactionID", "Description", "Debit", "Credit", "Bank", "Account", "Classification", "PairID"]
        df_display = df_total[[col for col in key_columns if col in df_total.columns]].copy()

        total_rows = len(df_display)
        total_pages = (total_rows // st.session_state.page_size) + (1 if total_rows % st.session_state.page_size > 0 else 0)
        start_idx = (st.session_state.page_number - 1) * st.session_state.page_size
        end_idx = start_idx + st.session_state.page_size
        df_page = df_display.iloc[start_idx:end_idx]

        def color_row(row):
            cls = row.get("Classification", "")
            if cls == "Internal":
                return ["background-color: #d4f7dc"] * len(row)
            elif cls == "External Incoming":
                return ["background-color: #e8f3ff"] * len(row)
            elif cls == "External Outgoing":
                return ["background-color: #fff3cd"] * len(row)
            return [""] * len(row)

        styled_df = df_page.style.apply(color_row, axis=1)\
                                 .set_table_styles([{'selector': 'th', 'props': [('font-weight', 'bold')]}])

        with st.expander("📄 Transaction Details", expanded=False):
            st.dataframe(styled_df)

            # --- Pagination buttons ---
            pag_col1, pag_col2, pag_col3 = st.columns([1, 1, 1])
            with pag_col1:
                if st.button("⬅ Previous", key="prev_page") and st.session_state.page_number > 1:
                    st.session_state.page_number -= 1
                    st.rerun()
            with pag_col3:
                if st.button("Next ➡", key="next_page") and st.session_state.page_number < total_pages:
                    st.session_state.page_number += 1
                    st.rerun()
            with pag_col2:
                st.markdown(f"Page {st.session_state.page_number} of {total_pages}")

        # --- Download full Excel file ---
        excel_bytes = exporter.export_report(df_total)
        st.download_button(
            label="📥Download Full Excel",
            data=excel_bytes,
            file_name="reconciliation_results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
