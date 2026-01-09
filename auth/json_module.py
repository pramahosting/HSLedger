# FILE: db_module.py
import json
import os
import bcrypt
import uuid
from datetime import datetime, timedelta
import smtplib
import ssl
from email.mime.text import MIMEText
import streamlit as st
from typing import Optional, List, Dict
import tempfile

current_dir = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(current_dir, "users.json")

# ===== EMAIL SETTINGS =====
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_PASS = os.getenv("EMAIL_PASS", "")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# ===== Helpers =====
_write_lock = None

def _ensure_lock():
    global _write_lock
    if _write_lock is None:
        import threading
        _write_lock = threading.Lock()
    return _write_lock


def _load_users() -> List[Dict]:
    if not os.path.exists(USERS_FILE):
        return []
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return []


def _save_users(users: List[Dict]):
    lock = _ensure_lock()
    with lock:
        dirpath = os.path.dirname(os.path.abspath(USERS_FILE))
        if not os.path.exists(dirpath):
            os.makedirs(dirpath, exist_ok=True)
        fd, path = tempfile.mkstemp(dir=dirpath, text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as tmpf:
            json.dump(users, tmpf, indent=2, ensure_ascii=False)
        os.replace(path, USERS_FILE)


def init_db():
    if not os.path.exists(USERS_FILE):
        _save_users([])


init_db()

# ===== Database-like API (JSON-backed) =====

def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _iso_to_dt(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except Exception:
        return None


def get_user(email: str) -> Optional[Dict]:
    users = _load_users()
    for u in users:
        if u.get("email", "").lower() == (email or "").lower():
            return u.copy()
    return None


def get_user_count() -> int:
    users = _load_users()
    return len(users)


def add_user(name: str, email: str, password: str, address: str = "", company: str = "", phone: str = ""):
    users = _load_users()
    normalized = (email or "").lower()
    if any(u.get("email", "").lower() == normalized for u in users):
        raise ValueError("Email already exists")
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    is_admin = len(users) == 0
    new_id = max((u.get("id", 0) for u in users), default=0) + 1
    user = {
        "id": new_id,
        "name": name,
        "email": email,
        "password_hash": hashed,
        "address": address,
        "company": company,
        "phone": phone,
        "is_admin": bool(is_admin),
        "reset_token": None,
        "reset_expiry": None
    }
    users.append(user)
    _save_users(users)
    return user


def update_password(email: str, new_password: str):
    users = _load_users()
    updated = False
    for u in users:
        if u.get("email", "").lower() == (email or "").lower():
            u["password_hash"] = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
            u["reset_token"] = None
            u["reset_expiry"] = None
            updated = True
            break
    if updated:
        _save_users(users)
    else:
        raise ValueError("User not found")


def set_reset_token(email: str) -> str:
    users = _load_users()
    token = str(uuid.uuid4())
    expiry = (datetime.utcnow() + timedelta(minutes=30)).isoformat()
    updated = False
    for u in users:
        if u.get("email", "").lower() == (email or "").lower():
            u["reset_token"] = token
            u["reset_expiry"] = expiry
            updated = True
            break
    if updated:
        _save_users(users)
        return token
    else:
        raise ValueError("User not found")


def get_user_by_token(token: str) -> Optional[Dict]:
    users = _load_users()
    now = datetime.utcnow()
    for u in users:
        if u.get("reset_token") == token:
            expiry = _iso_to_dt(u.get("reset_expiry"))
            if expiry and expiry > now:
                return u.copy()
            return None
    return None


def update_user(user_id: int, name: str, email: str, address: str, company: str, phone: str, is_admin: bool):
    users = _load_users()
    # ensure email uniqueness except for same user
    normalized = (email or "").lower()
    for u in users:
        if u.get("id") != user_id and u.get("email", "").lower() == normalized:
            raise ValueError("Email already exists for another user")
    updated = False
    for u in users:
        if u.get("id") == user_id:
            u["name"] = name
            u["email"] = email
            u["address"] = address
            u["company"] = company
            u["phone"] = phone
            u["is_admin"] = bool(is_admin)
            updated = True
            break
    if updated:
        _save_users(users)
    else:
        raise ValueError("User not found")


def delete_user(user_id: int):
    users = _load_users()
    new_users = [u for u in users if u.get("id") != user_id]
    if len(new_users) == len(users):
        raise ValueError("User not found")
    _save_users(new_users)


def get_all_users(search_query: Optional[str] = None) -> List[Dict]:
    users = _load_users()
    if search_query:
        q = search_query.lower()
        return [u.copy() for u in users if q in (u.get("name", "") or "").lower() or q in (u.get("email", "") or "").lower()]
    return [u.copy() for u in users]

# get_connection kept for compatibility with code that expects it; returns None

def get_connection():
    return None

# ===== EMAIL FUNCTION =====

def send_reset_email(email: str, token: str):
    reset_link = f"http://localhost:8501?reset_token={token}"
    body = f"Click the link to reset your password:\n\n{reset_link}\n\nThis link expires in 30 minutes."
    msg = MIMEText(body)
    msg["Subject"] = "Password Reset Request"
    msg["From"] = EMAIL_USER
    msg["To"] = email

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls(context=context)
            if EMAIL_USER and EMAIL_PASSWORD:
                server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_USER or "no-reply", email, msg.as_string())
        st.success("Password reset link sent to your email.")
    except Exception as e:

        st.error(f"Error sending email: {e}")

