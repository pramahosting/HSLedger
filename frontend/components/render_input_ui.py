# frontend/components/render_input_ui.py

import streamlit as st
from backend.reconciliation.bank_normalizer import BANK_PRESETS
from backend.reconciliation.session_manager import session_manager

def render_input_ui(username, run_agent_callback, load_session, save_current_session):
    """Render the input UI including account form, accounts list, sessions, and action buttons."""
    
    col1, _, col2, _, col3 = st.columns([5, 0.1, 3.5, 0.1, 3])

    # --- Add Bank Account Form ---
    with col1:
        st.markdown(
            "<h4 style='margin-top:0rem; margin-bottom:0rem; font-size:1.3rem;'>➕Add Bank Account & Upload Files</h4>",
            unsafe_allow_html=True,
        )
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
                    st.session_state.accounts.append(
                        {
                            "bank_name": bank_name,
                            "account_number": account_number,
                            "files": uploaded_files,
                        }
                    )
                    st.session_state.file_uploader_key += 1
                    st.rerun()

    # --- Accounts Ready ---
    with col2:
        st.markdown(
            "<h4 style='margin-top:0;margin-bottom:2px;font-size:1.3rem;'>📋Accounts Ready</h4>",
            unsafe_allow_html=True,
        )
        
        # Show loaded accounts from session
        display_accounts = st.session_state.accounts if st.session_state.accounts else st.session_state.accounts_metadata
        
        if display_accounts:
            for i, acc in enumerate(display_accounts, start=1):
                acc_col1, acc_col2 = st.columns([4, 1])
                with acc_col1:
                    st.write(f"**{i}. {acc['bank_name']} — {acc['account_number']}**")
                    
                    # Handle both file objects and string filenames
                    if 'files' in acc and acc['files']:
                        if isinstance(acc['files'][0], str):
                            # It's a list of filenames (from loaded session)
                            st.write("Files: " + ", ".join(acc['files']))
                        else:
                            # It's a list of file objects (from upload)
                            st.write("Files: " + ", ".join([f.name for f in acc["files"]]))
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
            st.info("No accounts added yet.")

    # --- Sessions List ---
    with col3:
        st.markdown(
            "<h4 style='margin-top:0;margin-bottom:2px;font-size:1.3rem;'>📂Sessions</h4>",
            unsafe_allow_html=True,
        )
        
        sessions = session_manager.get_all_sessions(username)
        
        if sessions:
            for session in sessions:
                session_col1, session_col2 = st.columns([4, 1])
                with session_col1:
                    is_current = session["session_id"] == st.session_state.current_session_id
                    label = f"{'🟢 ' if is_current else ''}**{session['display_name']}**"
                    if st.button(label, key=f"load_session_{session['session_id']}", use_container_width=True):
                        if load_session(session["session_id"]):
                            st.success(f"Session loaded: {session['display_name']}")
                            st.session_state.active_tab = "output"  # Switch to output tab after loading session
                            st.rerun()
                        else:
                            st.error("Failed to load session")
                with session_col2:
                    if st.button("🗑️", key=f"delete_session_{session['session_id']}", help="Delete session"):
                        if session_manager.delete_session(username, session["session_id"]):
                            if st.session_state.current_session_id == session["session_id"]:
                                st.session_state.current_session_id = None
                            st.success("Session deleted")
                            st.rerun()
        else:
            st.info("No sessions yet.")

    # --- Run Agent and Reset All ---
    st.markdown("<hr style='margin-top:5px; margin-bottom:5px;'>", unsafe_allow_html=True)
    btn_col1, btn_col2, btn_col3, btn_col4 = st.columns([3, 2, 2, 1])

    with btn_col2:
        if st.button("🚀Run Agent", disabled=len(st.session_state.accounts) == 0):
            run_agent_callback(username)
            st.rerun()

    with btn_col4:
        if st.button("🔄Reset All", disabled=st.session_state.reconciliation_results is None):
            keys_to_clear = [
                "reconciliation_results", "page_number", "accounts", 
                "gst_calculated", "edited_df_cache", "pending_changes", 
                "updated_pages", "current_session_id", "accounts_metadata",
                "loaded_files_data", "selected_rows"
            ]
            for key in keys_to_clear:
                if key in st.session_state:
                    del st.session_state[key]
            
            st.session_state.file_uploader_key += 1
            st.session_state.active_tab = "input"
            st.rerun()