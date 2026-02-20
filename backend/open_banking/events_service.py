import requests

from .auth import get_access_token

BASE_URL = "https://au-api.basiq.io"


def list_events(filter_query: str | None = None):
    """
    List events from the last 7 days. Optional filter string per Basiq spec.
    """
    token = get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    params = {}
    if filter_query:
        params["filter"] = filter_query

    res = requests.get(f"{BASE_URL}/events", headers=headers, params=params, timeout=60)
    res.raise_for_status()
    return res.json()


def get_event(event_id: str):
    """
    Retrieve an event by ID.
    """
    if not event_id:
        raise ValueError("event_id must be provided")

    token = get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    res = requests.get(f"{BASE_URL}/events/{event_id}", headers=headers, timeout=60)
    res.raise_for_status()
    return res.json()


def list_event_types():
    """
    List event types.
    """
    token = get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    res = requests.get(f"{BASE_URL}/events/types", headers=headers, timeout=60)
    res.raise_for_status()
    return res.json()


def get_event_type(event_type_id: str):
    """
    Retrieve an event type by ID.
    """
    if not event_type_id:
        raise ValueError("event_type_id must be provided")

    token = get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    res = requests.get(
        f"{BASE_URL}/events/types/{event_type_id}",
        headers=headers,
        timeout=60,
    )
    res.raise_for_status()
    return res.json()
