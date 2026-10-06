"""Optional channels (SPEC 11.2, 11.3): a Teams webhook and an SMTP e-mail, used only when
configured in .env and asked for explicitly. Nothing is sent by default; the outcome is
returned for the audit trail."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def send_teams(
    message: dict[str, Any], webhook_url: str | None, *, timeout: float = 10.0
) -> dict[str, Any]:
    if not webhook_url:
        return {"channel": "teams", "sent": False, "detail": "TEAMS_WEBHOOK_URL not configured"}
    import httpx

    try:
        response = httpx.post(webhook_url, json=message, timeout=timeout)
    except httpx.HTTPError as exc:
        return {"channel": "teams", "sent": False, "detail": f"request failed: {exc}"}
    return {
        "channel": "teams",
        "sent": response.status_code < 300,
        "detail": f"HTTP {response.status_code}",
    }


def send_email(subject: str, html_body: str, env: Mapping[str, str]) -> dict[str, Any]:
    host, sender, to = env.get("SMTP_HOST"), env.get("SMTP_FROM"), env.get("ALERT_EMAIL_TO")
    if not (host and sender and to):
        return {
            "channel": "email",
            "sent": False,
            "detail": "SMTP_HOST, SMTP_FROM or ALERT_EMAIL_TO not configured",
        }
    import smtplib
    from email.message import EmailMessage

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to
    message.set_content("This alert is rendered in HTML.")
    message.add_alternative(html_body, subtype="html")
    try:
        with smtplib.SMTP(host, int(env.get("SMTP_PORT") or 587), timeout=20) as smtp:
            smtp.starttls()
            if env.get("SMTP_USER"):
                smtp.login(env["SMTP_USER"], env.get("SMTP_PASSWORD") or "")
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        return {"channel": "email", "sent": False, "detail": f"send failed: {exc}"}
    return {"channel": "email", "sent": True, "detail": f"sent to {to}"}
