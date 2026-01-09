# frontend/components/navbar.py
import streamlit as st

def render_navbar():
    st.sidebar.title("HSLedger")
    tab = st.sidebar.radio("Choose module", ["Reconciliation", "Trading"])
    return tab
