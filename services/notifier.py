import json
import ssl
from html import escape
from urllib import error, request

import certifi

from config.settings import settings


RESEND_EMAILS_URL = "https://api.resend.com/emails"


class EmailDeliveryError(RuntimeError):
    pass


def send_email(to: str, subject: str, body: str, html: str | None = None) -> dict:
    if not settings.resend_api_key:
        raise EmailDeliveryError("RESEND_API_KEY is not configured")

    if not settings.resend_from_email:
        raise EmailDeliveryError("RESEND_FROM_EMAIL is not configured")

    payload = {
        "from": settings.resend_from_email,
        "to": [to],
        "subject": subject,
        "text": body,
    }

    if html:
        payload["html"] = html

    resend_request = request.Request(
        RESEND_EMAILS_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Content-Type": "application/json",
            "User-Agent": "saaspro-lab-api-management/1.0",
        },
        method="POST",
    )
    ssl_context = ssl.create_default_context(cafile=certifi.where())

    try:
        with request.urlopen(resend_request, timeout=10, context=ssl_context) as response:
            response_body = response.read().decode("utf-8")
            return json.loads(response_body) if response_body else {}
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8")
        raise EmailDeliveryError(f"Resend returned {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise EmailDeliveryError(f"Resend request failed: {exc.reason}") from exc


def send_invite_email(to: str, invite_link: str, org_name: str, inviter_name: str | None = None) -> dict:
    escaped_org = escape(org_name)
    escaped_inviter = escape(inviter_name) if inviter_name else "An administrator"
    escaped_link = escape(invite_link)

    subject = f"You're invited to {org_name} on Saaspro Dashboard"
    text = (
        f"{inviter_name or 'An administrator'} invited you to join {org_name} "
        "on Saaspro Dashboard.\n\n"
        f"Create your account here:\n{invite_link}\n\n"
        "If you were not expecting this invite, you can ignore this email."
    )
    html = f"""
    <div style="font-family: Arial, sans-serif; color: #172033; line-height: 1.6;">
      <p>{escaped_inviter} invited you to join <strong>{escaped_org}</strong> on Saaspro Dashboard.</p>
      <p>
        <a href="{escaped_link}" style="display: inline-block; padding: 10px 14px; background: #164e63; color: #ffffff; text-decoration: none; border-radius: 6px;">
          Create account
        </a>
      </p>
      <p style="font-size: 13px; color: #596579;">If the button does not work, copy and paste this link into your browser:</p>
      <p style="font-size: 13px; word-break: break-all;"><a href="{escaped_link}">{escaped_link}</a></p>
      <p style="font-size: 13px; color: #596579;">If you were not expecting this invite, you can ignore this email.</p>
    </div>
    """

    return send_email(to=to, subject=subject, body=text, html=html)
