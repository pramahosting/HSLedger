import requests
import sys
import os
from dotenv import load_dotenv
from pathlib import Path

if __package__ is None and __file__:
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.append(str(repo_root))

    from backend.open_banking.auth import get_access_token
else:
    from .auth import get_access_token

load_dotenv()

BASIQ_BASE_URL = os.getenv("BASIQ_BASE_URL")
BASIQ_VERSION = os.getenv("BASIQ_VERSION")

def create_basiq_user_object(access_token: str, email: str |None = None, mobile: str |None = None) -> dict:
    if not email and not mobile:
        raise ValueError("At least one of email or mobile must be provided")
    
    url = f"{BASIQ_BASE_URL}/users"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "basiq-version": BASIQ_VERSION,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    payload = {}

    if email: payload["email"] = email
    if mobile: payload["mobile"] = mobile

    res = requests.post(url, json=payload, headers=headers, timeout=60)
    res.raise_for_status()

    return res.json()

def get_user(access_token: str, user_id: str) -> dict:

    if not user_id:
        raise ValueError("user_id must be provided")
    
    url = f"{BASIQ_BASE_URL}/users/{user_id}"
    headers = {
    "accept": "application/json",
    "authorization": f"Bearer {access_token}"
    }

    res = requests.get(url, headers=headers, timeout=60)
    res.raise_for_status()
    return res.json()


def update_basiq_user(access_token: str, user_id: str, email: str | None = None, mobile: str | None = None) -> dict:
    """Update a Basiq user's email and/or mobile"""
    if not user_id:
        raise ValueError("user_id must be provided")
    
    if not email and not mobile:
        raise ValueError("At least one of email or mobile must be provided")
    
    url = f"{BASIQ_BASE_URL}/users/{user_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "basiq-version": BASIQ_VERSION,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    payload = {}
    if email: 
        payload["email"] = email
    if mobile: 
        payload["mobile"] = mobile

    res = requests.patch(url, json=payload, headers=headers, timeout=60)
    res.raise_for_status()
    return res.json()


def delete_basiq_user(access_token: str, user_id: str) -> bool:
    """Delete a Basiq user"""
    if not user_id:
        raise ValueError("user_id must be provided")
    
    url = f"{BASIQ_BASE_URL}/users/{user_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "basiq-version": BASIQ_VERSION,
        "Accept": "application/json",
    }

    res = requests.delete(url, headers=headers, timeout=60)
    res.raise_for_status()
    return res.status_code == 204


def list_basiq_users(access_token: str, limit: int = 100, offset: int = 0) -> dict:
    """List all Basiq users with pagination"""
    url = f"{BASIQ_BASE_URL}/users"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "basiq-version": BASIQ_VERSION,
        "Accept": "application/json",
    }

    params = {
        "limit": limit,
        "offset": offset
    }

    res = requests.get(url, headers=headers, params=params, timeout=60)
    res.raise_for_status()
    return res.json()


if __name__ == "__main__":
    access_token = get_access_token()
    print("Access Token:", access_token)
    email = "s390410@students.cdu.edu.au"

    user = create_basiq_user_object(access_token, email=email)
    print(user)

    get_user_response = get_user(access_token, user["id"])
    print(get_user_response)

    # Try to get accounts and print institution ID
    try:
        from backend.open_banking.job_service import get_accounts
        user_id = user["id"]
        accounts = get_accounts(user_id)
        
        if accounts:
            print("\n=== Accounts ===")
            for account in accounts:
                institution = account.get("institution")
                if isinstance(institution, dict):
                    institution_id = institution.get("id")
                    institution_name = institution.get("name", "Unknown")
                elif institution:
                    institution_id = institution
                    institution_name = "N/A"
                else:
                    institution_id = "N/A"
                    institution_name = "N/A"
                
                print(f"Account: {account.get('name', 'N/A')}")
                print(f"Institution ID: {institution_id}")
                print(f"Institution Name: {institution_name}")
                print("---")
        else:
            print("\nNo accounts found. Connect a bank first.")
    except Exception as e:
        print(f"\nCould not fetch accounts: {e}")