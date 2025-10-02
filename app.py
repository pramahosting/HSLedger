# frontend/app.py
import streamlit as st
import os
import sys

# --- Add frontend path ---
current_dir = os.path.dirname(os.path.abspath(__file__))
frontend_dir = current_dir
if frontend_dir not in sys.path:
    sys.path.append(frontend_dir)

# --- Import components ---
from frontend.components import navbar, reconciliation_ui, trading_ui

# --- Auth module path ---
auth_dir = os.path.join(current_dir, "Auth")
if auth_dir not in sys.path:
    sys.path.append(auth_dir)

from auth.auth_json_module import auth_ui

# --- Streamlit config ---
st.set_page_config(page_title="HSLedger", layout="wide")

# --- Clear session on first load ---
if "initialized" not in st.session_state:
    st.session_state.clear()
    st.session_state.logged_in = False
    st.session_state.user = {}
    st.session_state.initialized = True

# --- Handle logout request ---
if st.session_state.get("logout_request", False):
    st.session_state.logged_in = False
    st.session_state.user = {}
    st.session_state.logout_request = False
    st.rerun()

# --- Show login if not logged in ---
if not st.session_state.get("logged_in", False):
    auth_ui()
    st.stop()

# --- Main title ---
st.title("HSLedger - Reconciliation & Analysis")

# --- Navigation ---
tab = navbar.render_navbar()

if tab == "Reconciliation":
    reconciliation_ui.render()
elif tab == "Trading":
    trading_ui.render()
else:
    st.markdown(
        """
        Navigate using the top menu to Reconciliation or Trading.
        """
    )
