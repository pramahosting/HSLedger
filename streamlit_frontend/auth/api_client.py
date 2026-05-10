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
    trading_batch_id: Optional[str] = None,
) -> Optional[dict]:
    """Save one manual purchase lot. Returns the saved lot dict (with id) or None."""
    try:
        response = httpx.post(
            f"{FASTAPI_BASE_URL}/trading/lots",
            headers=_auth_headers(token),
            json={
                "financial_year":   financial_year,
                "ticker":           ticker,
                "purchase_date":    purchase_date,
                "qty":              qty,
                "unit_price":       unit_price,
                "brokerage":        brokerage,
                "gst":              gst,
                "trading_batch_id": trading_batch_id,
            },
            timeout=10.0,
        )
        if response.status_code == 201:
            return response.json()
        return None
    except Exception:
        return None


def load_purchase_lots(
    token: str,
    financial_year: str,
    ticker: Optional[str] = None,
    trading_batch_id: Optional[str] = None,
) -> list[dict]:
    """Fetch saved lots for the current user, FY, and (optionally) upload batch."""
    try:
        params: dict = {"financial_year": financial_year}
        if ticker:
            params["ticker"] = ticker
        if trading_batch_id:
            params["trading_batch_id"] = trading_batch_id
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


def update_purchase_lot(
    token: str,
    lot_id: int,
    purchase_date: Optional[str] = None,
    qty: Optional[float] = None,
    unit_price: Optional[float] = None,
    brokerage: Optional[float] = None,
    gst: Optional[float] = None,
) -> Optional[dict]:
    """Edit a saved lot. Returns the updated lot dict or None on failure."""
    try:
        body: dict = {}
        if purchase_date is not None: body["purchase_date"] = purchase_date
        if qty           is not None: body["qty"]           = qty
        if unit_price    is not None: body["unit_price"]    = unit_price
        if brokerage     is not None: body["brokerage"]     = brokerage
        if gst           is not None: body["gst"]           = gst
        response = httpx.put(
            f"{FASTAPI_BASE_URL}/trading/lots/{lot_id}",
            headers=_auth_headers(token),
            json=body,
            timeout=10.0,
        )
        if response.status_code == 200:
            return response.json()
        return None
    except Exception:
        return None


def delete_lots_by_batch(token: str, financial_year: str, trading_batch_id: str) -> bool:
    """Delete all lots for the current user + FY + trading_batch_id. Returns True on success."""
    try:
        response = httpx.delete(
            f"{FASTAPI_BASE_URL}/trading/lots",
            headers=_auth_headers(token),
            params={"financial_year": financial_year, "trading_batch_id": trading_batch_id},
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


# ── Crypto tax report persistence ─────────────────────────────────────────────
# Separate endpoints — never reuse stock report storage.

def save_crypto_tax_report(
    token: str,
    financial_year: str,
    net_taxable_gain: float,
    gross_capital_gains: float,
    gross_capital_losses: float,
    cgt_discount_applied: float,
    report_json: Optional[str] = None,
) -> Optional[dict]:
    """Persist a crypto CGT report summary to /trading/crypto/reports. Returns saved record or None."""
    try:
        response = httpx.post(
            f"{FASTAPI_BASE_URL}/trading/crypto/reports",
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


def list_crypto_tax_reports(token: str, financial_year: str) -> list[dict]:
    """List past crypto tax reports for the current user and FY, newest first."""
    try:
        response = httpx.get(
            f"{FASTAPI_BASE_URL}/trading/crypto/reports",
            headers=_auth_headers(token),
            params={"financial_year": financial_year},
            timeout=10.0,
        )
        if response.status_code == 200:
            return response.json()
        return []
    except Exception:
        return []


# ── Crypto manual lot persistence ─────────────────────────────────────────────
# Stored in /trading/crypto/lots — never mixed with shares lots.

def save_crypto_lot(
    token:            str,
    financial_year:   str,
    asset:            str,
    acquisition_date: str,
    qty:              float,
    total_cost_aud:   float,
    fee_aud:          float = 0.0,
    crypto_batch_id:  Optional[str] = None,
) -> Optional[dict]:
    """Save one manual crypto acquisition lot. Returns the saved lot dict (with id) or None."""
    try:
        response = httpx.post(
            f"{FASTAPI_BASE_URL}/trading/crypto/lots",
            headers=_auth_headers(token),
            json={
                "financial_year":   financial_year,
                "asset":            asset,
                "acquisition_date": acquisition_date,
                "qty":              qty,
                "total_cost_aud":   total_cost_aud,
                "fee_aud":          fee_aud,
                "crypto_batch_id":  crypto_batch_id,
            },
            timeout=10.0,
        )
        if response.status_code == 201:
            return response.json()
        return None
    except Exception:
        return None


def load_crypto_lots(
    token:           str,
    financial_year:  Optional[str] = None,
    asset:           Optional[str] = None,
    crypto_batch_id: Optional[str] = None,
) -> list[dict]:
    """Fetch saved crypto lots for the current user, filtered by FY, asset, and/or batch."""
    try:
        params: dict = {}
        if financial_year:
            params["financial_year"] = financial_year
        if asset:
            params["asset"] = asset
        if crypto_batch_id:
            params["crypto_batch_id"] = crypto_batch_id
        response = httpx.get(
            f"{FASTAPI_BASE_URL}/trading/crypto/lots",
            headers=_auth_headers(token),
            params=params,
            timeout=10.0,
        )
        if response.status_code == 200:
            return response.json()
        return []
    except Exception:
        return []


def delete_crypto_lot(token: str, lot_id: int) -> bool:
    """Delete a saved crypto lot by id. Returns True on success."""
    try:
        response = httpx.delete(
            f"{FASTAPI_BASE_URL}/trading/crypto/lots/{lot_id}",
            headers=_auth_headers(token),
            timeout=10.0,
        )
        return response.status_code == 204
    except Exception:
        return False


def update_crypto_lot(
    token:            str,
    lot_id:           int,
    acquisition_date: Optional[str]   = None,
    qty:              Optional[float] = None,
    total_cost_aud:   Optional[float] = None,
    fee_aud:          Optional[float] = None,
) -> Optional[dict]:
    """Edit a saved crypto lot. Returns the updated lot dict or None on failure."""
    try:
        body: dict = {}
        if acquisition_date is not None: body["acquisition_date"] = acquisition_date
        if qty              is not None: body["qty"]              = qty
        if total_cost_aud   is not None: body["total_cost_aud"]   = total_cost_aud
        if fee_aud          is not None: body["fee_aud"]          = fee_aud
        response = httpx.put(
            f"{FASTAPI_BASE_URL}/trading/crypto/lots/{lot_id}",
            headers=_auth_headers(token),
            json=body,
            timeout=10.0,
        )
        if response.status_code == 200:
            return response.json()
        return None
    except Exception:
        return None


def delete_crypto_lots_by_batch(
    token:           str,
    financial_year:  str,
    crypto_batch_id: str,
) -> bool:
    """Delete all crypto lots for the current user + FY + crypto_batch_id."""
    try:
        response = httpx.delete(
            f"{FASTAPI_BASE_URL}/trading/crypto/lots",
            headers=_auth_headers(token),
            params={"financial_year": financial_year, "crypto_batch_id": crypto_batch_id},
            timeout=10.0,
        )
        return response.status_code == 204
    except Exception:
        return False