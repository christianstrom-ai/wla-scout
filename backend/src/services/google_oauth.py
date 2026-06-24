"""OAuth-flow för WLA-kanal-konto. Återanvänder ytbedomare-projektets klient.
Token sparas som JSON och förnyas automatiskt via refresh_token."""
from __future__ import annotations
import json
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from ..config import settings

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]


def _client_secret_dict() -> dict:
    return {
        "installed": {
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["http://localhost"],
        }
    }


def interactive_login() -> Credentials:
    """Engångskörning per Google-konto: öppnar browser, sparar token."""
    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_ID/SECRET saknas i env")
    flow = InstalledAppFlow.from_client_config(_client_secret_dict(), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    save_credentials(creds)
    return creds


def save_credentials(creds: Credentials) -> None:
    path = Path(settings.google_oauth_token_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json())


def load_credentials() -> Credentials | None:
    path = Path(settings.google_oauth_token_path)
    if not path.exists():
        return None
    creds = Credentials.from_authorized_user_info(json.loads(path.read_text()), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_credentials(creds)
    return creds


def require_credentials() -> Credentials:
    creds = load_credentials()
    if not creds:
        raise RuntimeError(
            "Ingen sparad OAuth-token. Kör `python -m src.cli oauth-init` med "
            "WLA-kanal-ägar-kontot."
        )
    return creds
