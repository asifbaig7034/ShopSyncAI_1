"""Read-only Meta ownership check for one WhatsApp Business phone number.

This proves the supplied token can list a matching number on the WABA. It
does not prove permission to send messages, webhook delivery, or registration.
"""

from __future__ import annotations

import httpx


MAX_PAGES = 5


class MetaVerificationError(Exception):
    """A safe provider failure category; never include provider error bodies."""


class MetaAccessDenied(MetaVerificationError):
    """Meta refused access with the submitted token or WABA ID."""


class PhoneNumberNotFound(MetaVerificationError):
    """The requested phone number was not found under this WABA."""


class MetaUnavailable(MetaVerificationError):
    """No trustworthy Meta result was obtained."""


def _meta_client() -> httpx.Client:
    # No redirect or inherited proxy may receive an owner's bearer token.
    return httpx.Client(timeout=httpx.Timeout(8.0, connect=3.0),
                        follow_redirects=False, trust_env=False)


def verify_phone_number(*, version: str, waba_id: str, phone_number_id: str,
                        access_token: str) -> tuple[str | None, str | None]:
    """Query ONLY graph.facebook.com; use cursor, never a provider-supplied URL."""
    url = f"https://graph.facebook.com/{version}/{waba_id}/phone_numbers"
    cursor = None
    seen_cursors: set[str] = set()
    try:
        with _meta_client() as client:
            for _ in range(MAX_PAGES):
                params = {"fields": "id,display_phone_number,verified_name", "limit": "100"}
                if cursor:
                    params["after"] = cursor
                response = client.get(url, params=params,
                                      headers={"Authorization": f"Bearer {access_token}"})
                if response.status_code in (401, 403):
                    raise MetaAccessDenied()
                if response.status_code != 200:
                    raise MetaUnavailable()
                try:
                    result = response.json()
                except ValueError as exc:
                    raise MetaUnavailable() from exc
                if not isinstance(result, dict) or not isinstance(result.get("data"), list):
                    raise MetaUnavailable()
                for number in result["data"]:
                    if isinstance(number, dict) and str(number.get("id")) == phone_number_id:
                        display = number.get("display_phone_number")
                        name = number.get("verified_name")
                        return (display[:100] if isinstance(display, str) else None,
                                name[:100] if isinstance(name, str) else None)
                paging = result.get("paging")
                if not isinstance(paging, dict) or not paging.get("next"):
                    raise PhoneNumberNotFound()
                cursors = paging.get("cursors")
                next_cursor = cursors.get("after") if isinstance(cursors, dict) else None
                if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
                    raise MetaUnavailable()
                seen_cursors.add(next_cursor)
                cursor = next_cursor
    except httpx.RequestError as exc:
        raise MetaUnavailable() from exc
    # Refuse to claim a mismatch if there may be additional unexamined pages.
    raise MetaUnavailable()
