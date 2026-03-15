# frontend/components/render_bank_input.py

import streamlit as st
from datetime import datetime, timedelta
from backend.reconciliation.open_banking_connector import MockOpenBankingConnector
from backend.reconciliation.file_upload_helper import MockUploadedFile
import io


def render_bank_input_section():
    """
    Standalone bank input section that can be integrated with existing render_input_ui.
    Returns True if accounts were added, False otherwise.
    """
    
    st.markdown(
        "<h4 style='margin-top:0rem; margin-bottom:0.5rem; font-size:1.3rem;'>🏦 Connect Bank Account</h4>",
        unsafe_allow_html=True,
    )
    
    # Initialize session state for bank connection
    if 'bank_connector' not in st.session_state:
        st.session_state.bank_connector = None
    if 'bank_accounts_list' not in st.session_state:
        st.session_state.bank_accounts_list = []
    if 'selected_bank_accounts' not in st.session_state:
        st.session_state.selected_bank_accounts = set()
    if 'bank_authenticated' not in st.session_state:
        st.session_state.bank_authenticated = False
    
    accounts_added = False
    
    # Step 1: Bank Selection and Authentication
    with st.form("bank_login_form"):
        st.write("**Step 1: Select Bank & Authenticate**")
        
        col1, col2 = st.columns(2)
        
        with col1:
            bank_name = st.selectbox(
                "Select Bank",
                options=["", "ANZ", "CommBank", "NAB", "Westpac"],
                help="Choose your bank for Open Banking connection"
            )
        
        with col2:
            use_mock = st.checkbox(
                "Use Mock Data (Testing)",
                value=True,
                help="Enable this for testing without real credentials"
            )
        
        # Show credential fields if not using mock
        if not use_mock:
            st.info("ℹ️ You need to register at your bank's developer portal to get these credentials")
            client_id = st.text_input("Client ID", type="password")
            client_secret = st.text_input("Client Secret", type="password")
        else:
            st.info("ℹ️ Mock mode enabled - using sample data for testing")
            client_id = None
            client_secret = None
        
        # Date range for transactions
        st.write("**Transaction Period**")
        date_col1, date_col2 = st.columns(2)
        with date_col1:
            from_date = st.date_input(
                "From Date",
                value=datetime.now() - timedelta(days=90),
                max_value=datetime.now()
            )
        with date_col2:
            to_date = st.date_input(
                "To Date",
                value=datetime.now(),
                max_value=datetime.now()
            )
        
        connect_btn = st.form_submit_button("🔐 Connect to Bank")
        
        if connect_btn:
            if not bank_name:
                st.error("Please select a bank")
            else:
                with st.spinner("Connecting to bank..."):
                    try:
                        # Initialize connector
                        if use_mock:
                            connector = MockOpenBankingConnector(bank_name)
                        else:
                            from backend.reconciliation.open_banking_connector import OpenBankingConnector
                            if not client_id or not client_secret:
                                st.error("Please provide Client ID and Client Secret")
                                st.stop()
                            connector = OpenBankingConnector(bank_name, client_id, client_secret)
                            
                            # Real OAuth flow would happen here
                            st.warning("⚠️ Real OAuth flow not implemented in this demo. Please use Mock mode.")
                            st.stop()
                        
                        st.session_state.bank_connector = connector
                        st.session_state.from_date = datetime.combine(from_date, datetime.min.time())
                        st.session_state.to_date = datetime.combine(to_date, datetime.max.time())
                        
                        # Fetch accounts
                        accounts = connector.get_accounts()
                        st.session_state.bank_accounts_list = accounts
                        st.session_state.bank_authenticated = True
                        st.session_state.selected_bank_accounts = set()
                        
                        st.success(f"✅ Connected to {bank_name}! Found {len(accounts)} accounts.")
                        st.rerun()
                        
                    except Exception as e:
                        st.error(f"Connection failed: {str(e)}")
    
    # Step 2: Account Selection (shown after authentication)
    if st.session_state.bank_authenticated and st.session_state.bank_accounts_list:
        st.markdown("---")
        st.write("**Step 2: Select Accounts to Import**")
        
        # Display accounts with checkboxes
        for idx, account in enumerate(st.session_state.bank_accounts_list):
            col1, col2, col3, col4 = st.columns([0.5, 3, 2, 2])
            
            with col1:
                account_key = f"{account['account_id']}_{account['account_number']}"
                is_selected = st.checkbox(
                    "",
                    value=account_key in st.session_state.selected_bank_accounts,
                    key=f"select_bank_acc_{idx}",
                    label_visibility="collapsed"
                )
                
                if is_selected:
                    st.session_state.selected_bank_accounts.add(account_key)
                else:
                    st.session_state.selected_bank_accounts.discard(account_key)
            
            with col2:
                st.write(f"**{account['display_name']}**")
                st.caption(f"Type: {account['account_type']}")
            
            with col3:
                st.write(f"BSB: {account.get('bsb', 'N/A')}")
                st.write(f"Acc: {account['account_number']}")
            
            with col4:
                balance = account.get('balance', 0)
                if balance:
                    st.write(f"Balance: ${balance:,.2f}")
        
        # Import button
        st.markdown("---")
        import_col1, import_col2, import_col3 = st.columns([2, 2, 3])
        
        with import_col2:
            if st.button(
                "📥 Import Selected Accounts",
                disabled=len(st.session_state.selected_bank_accounts) == 0,
                use_container_width=True
            ):
                accounts_added = import_bank_accounts_to_list()
                if accounts_added:
                    st.rerun()
        
        with import_col3:
            if st.button("🔄 Disconnect", use_container_width=True):
                st.session_state.bank_connector = None
                st.session_state.bank_accounts_list = []
                st.session_state.selected_bank_accounts = set()
                st.session_state.bank_authenticated = False
                st.rerun()
    
    return accounts_added


def import_bank_accounts_to_list():
    """
    Import transactions from selected bank accounts and add to st.session_state.accounts list.
    Returns True if successful, False otherwise.
    """
    
    if not st.session_state.bank_connector or not st.session_state.selected_bank_accounts:
        st.error("No accounts selected")
        return False
    
    connector = st.session_state.bank_connector
    from_date = st.session_state.from_date
    to_date = st.session_state.to_date
    
    with st.spinner("Importing transactions..."):
        imported_count = 0
        
        for account_key in st.session_state.selected_bank_accounts:
            # Find the account details
            account = None
            for acc in st.session_state.bank_accounts_list:
                if f"{acc['account_id']}_{acc['account_number']}" == account_key:
                    account = acc
                    break
            
            if not account:
                continue
            
            try:
                # Fetch transactions
                df = connector.get_transactions(
                    account['account_id'],
                    from_date,
                    to_date
                )
                
                if df.empty:
                    st.warning(f"No transactions found for {account['display_name']}")
                    continue
                
                # Convert DataFrame to CSV bytes
                csv_buffer = io.BytesIO()
                df.to_csv(csv_buffer, index=False)
                csv_buffer.seek(0)
                csv_content = csv_buffer.getvalue()
                
                # Create mock file
                filename = f"{connector.bank_name}_{account['account_number']}_{from_date.strftime('%Y%m%d')}_to_{to_date.strftime('%Y%m%d')}.csv"
                mock_file = MockUploadedFile(filename, csv_content)
                
                # Add to accounts list (same structure as file upload)
                st.session_state.accounts.append({
                    "bank_name": connector.bank_name,
                    "account_number": account['account_number'],
                    "files": [mock_file],
                    "source": "bank_api",
                    "display_name": account['display_name']
                })
                
                imported_count += 1
                
            except Exception as e:
                st.error(f"Failed to import {account['display_name']}: {str(e)}")
        
        if imported_count > 0:
            st.success(f"✅ Successfully imported {imported_count} account(s)!")
            
            # Reset bank connection state
            st.session_state.bank_connector = None
            st.session_state.bank_accounts_list = []
            st.session_state.selected_bank_accounts = set()
            st.session_state.bank_authenticated = False
            
            return True
        else:
            st.error("No accounts were imported")
            return False


def render_input_method_selector():
    """
    Render radio button to select input method: File Upload or Bank Connection.
    Returns the selected method as a string.
    """
    
    st.markdown(
        "<h4 style='margin-top:0rem; margin-bottom:0.5rem; font-size:1.3rem;'>📋 Choose Input Method</h4>",
        unsafe_allow_html=True,
    )
    
    input_method = st.radio(
        "How would you like to add accounts?",
        options=["📁 Upload CSV Files", "🏦 Connect Bank Account"],
        horizontal=True,
        label_visibility="collapsed",
        key="input_method_selector"
    )
    
    return input_method


# Optional: Simplified integration function
def integrate_bank_input_with_file_upload(render_file_upload_func):
    """
    Helper function to integrate bank input with existing file upload UI.
    
    Usage in render_input_ui.py:
        from frontend.components.render_bank_input import integrate_bank_input_with_file_upload
        
        # Inside your column where you want the input form:
        integrate_bank_input_with_file_upload(your_existing_file_upload_function)
    
    Args:
        render_file_upload_func: Your existing file upload rendering function
    """
    
    # Show method selector
    input_method = render_input_method_selector()
    
    st.markdown("<hr style='margin-top:10px; margin-bottom:10px;'>", unsafe_allow_html=True)
    
    # Show appropriate form based on selection
    if input_method == "📁 Upload CSV Files":
        render_file_upload_func()
    else:  # Bank Connection
        render_bank_input_section()