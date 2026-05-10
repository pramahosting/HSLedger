# ---------------------------
# FILE: auth_module.py
import streamlit as st
from datetime import datetime, timedelta
import extra_streamlit_components as stx
from auth.api_client import login, register

# ===== COOKIE MANAGER =====
def get_cookie_manager():
    return stx.CookieManager()


# ===== LOGIN TAB =====
def login_tab(cookie_manager):
    st.subheader("Login")
    saved_email = ''
    try:
        saved_email = cookie_manager.get("auth_email") or ''
    except Exception as e:
        pass


    email = st.text_input("Email or Username", key="login_email")
    password = st.text_input("Password", type="password", key="login_password")
    remember_me = st.checkbox("Remember me", key="login_remember")

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("Login", key="login_btn"):
            # user = get_user(email)
            # if user and bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
            #     st.session_state.logged_in = True
            #     st.session_state.user = user
            #     if remember_me:
            #         cookie_manager.set("auth_email", email, expires_at=datetime.now() + timedelta(days=30))
            #         cookie_manager.set("auth_password", password, expires_at=datetime.now() + timedelta(days=30))
            #     else:
            #         if "auth_email" in cookie_manager.cookies:
            #             cookie_manager.delete("auth_email")
            #             cookie_manager.delete("auth_password")
            #     st.rerun()
            # else:
            #     st.error("Invalid email or password")
            if not email or not password:
                st.warning("Please enter both email and password.")
                return
            
            try:
                user = login(email, password)
            
            except ConnectionError as e:
                st.error(str(e))
                return
            
            if user:
                # Login now returns {"token": "...", "user": {...}}.
                # Extract both pieces; fall back to flat dict for graceful degradation.
                auth_token = user.get("token", "")
                user_data  = user.get("user", user)
                role_names = [str(r).strip().lower() for r in user_data.get("roles", [])]
                user_data["is_admin"] = "admin" in role_names
                st.session_state.logged_in  = True
                st.session_state.user       = user_data
                st.session_state.auth_token = auth_token
                if remember_me:
                    cookie_manager.set("auth_email", email, expires_at=datetime.now() + timedelta(days=30))
                    cookie_manager.set("auth_password", password, expires_at=datetime.now() + timedelta(days=30))
                else:
                    try:
                        cookie_manager.delete("auth_email")
                        cookie_manager.delete("auth_password")
                    except Exception:
                        pass
                st.rerun()
            else:
                st.error("Invalid email or password")

    with col2:
        if st.button("Forgot Password?", key="forgot_btn"):
            st.info("Password reset is not available. Contact your administrator.")

    # Safely delete other related cookies if needed
    for cookie_name in ["auth_token", "user_role"]:  # add any other cookies you use
        if cookie_name in cookie_manager.cookies:
            cookie_manager.delete(cookie_name)


# ===== RESET PASSWORD =====
def reset_password_ui(token):
    st.error("Password reset via link is not available. Contact your administrator.")


# ===== SIGNUP TAB =====
def signup_tab():
    st.subheader("Sign Up")
    name = st.text_input("Full Name", key="signup_name")
    username = st.text_input("Username", key="signup_username")
    email = st.text_input("Email", key="signup_email")
    password = st.text_input("Password", type="password", key="signup_password")
    address = st.text_area("Address", key="signup_address")
    company = st.text_input("Company", key="signup_company")
    phone = st.text_input("Phone", key="signup_phone")

    # Role selection: only allow admin for first user, else user only
    import sqlite3
    roles = []
    try:
        conn = sqlite3.connect("../reconciliation.db")
        cur = conn.cursor()
        cur.execute("SELECT name FROM roles ORDER BY name;")
        roles = [row[0] for row in cur.fetchall()]
        conn.close()
    except Exception:
        roles = ["user", "admin"]

    # Only allow admin if no users exist
    is_first_user = False
    try:
        conn = sqlite3.connect("../reconciliation.db")
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users;")
        is_first_user = cur.fetchone()[0] == 0
        conn.close()
    except Exception:
        pass

    allowed_roles = roles if is_first_user else [r for r in roles if r != "admin"]
    selected_role = st.selectbox("Role", allowed_roles, key="signup_role")

    if st.button("Sign Up", key="signup_btn"):
        username = username.strip()
        full_name = name.strip()

        if not username or not full_name or not email or not password:
            st.warning("Please enter full name, username, email, and password.")
            return

        try:
            user = register(username, full_name, email.strip(), password, phone, address, selected_role)
        except ConnectionError as e:
            st.error(str(e))
            return

        if user:
            st.success("Account created! Please log in.")
        else:
            st.error("Signup failed. Email or username may already be registered.")


# ===== ADMIN PANEL =====
def admin_panel():
    st.info("Admin user management is handled via the backend API. Use the database admin tools directly.")


# ===== MAIN AUTH FUNCTION =====
def auth_ui():
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
    if "user" not in st.session_state or st.session_state.get("user") is None:
        st.session_state.user = {}

    cookie_manager = get_cookie_manager()
    query_params = st.query_params

    # RESET PASSWORD MODE
    if "reset_token" in query_params:
        reset_password_ui(query_params["reset_token"])
        return False  # Not admin

    # AUTO-LOGIN FROM COOKIES
    # if not st.session_state.logged_in:
    #     saved_email = cookie_manager.get("auth_email")
    #     saved_password = cookie_manager.get("auth_password")
    #     if saved_email and saved_password:
    #         user = get_user(saved_email)
    #         if user and bcrypt.checkpw(saved_password.encode(), user["password_hash"].encode()):
    #             st.session_state.logged_in = True
    #             st.session_state.user = user

    # SHOW UI
    if st.session_state.logged_in:
        if st.session_state.user.get("is_admin", False):
            # Admin header + logout button
            col1, col2 = st.columns([6, 1])
            with col1:
                st.subheader("Admin Control Panel")
            with col2:
                if st.button("Logout", key="logout_btn"):
                    st.session_state.logged_in = False
                    st.session_state.user = {}
                    try:
                        cookie_manager.delete("auth_email")
                        cookie_manager.delete("auth_password")
                    except KeyError:
                        pass

            admin_panel()
            return True  # Admin logged in, app.py should not render dashboard

        # Normal user
        return False  # Not admin, app.py can render dashboard

    # --- Show login/signup if not logged in ---
    st.markdown(
        """
        <div class="auth-title">
            Authentication
        </div>
        <style>
        .auth-title {
            font-size: 2rem;   /* same as st.title */
            margin-top: 0px;
            margin-bottom: 10px;
            font-weight: bold;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

    tab1, tab2 = st.tabs(["Login", "Sign Up"])
    with tab1:
        login_tab(cookie_manager)
    with tab2:
        signup_tab()

    return False  # Not admin


