"""Sending mail.

Stdlib ``smtplib`` and nothing else — the same reasoning as the auth module:
no API keys, no vendor account, no per-message cost, and it works against
whatever SMTP the VPS or the domain's mail provider already offers.

Configure it with:

    LESARIN_SMTP_HOST      required to actually send; without it, see below
    LESARIN_SMTP_PORT      default 587
    LESARIN_SMTP_USER      optional, enables login
    LESARIN_SMTP_PASSWORD  optional
    LESARIN_SMTP_STARTTLS  default true
    LESARIN_MAIL_FROM      default no-reply@<host>
    LESARIN_BASE_URL       default http://localhost:8000, used to build links

With no host configured — local development, or a deployment that hasn't set
mail up yet — messages are logged instead of sent, at WARNING so they're
impossible to miss. That keeps password reset usable on a laptop without
pretending a message went out.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger("lesarin.mail")


def base_url() -> str:
    return os.environ.get("LESARIN_BASE_URL", "http://localhost:8000").rstrip("/")


def is_configured() -> bool:
    return bool(os.environ.get("LESARIN_SMTP_HOST"))


def send(to: str, subject: str, body: str) -> bool:
    """Send one plain-text message. Returns whether it actually went out.

    Never raises: a failure to send must not turn into a 500 for the caller,
    who would learn nothing useful from it anyway.
    """
    host = os.environ.get("LESARIN_SMTP_HOST")
    sender = os.environ.get("LESARIN_MAIL_FROM") or f"no-reply@{host or 'lesarin.local'}"

    if not host:
        logger.warning(
            "SMTP is not configured (LESARIN_SMTP_HOST), so this message was not sent.\n"
            "--- to: %s\n--- subject: %s\n%s",
            to, subject, body,
        )
        return False

    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    port = int(os.environ.get("LESARIN_SMTP_PORT", "587"))
    user = os.environ.get("LESARIN_SMTP_USER")
    password = os.environ.get("LESARIN_SMTP_PASSWORD")
    starttls = os.environ.get("LESARIN_SMTP_STARTTLS", "true").lower() != "false"

    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            if starttls:
                smtp.starttls()
            if user and password:
                smtp.login(user, password)
            smtp.send_message(message)
        logger.info("sent %r to %s", subject, to)
        return True
    except Exception:  # noqa: BLE001 — logged, never surfaced to the caller
        logger.exception("could not send %r to %s", subject, to)
        return False
