import os
from time import time
import requests
from dotenv import load_dotenv
from backend.utils.logger import logger

# Load environment variables
load_dotenv()
BASIQ_BASE_URL = os.getenv("BASIQ_BASE_URL")
BASIQ_API_KEY = os.getenv("BASIQ_API_KEY")
BASIQ_VERSION = os.getenv("BASIQ_VERSION")
BASIQ_TOKEN_URL = f"{BASIQ_BASE_URL}/token"
ACCESS_TOKEN = None
TOKEN_EXPIRY = 0  # epoch timestamp

if not BASIQ_API_KEY:
    raise ValueError("BASIQ_API_KEY is not set in environment variables")

def get_access_token():
    global ACCESS_TOKEN, TOKEN_EXPIRY

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
    response = requests.post(BASIQ_TOKEN_URL, headers=headers)
    response.raise_for_status()

    data = response.json()

    ACCESS_TOKEN = data["access_token"]

    # token valid for 60 min → refresh at 55 min
    TOKEN_EXPIRY = current_time + (55 * 60)
    logger.info("New Basiq access token acquired (expires at %s)", TOKEN_EXPIRY)

    return ACCESS_TOKEN


def get_client_access_token(user_id: str):
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
    response = requests.post(BASIQ_TOKEN_URL, headers=headers, data=data)
    response.raise_for_status()

    return response.json()["access_token"]

def create_auth_link(user_id, mobile: str | None = None):
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