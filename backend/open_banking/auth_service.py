# backend/open_banking/auth_service.py
import requests
from .auth import get_access_token

BASE_URL = "https://au-api.basiq.io"

def create_auth_link(user_id, mobile=None):
    token = get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "accept": "application/json"
    }

    payload = {"mobile": mobile} if mobile else {}

    res = requests.post(
        f"{BASE_URL}/users/{user_id}/auth_link",
        json=payload,
        headers=headers
    )

    res.raise_for_status()
    return res.json()["links"]["public"]


def get_consents(user_id):
    token = get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "accept": "application/json"
    }

    res = requests.get(
        f"{BASE_URL}/users/{user_id}/consents",
        headers=headers
    )

    res.raise_for_status()
    return res.json()


def get_auth_link(user_id):
    token = get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "accept": "application/json"
    }

    res = requests.get(
        f"{BASE_URL}/users/{user_id}/auth_link",
        headers=headers
    )

    res.raise_for_status()
    return res.json()["links"]["public"]
