# backend/open_banking/transaction_service.py
import requests
from .basiq_client import get_access_token

BASE_URL = "https://au-api.basiq.io"

def fetch_transactions(user_id):
    token = get_access_token()

    headers = {
        "Authorization": f"Bearer {token}"
    }

    acc_res = requests.get(
        f"{BASE_URL}/users/{user_id}/accounts",
        headers=headers
    )
    acc_res.raise_for_status()

    accounts = acc_res.json()["data"]
    transactions = []

    for acc in accounts:
        acc_id = acc["id"]
        tx_res = requests.get(
            f"{BASE_URL}/accounts/{acc_id}/transactions",
            headers=headers
        )
        tx_res.raise_for_status()
        transactions.extend(tx_res.json()["data"])

    return transactions
