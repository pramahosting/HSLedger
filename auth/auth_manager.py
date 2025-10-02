# backend/auth/auth_manager.py
import streamlit as st
from typing import Optional, Dict

SESSION_USER_KEY = "hsl_user"

def authenticate_user(username: str = "demo_user") -> Dict:
    """
    Placeholder authentication. Stores a simple user in Streamlit session_state.
    Replace this with your real authentication integration.
    """
    user = {"username": username, "role": "user"}
    st.session_state[SESSION_USER_KEY] = user
    return user

def current_user() -> Optional[Dict]:
    return st.session_state.get(SESSION_USER_KEY)

def logout_user() -> None:
    """Clear session auth."""
    if SESSION_USER_KEY in st.session_state:
        del st.session_state[SESSION_USER_KEY]
