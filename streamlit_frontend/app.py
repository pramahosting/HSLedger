import streamlit as st
import os
import sys

# --- Add frontend path ---
current_dir = os.path.dirname(os.path.abspath(__file__))
frontend_dir = current_dir
if frontend_dir not in sys.path:
    sys.path.append(frontend_dir)

project_root_dir = os.path.dirname(current_dir)
if project_root_dir not in sys.path:
    sys.path.append(project_root_dir)

# --- Auth module path ---
auth_dir = os.path.join(current_dir, "Auth")
if auth_dir not in sys.path:
    sys.path.append(auth_dir)

# --- Import components ---
from auth.auth_json_module import auth_ui
from frontend.components import navbar, reconciliation_ui, trading_ui, rdr_ui, openbanking_ui, invoice_ui, invoice_extractor_ui, train_model_ui, cash_flow_ui

# --- Streamlit config ---
st.set_page_config(page_title="HSLedger", layout="wide")

# --- Load external CSS ---
def local_css(file_name):
    with open(file_name) as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

local_css(os.path.join("frontend", "static", "css", "style.css"))

# --- Clear session on first load ---
if "initialized" not in st.session_state:
    st.session_state.clear()
    st.session_state.logged_in = False
    st.session_state.user = {}
    st.session_state.initialized = True
    st.session_state.session_loaded = False

# --- Handle logout request ---
if st.session_state.get("logout_request", False):
    # Clear all session data including reconciliation data
    keys_to_clear = [
        "logged_in", "user", "logout_request",
        "reconciliation_results", "page_number", "accounts", 
        "gst_calculated", "edited_df_cache", "pending_changes", 
        "updated_pages", "current_session_id", "accounts_metadata",
        "loaded_files_data", "selected_rows", "session_loaded"
    ]
    for key in keys_to_clear:
        if key in st.session_state:
            del st.session_state[key]
    
    st.session_state.logged_in = False
    st.session_state.user = {}
    st.session_state.logout_request = False
    st.session_state.session_loaded = False
    st.rerun()

# --- Show header BEFORE deciding page type ---
if not st.session_state.get("logged_in", False):
    # Header for login (smaller top margin)
    st.markdown(
        """
        <div class="auth-page">
            <div class="header-bar auth">
                <div class="header-title">HSLedger - Reconciliation & Analysis</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )
    # Show login form
    auth_ui()
    st.stop()
else:
    # Header for main pages
    st.markdown(
        """
        <div class="header-bar">
            <div class="header-title">HSLedger - Reconciliation & Analysis</div>
        </div>
        """,
        unsafe_allow_html=True
    )

# --- Auto-load latest session on login ---
if st.session_state.get("logged_in") and not st.session_state.get("session_loaded", False):
    from backend.reconciliation.session_manager import session_manager
    
    username = st.session_state.user.get("username", "default_user")
    latest_session = session_manager.get_latest_session(username)
    
    if latest_session:
        # Session will be loaded automatically in reconciliation_ui.render()
        st.session_state.session_loaded = True

# ==================================================
# ✅ Sidebar Logout Button
# ==================================================
with st.sidebar:
    if st.button("🚪Logout", use_container_width=True):
        st.session_state.logout_request = True
        st.rerun()
# ==================================================

# --- Navigation ---
current_user = st.session_state.get("user", {}) or {}
role_names = [str(r).strip().lower() for r in current_user.get("roles", [])]
is_admin = bool(current_user.get("is_admin", False) or ("admin" in role_names))
tab = navbar.render_navbar(is_admin=is_admin)

if tab == "Reconciliation":
    reconciliation_ui.render()
elif tab == "Open Banking":
    openbanking_ui.render()
elif tab == "Trading":
    trading_ui.render()
elif tab == "Invoice":
    invoice_ui.render()
elif tab == "Invoice Extractor":
    invoice_extractor_ui.render()
elif tab == "Cash Flow":
    cash_flow_ui.render()
elif tab == "ML_Classifier":
    if not is_admin:
        st.error("Access denied. Admin users only.")
    else:
        ml_tab1, ml_tab2 = st.tabs(["Train Mode", "RDR Rule Editor"])
        with ml_tab1:
            train_model_ui.render()
        with ml_tab2:
            rdr_ui.render()
else:
    st.markdown(
        """
        Navigate using the top menu to Reconciliation or Trading.
        """
    )

