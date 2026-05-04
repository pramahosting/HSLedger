import httpx
import math
from datetime import date, datetime
from typing import Optional

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


def _auth_headers(token: str) -> dict:
    """Build the X-Auth-Token header. user_id is never sent in the body."""
    return {"X-Auth-Token": token}


def login(email: str, password: str):
    """Returns {"token": str, "user": {...}} or None on failure."""
    try:
        response = httpx.post(
            f"{FASTAPI_BASE_URL}/auth/login",
            json={"email": email, "password": password},
            timeout=10.0,
        )
        if response.status_code == 200:
            return response.json()
        return None
    except httpx.ConnectError as e:
        raise ConnectionError(
            f"Cannot connect to the backend server.\n"
            f"An error occurred while connecting to the authentication service: {e}"
        )
    
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


# ── Trading / Tax persistence ──────────────────────────────────────────────────
# All functions accept a token parameter and send X-Auth-Token header.
# user_id is NEVER sent in the request body — the backend derives it from the token.

def save_purchase_lot(
    token: str,
    financial_year: str,
    ticker: str,
    purchase_date: str,
    qty: float,
    unit_price: float,
    brokerage: float = 0.0,
    gst: float = 0.0,
) -> Optional[dict]:
    """Save one manual purchase lot. Returns the saved lot dict (with id) or None."""
    try:
        response = httpx.post(
            f"{FASTAPI_BASE_URL}/trading/lots",
            headers=_auth_headers(token),
            json={
                "financial_year": financial_year,
                "ticker":         ticker,
                "purchase_date":  purchase_date,
                "qty":            qty,
                "unit_price":     unit_price,
                "brokerage":      brokerage,
                "gst":            gst,
            },
            timeout=10.0,
        )
        if response.status_code == 201:
            return response.json()
        return None
    except Exception:
        return None


def load_purchase_lots(token: str, financial_year: str, ticker: Optional[str] = None) -> list[dict]:
    """Fetch all saved lots for the current user and FY. Returns [] on any failure."""
    try:
        params: dict = {"financial_year": financial_year}
        if ticker:
            params["ticker"] = ticker
        response = httpx.get(
            f"{FASTAPI_BASE_URL}/trading/lots",
            headers=_auth_headers(token),
            params=params,
            timeout=10.0,
        )
        if response.status_code == 200:
            return response.json()
        return []
    except Exception:
        return []


def delete_purchase_lot(token: str, lot_id: int) -> bool:
    """Delete a saved lot by id. Returns True on success."""
    try:
        response = httpx.delete(
            f"{FASTAPI_BASE_URL}/trading/lots/{lot_id}",
            headers=_auth_headers(token),
            timeout=10.0,
        )
        return response.status_code == 204
    except Exception:
        return False


def save_tax_report(
    token: str,
    financial_year: str,
    net_taxable_gain: float,
    gross_capital_gains: float,
    gross_capital_losses: float,
    cgt_discount_applied: float,
    report_json: Optional[str] = None,
) -> Optional[dict]:
    """Persist a generated tax report summary. Returns saved record or None."""
    try:
        response = httpx.post(
            f"{FASTAPI_BASE_URL}/trading/reports",
            headers=_auth_headers(token),
            json={
                "financial_year":       financial_year,
                "net_taxable_gain":     net_taxable_gain,
                "gross_capital_gains":  gross_capital_gains,
                "gross_capital_losses": gross_capital_losses,
                "cgt_discount_applied": cgt_discount_applied,
                "report_json":          report_json,
            },
            timeout=10.0,
        )
        if response.status_code == 201:
            return response.json()
        return None
    except Exception:
        return None


def list_tax_reports(token: str, financial_year: str) -> list[dict]:
    """List past tax reports for the current user and FY, newest first."""
    try:
        response = httpx.get(
            f"{FASTAPI_BASE_URL}/trading/reports",
            headers=_auth_headers(token),
            params={"financial_year": financial_year},
            timeout=10.0,
        )
        if response.status_code == 200:
            return response.json()
        return []
    except Exception:
        return []