# tests/test_bank_upload_app.py
import streamlit as st
from frontend.components.reconciliation_input_bank import render_bank_input_ui

# Minimal session initialization for testing
if "accounts" not in st.session_state:
    st.session_state.accounts = []
if "accounts_metadata" not in st.session_state:
    st.session_state.accounts_metadata = []
if "file_uploader_key" not in st.session_state:
    st.session_state.file_uploader_key = 0
if "logged_in" not in st.session_state:
    st.session_state.logged_in = True  # pretend user is logged in for test
if "user" not in st.session_state:
    st.session_state.user = {"username": "test_user"}
if "current_session_id" not in st.session_state:
    st.session_state.current_session_id = "TEST"

# Dummy functions to match required signature
def dummy_process_files_cached(entries):
    st.write("process_files_cached called (dummy).")
    return None

def dummy_load_session(x):
    return False

def dummy_save_current_session(x=None):
    pass

st.title("Test: Bank Upload Module (Mock)")

render_bank_input_ui(
    username=st.session_state.user.get("username", "test_user"),
    process_files_cached=dummy_process_files_cached,
    load_session_fn=dummy_load_session,
    save_current_session_fn=dummy_save_current_session,
)

st.markdown("---")
st.write("Session `accounts` object (for debugging):")
st.write(st.session_state.get("accounts", []))
