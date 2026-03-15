import requests

from .auth import get_access_token

BASE_URL = "https://au-api.basiq.io"


def get_institutions(search: str | None = None):
    token = get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    res = requests.get(f"{BASE_URL}/institutions", headers=headers, timeout=60)
    res.raise_for_status()

    institutions = res.json().get("data", [])

    filtered = []
    for institution in institutions:
        authorization = institution.get("authorization")
        stage = institution.get("stage")
        status = institution.get("status")

        if authorization != "user":
            continue
        if stage == "alpha":
            continue
        if status == "major-outage":
            continue

        filtered.append(institution)

    filtered.sort(key=lambda item: item.get("tier", 0))

    if search:
        search_value = search.lower().strip()
        filtered = [
            item
            for item in filtered
            if search_value in (item.get("name", "").lower())
            or search_value in (item.get("shortName", "").lower())
        ]

    return filtered
