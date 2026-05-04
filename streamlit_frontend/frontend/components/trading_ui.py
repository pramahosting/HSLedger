import streamlit as st
from frontend.components.shares_trading_taxation_ui import render as _render_shares_taxation
from frontend.components.crypto_trading_taxation_ui import render as _render_crypto_taxation


def render():
    st.markdown("""
        <style>.block-container { padding-top: 2rem; }</style>
    """, unsafe_allow_html=True)

    stocks_tab, crypto_tab = st.tabs(["📈 Stocks / Shares", "₿ Crypto"])

    with stocks_tab:
        _render_shares_taxation()

    with crypto_tab:
        _render_crypto_taxation()
