from urllib.parse import urlencode

from config.settings import settings


def build_invite_link(token: str, email: str) -> str:
    query = urlencode({"token": token, "email": email})
    return f"{settings.dashboard_base_url.rstrip('/')}/register?{query}"
