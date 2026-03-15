import requests
from .auth import get_access_token

BASE_URL = "https://au-api.basiq.io"


def get_job_status(job_id: str):
    """
    Retrieve the status of a Basiq job.
    Returns job details including step status (pending, in-progress, success, failed).
    """
    if not job_id:
        raise ValueError("job_id must be provided")

    token = get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    res = requests.get(f"{BASE_URL}/jobs/{job_id}", headers=headers, timeout=60)
    res.raise_for_status()
    return res.json()


def get_accounts(user_id: str):
    """
    Retrieve all accounts for a user after successful connection.
    """
    if not user_id:
        raise ValueError("user_id must be provided")

    token = get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    res = requests.get(f"{BASE_URL}/users/{user_id}/accounts", headers=headers, timeout=60)
    res.raise_for_status()
    return res.json().get("data", [])


def get_account_details(account_id: str):
    """
    Retrieve detailed account information for a specific account.
    """
    if not account_id:
        raise ValueError("account_id must be provided")

    token = get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    res = requests.get(f"{BASE_URL}/accounts/{account_id}", headers=headers, timeout=60)
    res.raise_for_status()
    return res.json()


def get_transactions(account_id: str):
    """
    Retrieve transactions for a specific account.
    """
    if not account_id:
        raise ValueError("account_id must be provided")

    token = get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    res = requests.get(
        f"{BASE_URL}/accounts/{account_id}/transactions",
        headers=headers,
        timeout=60
    )
    res.raise_for_status()
    return res.json().get("data", [])


def get_transaction(user_id: str, transaction_id: str):
    """
    Retrieve a single transaction for a specific user.
    """
    if not user_id:
        raise ValueError("user_id must be provided")
    if not transaction_id:
        raise ValueError("transaction_id must be provided")

    token = get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    res = requests.get(
        f"{BASE_URL}/users/{user_id}/transactions/{transaction_id}",
        headers=headers,
        timeout=60,
    )
    res.raise_for_status()
    return res.json()


def create_statement(user_id: str, institution_id: str, file_name: str, file_bytes: bytes, content_type: str):
    """
    Create a statement upload job for a user.
    """
    if not user_id:
        raise ValueError("user_id must be provided")
    if not institution_id:
        raise ValueError("institution_id must be provided")
    if not file_name or not file_bytes:
        raise ValueError("statement file must be provided")

    token = get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    data = {
        "institutionId": institution_id,
    }

    files = {
        "statement": (file_name, file_bytes, content_type or "application/octet-stream"),
    }

    res = requests.post(
        f"{BASE_URL}/users/{user_id}/statements",
        headers=headers,
        data=data,
        files=files,
        timeout=120,
    )
    res.raise_for_status()
    return res.json()

