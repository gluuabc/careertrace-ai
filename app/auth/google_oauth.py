import os
import time
from collections.abc import Mapping
from functools import cache
from typing import Any
from urllib.parse import urlparse

from streamlit.runtime.secrets import secrets_singleton

GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
GOOGLE_METADATA_URL = (
    "https://accounts.google.com/.well-known/openid-configuration"
)


class OAuthConfigurationError(RuntimeError):
    """Raised when required server-side Google OAuth settings are missing."""


class InvalidGoogleIdentity(ValueError):
    """Raised when authenticated claims fail CareerTrace validation."""


@cache
def configure_google_oauth() -> str:
    """Configure Streamlit OIDC from server environment variables."""

    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    cookie_secret = os.getenv("AUTH_COOKIE_SECRET", "").strip()
    redirect_uri = os.getenv(
        "OAUTH_REDIRECT_URI", "http://localhost:8501/oauth2callback"
    ).strip()
    missing = [
        name
        for name, value in (
            ("GOOGLE_CLIENT_ID", client_id),
            ("GOOGLE_CLIENT_SECRET", client_secret),
            ("AUTH_COOKIE_SECRET", cookie_secret),
        )
        if not value
    ]
    if missing:
        raise OAuthConfigurationError(
            "Missing authentication settings: " + ", ".join(missing)
        )
    parsed_redirect = urlparse(redirect_uri)
    is_local_http = (
        parsed_redirect.scheme == "http"
        and parsed_redirect.hostname in {"localhost", "127.0.0.1"}
    )
    if (
        not parsed_redirect.netloc
        or parsed_redirect.path != "/oauth2callback"
        or parsed_redirect.query
        or parsed_redirect.fragment
        or (parsed_redirect.scheme != "https" and not is_local_http)
    ):
        raise OAuthConfigurationError(
            "OAUTH_REDIRECT_URI must be an absolute HTTPS URL ending in "
            "/oauth2callback (HTTP is allowed only for localhost)."
        )
    if len(cookie_secret) < 32:
        raise OAuthConfigurationError(
            "AUTH_COOKIE_SECRET must contain at least 32 characters."
        )

    # Streamlit's native OIDC flow validates authorization state and nonce.
    # Programmatic secrets let deployments keep credentials in environment
    # variables instead of committing a secrets.toml file.
    secrets_singleton.load_if_toml_exists()
    secrets_singleton.merge_programmatic_secrets(
        {
            "auth": {
                "redirect_uri": redirect_uri,
                "cookie_secret": cookie_secret,
                "client_id": client_id,
                "client_secret": client_secret,
                "server_metadata_url": GOOGLE_METADATA_URL,
            }
        }
    )
    return client_id


def validate_google_claims(
    claims: Mapping[str, Any],
    client_id: str,
    *,
    now: int | None = None,
) -> dict[str, str]:
    """Validate the Google identity claims used for database account mapping."""

    current_time = int(time.time()) if now is None else now
    issuer = claims.get("iss")
    subject = claims.get("sub")
    email = claims.get("email")
    name = claims.get("name")
    audience = claims.get("aud")
    authorized_party = claims.get("azp")
    expires_at = claims.get("exp")
    issued_at = claims.get("iat")

    if issuer not in GOOGLE_ISSUERS:
        raise InvalidGoogleIdentity("The identity was not issued by Google.")
    if not isinstance(subject, str) or not subject.strip():
        raise InvalidGoogleIdentity("The Google identity has no subject.")
    if not isinstance(email, str) or not email.strip():
        raise InvalidGoogleIdentity("The Google identity has no email.")
    if claims.get("email_verified") is not True:
        raise InvalidGoogleIdentity("The Google email is not verified.")

    valid_audience = (
        client_id in audience
        if isinstance(audience, list)
        else audience == client_id
    )
    if not valid_audience or (
        authorized_party is not None and authorized_party != client_id
    ):
        raise InvalidGoogleIdentity(
            "The Google identity was issued for another application."
        )
    if not isinstance(expires_at, (int, float)) or expires_at <= current_time:
        raise InvalidGoogleIdentity("The Google identity has expired.")
    if not isinstance(issued_at, (int, float)) or issued_at > current_time + 60:
        raise InvalidGoogleIdentity("The Google identity issue time is invalid.")

    return {
        "google_id": subject.strip(),
        "email": email.strip().casefold(),
        "name": str(name or email).strip(),
        "profile_image": str(claims.get("picture") or "").strip(),
    }
