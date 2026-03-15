# frontend/components/reconciliation_input_bank.py
import streamlit as st
import io
import pandas as pd
from typing import List, Dict, Any
from backend.reconciliation.bank_normalizer import BANK_PRESETS
from backend.reconciliation.gst_calculator import calculate_gst
from backend.reconciliation.session_manager import session_manager

# --------------------------------------------------------------------
# MOCK BANK CLIENT (for testing only)
# Replace this with a real bank API client in production.
# --------------------------------------------------------------------
class MockBankClient:
    def __init__(self):
        self.logged_in = False
        self.username = None

    def login(self, bank_name: str, customer_number: str, password: str) -> bool:
        # This mock accepts any non-empty credentials
        if bank_name and customer_number and password:
            self.logged_in = True
            self.username = customer_number
            return True
        return False

    def list_accounts(self) -> List[Dict[str, str]]:
        # Return a fake list of accounts for the logged-in user
        # Each account dict has account_number and display_name
        if not self.logged_in:
            return []
        return [
            {"account_number": f"ACC-{self.username}-01", "display_name": "Everyday Account"},
            {"account_number": f"ACC-{self.username}-02", "display_name": "Savings Account"},
        ]

    def download_transactions_csv(self, account_number: str) -> io.BytesIO:
        # Generate a small example CSV for the account
        csv_content = "date,description,debit,credit\n01/10/2025,Test deposit,,1000\n02/10/2025,Test payment,200,\n"
        b = io.BytesIO(csv_content.encode("utf-8"))
        b.name = f"{account_number}.csv"  # mimic uploaded file name attribute
        b.seek(0)
        return b

# --------------------------------------------------------------------
# Input UI: render_bank_input_ui
# - Adds radio option to choose between "Upload from computer" and "Connect bank account"
# - For bank flow: show bank dropdown, customer number, password; login, list accounts; select accounts; pull CSVs into accounts list
# - Files added are consistent with shape used by the main app:
#     { "bank_name": bank_name, "account_number": account_number, "files": [file-like-objects] }
# --------------------------------------------------------------------
def render_bank_input_ui(username: str, process_files_cached, load_session_fn, save_current_session_fn):
    """
    Call signature matches your modular approach.
    - username: current logged-in username
    - process_files_cached: function to call the processor (cached)
    - load_session_fn: loader function (kept for signature parity, not used here)
    - save_current_session_fn: saver for session state
    """

    st.markdown("<h4 style='margin-top:0rem; margin-bottom:0rem; font-size:1.3rem;'>➕Add Bank Account & Upload Files</h4>", unsafe_allow_html=True)

    # Choose input method
    mode = st.radio("Choose input method:", ["Upload from Computer", "Connect Bank Account"], horizontal=True)

    if mode == "Upload from Computer":
        # Existing upload form (keeps same keys to ensure no behavioral change)
        form_key = f"add_account_form_{st.session_state.file_uploader_key}"
        with st.form(key=form_key):
            sorted_banks = [""] + sorted(BANK_PRESETS.keys())
            bank_name = st.selectbox(
                "Bank Name",
                options=sorted_banks,
                index=0,
                key=f"bank_name_input_{st.session_state.file_uploader_key}",
            )
            account_number = st.text_input(
                "Account Number", key=f"account_number_input_{st.session_state.file_uploader_key}"
            )
            uploaded_files = st.file_uploader(
                "Upload CSV(s) for this account",
                type=["csv"],
                accept_multiple_files=True,
                key=f"uploaded_files_input_{st.session_state.file_uploader_key}",
            )
            submitted = st.form_submit_button("Add Account")
            if submitted:
                if not bank_name or not account_number or not uploaded_files:
                    st.error("Please provide bank name, account number and at least one CSV file.")
                else:
                    # Keep same shape as existing application
                    st.session_state.accounts.append(
                        {
                            "bank_name": bank_name,
                            "account_number": account_number,
                            "files": uploaded_files,
                        }
                    )
                    st.session_state.file_uploader_key += 1
                    st.success("Account added (uploaded files).")
                    st.rerun()

    else:
        # Bank connection flow
        st.info("Connect to your bank to pull transaction CSVs. (This is a test mock — replace with real bank client in production.)")
        sorted_banks = [""] + sorted(BANK_PRESETS.keys())
        bank_name = st.selectbox("Bank Name", options=sorted_banks, index=0, key="bank_select_for_connect")
        customer_number = st.text_input("Customer Number / User ID", key="bank_customer_number")
        password = st.text_input("Password", key="bank_password", type="password")

        # instantiate (or reuse) client in session state
        if "mock_bank_client" not in st.session_state:
            st.session_state.mock_bank_client = MockBankClient()

        client: MockBankClient = st.session_state.mock_bank_client

        if st.button("Login to Bank", key="bank_login_btn", disabled=(not bank_name or not customer_number or not password)):
            logged_in = client.login(bank_name, customer_number, password)
            if logged_in:
                st.success("Bank login successful (mock).")
                # For security: do not keep real password in persistent storage
                # We store nothing sensitive on disk in this mock.
            else:
                st.error("Bank login failed (mock). Please check credentials.")

        # if logged in, show accounts
        if client.logged_in:
            accounts = client.list_accounts()
            st.markdown("#### Select account(s) to pull transactions from:")
            selected = []
            for acc in accounts:
                key = f"bank_acc_select_{acc['account_number']}"
                if st.checkbox(f"{acc['display_name']} — {acc['account_number']}", key=key):
                    selected.append(acc)

            if selected:
                if st.button("Pull selected accounts as CSV(s)", key="pull_selected_accounts"):
                    # For each selected account, pull CSV and add to session_state.accounts
                    added_count = 0
                    for acc in selected:
                        f = client.download_transactions_csv(acc['account_number'])
                        # `f` is a file-like BytesIO object with .name attribute
                        st.session_state.accounts.append({
                            "bank_name": bank_name,
                            "account_number": acc['account_number'],
                            "files": [f],
                        })
                        added_count += 1

                    if added_count:
                        st.session_state.file_uploader_key += 1
                        st.success(f"Pulled transactions for {added_count} account(s) — added to Accounts Ready.")
                        # Optionally rerun so Accounts Ready shows immediately in the main UI
                        st.rerun()
            else:
                st.info("No accounts selected yet.")

    # At the bottom show the current Accounts Ready (same as original UI does elsewhere)
    st.markdown("### Accounts Ready (From this session)")
    display_accounts = st.session_state.accounts if st.session_state.accounts else st.session_state.accounts_metadata
    if display_accounts:
        for i, acc in enumerate(display_accounts, start=1):
            acc_col1, acc_col2 = st.columns([4, 1])
            with acc_col1:
                st.write(f"**{i}. {acc['bank_name']} — {acc['account_number']}**")
                if 'files' in acc and acc['files']:
                    if isinstance(acc['files'][0], str):
                        st.write("Files: " + ", ".join(acc['files']))
                    else:
                        st.write("Files: " + ", ".join([getattr(f, "name", f"file_{idx}") for idx, f in enumerate(acc["files"], start=1)]))
                else:
                    st.write("Files: None")
            with acc_col2:
                if st.button("🗑️", key=f"remove_account_{i}", help="Remove this account"):
                    if st.session_state.accounts:
                        st.session_state.accounts.pop(i-1)
                    elif st.session_state.accounts_metadata:
                        st.session_state.accounts_metadata.pop(i-1)
                    st.rerun()
    else:
        st.info("No accounts added yet. Use the upload form or connect a bank account.")

    # Security note (display, not functional)
    st.caption("⚠️ This bank connector is a mock for testing. DO NOT enter real credentials here in production. Implement secure token/OAuth flows for real bank integrations.")
