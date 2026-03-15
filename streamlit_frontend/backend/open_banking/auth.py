import os
from time import time
import requests
from dotenv import load_dotenv
from pathlib import Path
from backend.utils.logger import logger

# Load environment variables from standard .env and HSLedger/basiqenv.
load_dotenv()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / "basiqenv")

BASIQ_BASE_URL = os.getenv("BASIQ_BASE_URL")
BASIQ_API_KEY = os.getenv("BASIQ_API_KEY")
BASIQ_VERSION = os.getenv("BASIQ_VERSION")
ACCESS_TOKEN = None
TOKEN_EXPIRY = 0  # epoch timestamp

def _require_basiq_config():
    missing = []
    if not BASIQ_BASE_URL:
        missing.append("BASIQ_BASE_URL")
    if not BASIQ_API_KEY:
        missing.append("BASIQ_API_KEY")
    if not BASIQ_VERSION:
        missing.append("BASIQ_VERSION")

    if missing:
        raise ValueError(
            "Missing required Basiq environment variables: "
            + ", ".join(missing)
            + ". Set them in environment or HSLedger/basiqenv."
        )

    return f"{BASIQ_BASE_URL}/token"

def get_access_token():
    global ACCESS_TOKEN, TOKEN_EXPIRY
    basiq_token_url = _require_basiq_config()

    current_time = time()

    # Reuse token if still valid (with buffer)
    if ACCESS_TOKEN and current_time < TOKEN_EXPIRY:
        logger.info("Reusing Basiq access token (expires at %s)", TOKEN_EXPIRY)
        return ACCESS_TOKEN
    
    # Otherwise, fetch new token
    headers = {
        "accept": "application/json",
        "basiq-version": BASIQ_VERSION,
        "content-type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {BASIQ_API_KEY}"
    }

    logger.info("Requesting new Basiq access token")
    response = requests.post(basiq_token_url, headers=headers)
    response.raise_for_status()

    data = response.json()

    ACCESS_TOKEN = data["access_token"]

    # token valid for 60 min → refresh at 55 min
    TOKEN_EXPIRY = current_time + (55 * 60)
    logger.info("New Basiq access token acquired (expires at %s)", TOKEN_EXPIRY)

    return ACCESS_TOKEN


def get_client_access_token(user_id: str):
    basiq_token_url = _require_basiq_config()

    if not user_id:
        raise ValueError("user_id must be provided")

    headers = {
        "accept": "application/json",
        "basiq-version": BASIQ_VERSION,
        "content-type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {BASIQ_API_KEY}",
    }

    data = {
        "scope": "CLIENT_ACCESS",
        "userId": user_id,
    }

    logger.info("Requesting Basiq client access token for user %s", user_id)
    response = requests.post(basiq_token_url, headers=headers, data=data)
    response.raise_for_status()

    return response.json()["access_token"]

def create_auth_link(user_id, mobile: str | None = None):
    _require_basiq_config()
    auth_url = f"{BASIQ_BASE_URL}/users/{user_id}/auth_link"
    payload = {"mobile": mobile} if mobile else {}
    
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "authorization": f"Bearer {get_access_token()}"
    }
    
    response = requests.post(auth_url, json=payload, headers=headers)
    response.raise_for_status()
    return response.json()


def get_auth_link(user_id):
    _require_basiq_config()
    auth_url = f"{BASIQ_BASE_URL}/users/{user_id}/auth_link"

    headers = {
        "accept": "application/json",
        "authorization": f"Bearer {get_access_token()}"
    }

    response = requests.get(auth_url, headers=headers)
    response.raise_for_status()
    return response.json()

if __name__ == "__main__":
    token = get_access_token()
    print(TOKEN_EXPIRY)
    print("Access Token:", token)