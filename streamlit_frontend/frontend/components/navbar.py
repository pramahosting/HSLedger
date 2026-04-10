# frontend/components/navbar.py
import streamlit as st

def render_navbar(is_admin=False):
    st.sidebar.title("HSLedger")
    modules = [
        "Reconciliation",
        "Open Banking",
        "Trading",
        "Invoice",
        "Invoice Extractor",
        "Cash Flow",
    ]

    if is_admin:
        modules.append("ML_Classifier")

    tab = st.sidebar.radio(
        "Choose module",
        modules,
    )
    return tab
