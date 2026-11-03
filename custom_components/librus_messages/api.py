"""Small asynchronous client for inbox metadata, with no detail endpoint."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import aiohttp

AUTH = "https://api.librus.pl"
SYNERGIA = "https://synergia.librus.pl"
MESSAGES = "https://wiadomosci.librus.pl"
WARSAW = ZoneInfo("Europe/Warsaw")
TIMEOUT = aiohttp.ClientTimeout(total=40, connect=10, sock_read=25)
MAX_RESPONSE = 4_000_000
MAX_PAGES = 1000


class ApiError(Exception):
    """Safe, fixed error category; never includes provider response data."""


class AuthError(ApiError):
    """Credentials were explicitly rejected."""


class SessionExpired(ApiError):
    """The authenticated web session must be renewed."""


class RateLimited(ApiError):
    """Librus requested slower polling."""

    def __init__(self, retry_after: int = 300) -> None:
        super().__init__("rate_limited")
        self.retry_after = retry_after


@dataclass(frozen=True, repr=False)
class Message:
    """Metadata only. Deliberately omit a repr containing school messages."""

    message_id: str
    sender: str
    subject: str
    preview: str
    sent_at: datetime
    read: bool

    @property
    def url(self) -> str:
        return f"{MESSAGES}/nowy/inbox/{self.message_id}"

    def attributes(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "sender": self.sender,
            "subject": self.subject,
            "preview": self.preview,
            "sent_at": self.sent_at.isoformat(),
            "url": self.url,
            "read": self.read,
        }


@dataclass(frozen=True, repr=False)
class Inbox:
    messages: tuple[Message, ...]
    unread: int

    @property
    def latest(self) -> Message | None:
        return max(self.messages, key=message_order, default=None)


def message_order(message: Message) -> tuple[datetime, int]:
    return message.sent_at, int(message.message_id)


def validate_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname
            in {
                "api.librus.pl",
                "synergia.librus.pl",
                "wiadomosci.librus.pl",
                "portal.librus.pl",
                "rodzina.librus.pl",
            }
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        valid = False
    if not valid:
        raise ApiError("unexpected_destination")


def parse_message(row: Any) -> Message:
    try:
        message_id = row["messageId"]
        if not isinstance(message_id, str) or not re.fullmatch(r"[0-9]{1,30}", message_id):
            raise ValueError
        sent_at = datetime.fromisoformat(row["sendDate"])
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=WARSAW)
        encoded = row["content"]
        if not isinstance(encoded, str) or len(encoded) > 100_000:
            raise ValueError
        preview = base64.b64decode(encoded, validate=True).decode("utf-8")
        sender = row["senderName"]
        subject = row["topic"]
        read_date = row["readDate"]
        if not isinstance(sender, str) or not isinstance(subject, str):
            raise ValueError
        if read_date is not None and not isinstance(read_date, str):
            raise ValueError
        return Message(
            message_id, sender, subject, preview, sent_at.astimezone(UTC), bool(read_date)
        )
    except KeyError, TypeError, ValueError, binascii.Error:
        raise ApiError("invalid_message_metadata") from None


@dataclass(repr=False)
class Response:
    status: int
    location: str
    body: bytes

    def json(self) -> dict[str, Any]:
        try:
            value = json.loads(self.body)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except ValueError, UnicodeError:
            raise ApiError("invalid_json_response") from None


class LibrusClient:
    """Own one isolated session per account; caller owns session lifetime."""

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self.session = session
        self._username = username
        self._password = password
        self._authenticated = False
        self._lock = asyncio.Lock()

    async def _request(
        self, method: str, url: str, *, follow: bool = False, **kwargs: Any
    ) -> Response:
        for _ in range(10):
            validate_url(url)
            try:
                async with self.session.request(
                    method, url, allow_redirects=False, timeout=TIMEOUT, **kwargs
                ) as response:
                    if response.status == 429:
                        delay = response.headers.get("Retry-After", "300")
                        raise RateLimited(
                            min(max(int(delay), 60), 3600) if delay.isdigit() else 300
                        )
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE:
                            raise ApiError("response_too_large")
                    result = Response(
                        response.status, response.headers.get("Location", ""), bytes(body)
                    )
            except aiohttp.ClientError, TimeoutError:
                raise ApiError("connection_failed") from None
            if not follow or result.status not in (301, 302, 303, 307, 308):
                return result
            if method != "GET" or not result.location:
                raise ApiError("unexpected_redirect")
            url = urljoin(url, result.location)
            kwargs.pop("params", None)
        raise ApiError("too_many_redirects")

    async def _handoff(self) -> None:
        bridge = await self._request("GET", SYNERGIA + "/wiadomosci3")
        # An expired Synergia session can return its login page with HTTP 200
        # instead of redirecting. An authenticated handoff always returns 302.
        if bridge.status == 200:
            raise SessionExpired("synergia_session_expired")
        if bridge.status in (401, 302) and not bridge.location.startswith(MESSAGES + "/"):
            raise SessionExpired("synergia_session_expired")
        if bridge.status != 302:
            raise ApiError("handoff_failed")
        validate_url(bridge.location)
        target = urlsplit(bridge.location)
        if target.hostname != "wiadomosci.librus.pl" or not re.match(
            r"^/pobierz\d+/MultiDomainLogon/token/", target.path
        ):
            raise ApiError("unexpected_handoff")
        exchange = await self._request("GET", bridge.location)
        if exchange.status != 302:
            raise ApiError("session_exchange_failed")
        # The exchange already sets the cookie. Its next redirect is unnecessary.
        await self._api("/me", wrapped=False)

    async def _login(self) -> None:
        self.session.cookie_jar.clear()
        initial = await self._request("GET", AUTH, follow=True)
        if initial.status not in (200, 401):
            raise ApiError("login_bootstrap_failed")
        entry = await self._request("GET", SYNERGIA + "/loguj/portalRodzina", follow=True)
        if entry.status != 200:
            raise ApiError("login_entry_failed")
        result = await self._request(
            "POST",
            AUTH + "/OAuth/Authorization?client_id=46",
            data={"action": "login", "login": self._username, "pass": self._password},
        )
        if result.status == 401:
            raise AuthError("invalid_auth")
        if result.status != 200:
            raise ApiError("login_service_failed")
        status = result.json().get("status")
        if status == "error":
            raise AuthError("invalid_auth")
        if status != "ok":
            raise ApiError("unsupported_login_response")
        finish = await self._request(
            "GET", AUTH + "/OAuth/Authorization/2FA?client_id=46", follow=True
        )
        if finish.status != 200:
            raise ApiError("login_completion_failed")
        try:
            await self._handoff()
        except SessionExpired:
            raise AuthError("interactive_login_required") from None
        self._authenticated = True

    async def _api(self, path: str, *, wrapped: bool = True, **params: int) -> dict[str, Any]:
        # Whitelist metadata endpoints: a detail request cannot pass this guard.
        if path not in ("/me", "/inbox/messages", "/inbox/unreadMessagesCount"):
            raise ApiError("unsupported_endpoint")
        response = await self._request("GET", MESSAGES + "/api" + path, params=params)
        if response.status == 401 or response.status in (301, 302, 303, 307, 308):
            raise SessionExpired("messages_session_expired")
        if response.status != 200:
            raise ApiError("messages_service_failed")
        payload = response.json()
        if wrapped and "data" not in payload:
            raise ApiError("unexpected_api_shape")
        return payload

    async def _fetch(self) -> Inbox:
        messages: list[Message] = []
        ids: set[str] = set()
        total: int | None = None
        for page in range(1, MAX_PAGES + 1):
            payload = await self._api("/inbox/messages", page=page, limit=10)
            rows = payload["data"]
            reported = payload.get("total")
            if type(reported) is not int or reported < 0 or not isinstance(rows, list):
                raise ApiError("invalid_inbox_shape")
            if total is None:
                total = reported
            if total != reported:
                raise ApiError("inbox_changed_during_pagination")
            for row in rows:
                message = parse_message(row)
                if message.message_id in ids:
                    raise ApiError("duplicate_id_during_pagination")
                ids.add(message.message_id)
                messages.append(message)
            if len(messages) == total:
                break
            if not rows or len(messages) > total:
                raise ApiError("incomplete_inbox")
        else:
            raise ApiError("pagination_limit_reached")
        counts = await self._api("/inbox/unreadMessagesCount")
        data = counts["data"]
        count = data.get("inbox") if isinstance(data, dict) else None
        if type(count) is not int or count < 0:
            raise ApiError("invalid_unread_count")
        return Inbox(tuple(messages), count)

    async def fetch_inbox(self) -> Inbox:
        """Fetch a complete result, renewing a rejected session at most once."""
        async with self._lock:
            if not self._authenticated:
                await self._login()
            try:
                return await self._fetch()
            except SessionExpired:
                self._authenticated = False
                try:
                    await self._handoff()
                except SessionExpired:
                    await self._login()
                self._authenticated = True
                try:
                    return await self._fetch()
                except SessionExpired:
                    self._authenticated = False
                    raise ApiError("session_renewal_failed") from None
