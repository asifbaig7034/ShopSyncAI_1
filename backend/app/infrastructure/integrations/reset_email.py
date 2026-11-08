"""Deliver owner recovery codes through SMTP, never via API or server logs."""

from __future__ import annotations

import ipaddress
import smtplib
import ssl
from email.message import EmailMessage

from ...core.config import Settings


def smtp_configured(settings: Settings) -> bool:
    """Plain SMTP is permitted only to an explicitly loopback local server."""
    host = settings.smtp_host.strip()
    if not host or (settings.smtp_ssl and settings.smtp_starttls):
        return False
    if bool(settings.smtp_username) != bool(settings.smtp_password):
        return False
    if not settings.smtp_ssl and not settings.smtp_starttls:
        if host.lower() != "localhost":
            try:
                if not ipaddress.ip_address(host).is_loopback:
                    return False
            except ValueError:
                return False
    return True


def _send(settings: Settings, address: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = address
    message["Subject"] = subject
    message.set_content(body)
    context = ssl.create_default_context()
    if settings.smtp_ssl:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=5, context=context) as mailer:
            if settings.smtp_username:
                mailer.login(settings.smtp_username, settings.smtp_password)
            mailer.send_message(message)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=5) as mailer:
            if settings.smtp_starttls:
                mailer.starttls(context=context)
            if settings.smtp_username:
                mailer.login(settings.smtp_username, settings.smtp_password)
            mailer.send_message(message)


def send_password_reset_email(settings: Settings, address: str, code: str) -> None:
    _send(settings, address, "ShopSync AI password recovery", (
        "A password reset was requested for your ShopSync AI owner account.\n\n"
        f"Your one-time code: {code}\n\n"
        "It expires in 15 minutes. If you did not request this, ignore this email.\n"
        "Never share this code with anyone."
    ))


def send_password_changed_email(settings: Settings, address: str) -> None:
    _send(settings, address, "ShopSync AI password changed", (
        "The password for your ShopSync AI owner account has been changed.\n"
        "If you did not make this change, contact the project administrator."
    ))
