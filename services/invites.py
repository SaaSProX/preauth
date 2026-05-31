from urllib.parse import urlencode

from config.settings import settings


LEGACY_DASHBOARD_URLS = {
    "https://mgt.saasprolabs.io",
    "http://mgt.saasprolabs.io",
}
CANONICAL_DASHBOARD_URL = "https://dashboard.saasprolabs.io"


def dashboard_base_url() -> str:
    configured = settings.dashboard_base_url.rstrip("/")
    if configured in LEGACY_DASHBOARD_URLS:
        return CANONICAL_DASHBOARD_URL
    return configured


def build_invite_link(token: str, email: str) -> str:
    query = urlencode({"token": token, "email": email})
    return f"{dashboard_base_url()}/register?{query}"
