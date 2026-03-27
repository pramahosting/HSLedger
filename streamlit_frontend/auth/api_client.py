import httpx
import math
from datetime import date, datetime

FASTAPI_BASE_URL = "http://localhost:8000"


def _json_safe(value):
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return value

def login(email: str, password: str):
    try:
        response = httpx.post(
            f'{FASTAPI_BASE_URL}/auth/login',
            json={"email": email, "password": password},
            timeout=10.0
        )

        if response.status_code == 200:
            return response.json()
        
        return None
    
    except httpx.ConnectError as e:
        raise ConnectionError(f"Cannot connect to the backend server.\nAn error occurred while connecting to the authentication service: {e}")
    
def register(username, full_name, email, password, phone, address, role=None):
    try:
        payload = {
            "username": username,
            "full_name": full_name,
            "email": email,
            "password": password,
            "phone": phone,
            "address": address
        }
        if role:
            payload["role"] = role
        response = httpx.post(
            f'{FASTAPI_BASE_URL}/auth/register',
            json=payload,
            timeout=10.0
        )

        if response.status_code == 200:
            return response.json()
        
        return None
    
    except httpx.ConnectError as e:
        raise ConnectionError(f"Cannot connect to the backend server.\nAn error occurred while connecting to the authentication service: {e}")
    
def save_transactions(user_id, df):
    columns = ["date", "bank", "account", "description", "debit", "credit", "classification", "pair_id", "gl_account", "gst", "gst_category", "who"]

    available = [col for col in columns if col in df.columns]

    raw_rows = df.where(df.notna(), None)[available].to_dict(orient='records')
    rows = [{k: _json_safe(v) for k, v in row.items()} for row in raw_rows]

    try:
        response = httpx.post(
            f'{FASTAPI_BASE_URL}/transactions/save',
            json={"user_id": user_id, "transactions": rows},
            timeout=30.0
        )

        response.raise_for_status()
        return response.json()
    
    except httpx.ConnectError as e:
        raise ConnectionError(f"Cannot connect to the backend server.\nAn error occurred while connecting to the transactions service: {e}")
    except httpx.HTTPStatusError as e:
        raise RuntimeError(
            f"Transaction save API failed: {e.response.status_code} {e.response.text}"
        )