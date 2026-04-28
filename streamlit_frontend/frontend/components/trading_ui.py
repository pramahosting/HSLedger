import streamlit as st
from frontend.components.shares_trading_taxation_ui import render as _render_shares_taxation


def render():
    st.markdown("""
        <style>.block-container { padding-top: 2rem; }</style>
    """, unsafe_allow_html=True)

    submodule = st.selectbox(
        "Trading submodule",
        ["Shares Trading Taxation"],
        label_visibility="collapsed",
        key="trading_submodule_select",
    )

    if submodule == "Shares Trading Taxation":
        _render_shares_taxation()