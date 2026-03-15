# backend/open_banking/user_service.py
import sys
from pathlib import Path

import requests

if __package__ is None and __file__:
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.append(str(repo_root))

    from backend.open_banking.auth import get_access_token
else:
    from .auth import get_access_token

BASE_URL = "https://au-api.basiq.io"

def create_user_basiq_object(email, mobile):
    token = get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload = {
        "email": email,
        "mobile": mobile
    }

    res = requests.post(f"{BASE_URL}/users", json=payload, headers=headers)
    print("res",res.json())
    res.raise_for_status()
    return res.json()["id"]



def get_user(user_id):
    token = get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "accept": "application/json"
    }

    res = requests.get(
        f"{BASE_URL}/users/{user_id}",
        headers=headers
    )

    res.raise_for_status()
    return res.json()