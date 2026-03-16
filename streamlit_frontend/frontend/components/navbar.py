# frontend/components/navbar.py
import streamlit as st

def render_navbar():
    st.sidebar.title("HSLedger")
    tab = st.sidebar.radio(
        "Choose module",
        [
            "Reconciliation",
            "Open Banking",
            "Trading",
            "Invoice",
            "RDR Rules Editor",
            "Upload CSV To DB",
        ],
    )
    return tab
