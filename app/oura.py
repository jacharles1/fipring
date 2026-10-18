"""Minimal client for the Oura Cloud API v2 (OAuth2 authorization-code flow).

Docs: https://cloud.ouraring.com/v2/docs
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from app import config

AUTHORIZE_URL = "https://cloud.ouraring.com/oauth/authorize"
TOKEN_URL = "https://api.ouraring.com/oauth/token"
API_BASE = "https://api.ouraring.com/v2/usercollection"

# Collections queried with start_date/end_date. Each returns {"data": [...], "next_token": ...}
DATE_COLLECTIONS = [
    "daily_activity",
    "daily_readiness",
    "daily_sleep",
    "daily_spo2",
    "daily_stress",
    "daily_resilience",
    "daily_cardiovascular_age",
    "vO2_max",
    "sleep",
    "sleep_time",
    "workout",
    "session",
    "enhanced_tag",
    "rest_mode_period",
]


def authorize_url(state: str) -> str:
    return AUTHORIZE_URL + "?" + urlencode({
        "response_type": "code",
        "client_id": config.OURA_CLIENT_ID,
        "redirect_uri": config.OURA_REDIRECT_URI,
        "scope": config.OURA_SCOPES,
        "state": state,
    })


def _token_request(data: dict) -> dict:
    resp = httpx.post(TOKEN_URL, data={
        **data,
        "client_id": config.OURA_CLIENT_ID,
        "client_secret": config.OURA_CLIENT_SECRET,
    }, timeout=30)
    resp.raise_for_status()
    tok = resp.json()
    tok["expires_at"] = datetime.now(timezone.utc) + timedelta(seconds=tok.get("expires_in", 86400) - 60)
    return tok


def exchange_code(code: str) -> dict:
    return _token_request({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.OURA_REDIRECT_URI,
    })


def refresh(refresh_token: str) -> dict:
    return _token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})


class OuraClient:
    def __init__(self, access_token: str, transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(
            base_url=API_BASE,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=60,
            transport=transport,
        )

    def get(self, path: str, **params) -> dict:
        resp = self.http.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    def paginate(self, collection: str, **params):
        """Yield every document in a collection, following next_token."""
        while True:
            page = self.get(collection, **params)
            yield from page.get("data", [])
            token = page.get("next_token")
            if not token:
                return
            params["next_token"] = token

    def personal_info(self) -> dict:
        return self.get("personal_info")
