# frontend/components/reconciliation_ui.py

import streamlit as st
import pandas as pd
from backend.reconciliation import reconcile_service
from backend.utils.logger import logger
from backend.reconciliation.session_manager import session_manager
from backend.reconciliation.gst_calculator import calculate_gst
from frontend.components.render_input_ui import render_input_ui
from frontend.components.render_output_ui import render_output_ui


@st.cache_data(show_spinner=False)
def process_files_cached(file_entries):
    return reconcile_service.process_files(file_entries)


def save_current_session():
    if st.session_state.get("current_session_id") and st.session_state.get("logged_in"):
        username = st.session_state.user.get("username", "default_user")
        if st.session_state.get("reconciliation_results") is not None:
            session_manager.save_pending_changes_only(
                username,
                st.session_state.current_session_id,
                st.session_state.get("pending_changes", {}),
                st.session_state.get("updated_pages", set()),
                st.session_state.get("page_number", 1)
            )


def load_session(session_id: str):
    username = st.session_state.user.get("username", "default_user")
    session_data = session_manager.load_session_data(username, session_id)
    
    if session_data:
        st.session_state.accounts_metadata = session_data.get("accounts", [])
        st.session_state.accounts = []
        st.session_state.loaded_files_data = session_data.get("files_data", {})
        
        if session_data.get("results") is not None:
            st.session_state.reconciliation_results = session_data["results"]
            st.session_state.edited_df_cache = session_data["results"].copy()
            st.session_state.gst_calculated = True
        
        st.session_state.pending_changes = session_data.get("pending_changes", {})
        st.session_state.updated_pages = session_data.get("updated_pages", set())
        st.session_state.page_number = session_data.get("page_number", 1)
        st.session_state.current_session_id = session_id
        st.session_state.selected_rows = set()
        
        return True
    return False


def run_agent_callback(username):
    session_id = session_manager.create_session(username)
    st.session_state.current_session_id = session_id
    
    file_entries = []
    uploaded_files_data = {}
    
    for acc in st.session_state.accounts:
        for f in acc["files"]:
            file_entries.append({
                "bank_name": acc["bank_name"], 
                "account_number": acc["account_number"], 
                "file": f
            })
            f.seek(0)
            uploaded_files_data[f.name] = f.read()
            f.seek(0)
    
    session_manager.save_input_data(username, session_id, st.session_state.accounts, uploaded_files_data)
    
    status_placeholder = st.empty()
    status_placeholder.info("Matching in progress ⏳")
    result_df = process_files_cached(file_entries)

    result_df = result_df.rename(columns={
        "date": "Date", "bank": "Bank", "account": "Account",
        "description": "Description", "debit": "Debit", "credit": "Credit",
        "tmp_idx": "TmpIdx", "classification": "Classification", "pairid": "PairID",
        "GL Account": "GL Account", "GST": "GST", "GST Category": "GST Category",
        "Who": "Who", "Month": "Month", "Year": "Year"
    })

    status_placeholder.empty()

    if result_df is None or result_df.empty:
        st.info("No transactions processed or all files invalid.")
        st.session_state.reconciliation_results = None
        st.session_state.gst_calculated = False
        st.session_state.edited_df_cache = None
        st.session_state.pending_changes = {}
        st.session_state.updated_pages = set()
    else:
        result_df = calculate_gst(result_df)
        st.session_state.reconciliation_results = result_df
        st.session_state.edited_df_cache = result_df.copy()
        st.session_state.page_number = 1
        st.session_state.gst_calculated = True
        st.session_state.pending_changes = {}
        st.session_state.updated_pages = set()
        st.session_state.selected_rows = set()
        
        session_manager.save_output_data(
            username, session_id, result_df,
            st.session_state.pending_changes,
            st.session_state.updated_pages,
            st.session_state.page_number
        )
        
        st.session_state.active_tab = "output"


def render():
    pd.set_option("styler.render.max_elements", 5000000)

    username = st.session_state.user.get("username", "default_user") if st.session_state.get("logged_in") else "default_user"

    if "accounts" not in st.session_state:
        st.session_state.accounts = []
    if "accounts_metadata" not in st.session_state:
        st.session_state.accounts_metadata = []
    if "loaded_files_data" not in st.session_state:
        st.session_state.loaded_files_data = {}
    if "reconciliation_results" not in st.session_state:
        st.session_state.reconciliation_results = None
    if "file_uploader_key" not in st.session_state:
        st.session_state.file_uploader_key = 0
    if "page_number" not in st.session_state:
        st.session_state.page_number = 1
    if "page_size" not in st.session_state:
        st.session_state.page_size = 25
    if "show_gst" not in st.session_state:
        st.session_state.show_gst = True
    if "gst_calculated" not in st.session_state:
        st.session_state.gst_calculated = False
    if "edited_df_cache" not in st.session_state:
        st.session_state.edited_df_cache = None
    if "pending_changes" not in st.session_state:
        st.session_state.pending_changes = {}
    if "updated_pages" not in st.session_state:
        st.session_state.updated_pages = set()
    if "current_session_id" not in st.session_state:
        latest_session = session_manager.get_latest_session(username)
        if latest_session:
            load_session(latest_session)
        else:
            st.session_state.current_session_id = None
    if "selected_rows" not in st.session_state:
        st.session_state.selected_rows = set()
    if "filter_internal" not in st.session_state:
        st.session_state.filter_internal = True
    if "filter_incoming" not in st.session_state:
        st.session_state.filter_incoming = True
    if "filter_outgoing" not in st.session_state:
        st.session_state.filter_outgoing = True
    if "active_tab" not in st.session_state:
        st.session_state.active_tab = "input"

    st.markdown("""
        <style>
            .block-container { padding-top: 2rem; }
            .stTabs [data-baseweb="tab-list"] button [data-testid="stMarkdownContainer"] p {
                font-size: 1.2rem;
            }
        </style>
    """, unsafe_allow_html=True)

    st.markdown(
        "<h3 style='margin-top:0rem;margin-bottom:0rem'>🏦Bank Transactions Reconciliation</h3>",
        unsafe_allow_html=True,
    )

    tab_input, tab_output = st.tabs(["📥Input", "📊Output"])
    
    with tab_input:
        render_input_ui(username, run_agent_callback, load_session, save_current_session)
    
    with tab_output:
        render_output_ui(username, save_current_session)