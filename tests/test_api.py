"""Exercise real HTTP request logic against mocked responses."""

import base64
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.librus_messages.api import (
    AUTH,
    MESSAGES,
    SYNERGIA,
    ApiError,
    AuthError,
    LibrusClient,
    RateLimited,
    parse_message,
    validate_url,
)


@pytest.fixture(autouse=True)
def aiohttp_mock_compatibility():
    """aioresponses 0.7.9 predates aiohttp 3.14's required stream_writer arg."""

    class MockResponse(aiohttp.ClientResponse):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("stream_writer", Mock(output_size=0))
            super().__init__(*args, **kwargs)

    with patch("aioresponses.core.ClientResponse", MockResponse):
        yield


def row(number=1, **overrides):
    return {
        "messageId": str(number),
        "senderName": "Example teacher",
        "topic": "Example subject",
        "content": base64.b64encode("Zażółć\nPrzykład".encode()).decode(),
        "sendDate": "2026-01-01T12:00:00",
        "readDate": None,
        **overrides,
    }


def mock_login(mock):
    mock.get(AUTH, status=401)
    mock.get(SYNERGIA + "/loguj/portalRodzina", status=302, headers={"Location": AUTH + "/form"})
    mock.get(AUTH + "/form", body="form")
    mock.post(AUTH + "/OAuth/Authorization?client_id=46", payload={"status": "ok"})
    mock.get(
        AUTH + "/OAuth/Authorization/2FA?client_id=46",
        status=302,
        headers={"Location": SYNERGIA + "/logged"},
    )
    mock.get(SYNERGIA + "/logged", body="logged in")
    mock_handoff(mock)


def mock_handoff(mock):
    target = MESSAGES + "/pobierz99/MultiDomainLogon/token/synthetic"
    mock.get(SYNERGIA + "/wiadomosci3", status=302, headers={"Location": target})
    mock.get(target, status=302, headers={"Location": "/unused"})
    mock.get(MESSAGES + "/api/me", payload={"syntheticIdentity": True})


def mock_inbox(mock, rows=None, unread=1):
    rows = [row()] if rows is None else rows
    mock.get(
        MESSAGES + "/api/inbox/messages?page=1&limit=10", payload={"data": rows, "total": len(rows)}
    )
    mock.get(MESSAGES + "/api/inbox/unreadMessagesCount", payload={"data": {"inbox": unread}})


def test_decode_preview_and_timezone():
    item = parse_message(row())
    assert item.preview == "Zażółć\nPrzykład"
    assert item.sent_at == datetime(2026, 1, 1, 11, tzinfo=UTC)
    assert not item.read
    assert "Example teacher" not in repr(item)
    assert parse_message(row(sendDate="2026-07-01T12:00:00")).sent_at.hour == 10


@pytest.mark.parametrize(
    "url",
    [
        "http://api.librus.pl",
        "https://api.librus.pl.evil.example",
        "https://user@api.librus.pl",
        "https://api.librus.pl:444",
    ],
)
def test_destination_guard(url):
    with pytest.raises(ApiError, match="unexpected_destination"):
        validate_url(url)


async def test_fresh_login_metadata_only_and_session_reuse():
    async with aiohttp.ClientSession() as session:
        api = LibrusClient(session, "example-login", "example-password")
        with aioresponses() as mock:
            mock_login(mock)
            mock_inbox(mock)
            mock_inbox(mock)
            first = await api.fetch_inbox()
            second = await api.fetch_inbox()
            assert first == second
            assert first.unread == 1
            assert len(first.messages) == 1
            assert (
                sum(len(calls) for (method, _), calls in mock.requests.items() if method == "POST")
                == 1
            )
            assert not any("/messages/" in str(url) for _, url in mock.requests)


async def test_messages_cookie_recovery_does_not_repeat_password_login():
    async with aiohttp.ClientSession() as session:
        api = LibrusClient(session, "example-login", "example-password")
        api._authenticated = True
        with aioresponses() as mock:
            mock.get(MESSAGES + "/api/inbox/messages?page=1&limit=10", status=401)
            mock_handoff(mock)
            mock_inbox(mock)
            assert (await api.fetch_inbox()).unread == 1
            assert all(method == "GET" for method, _ in mock.requests)


@pytest.mark.parametrize("handoff_status", [200, 302, 401])
async def test_complete_session_loss_repeats_login_once(handoff_status):
    async with aiohttp.ClientSession() as session:
        api = LibrusClient(session, "example-login", "example-password")
        api._authenticated = True
        with aioresponses() as mock:
            mock.get(MESSAGES + "/api/inbox/messages?page=1&limit=10", status=401)
            mock.get(
                SYNERGIA + "/wiadomosci3",
                status=handoff_status,
                headers={"Location": AUTH + "/form"} if handoff_status == 302 else {},
                body="<html>Sign in</html>" if handoff_status == 200 else "",
            )
            mock_login(mock)
            mock_inbox(mock)
            assert (await api.fetch_inbox()).unread == 1
            assert (
                sum(len(calls) for (method, _), calls in mock.requests.items() if method == "POST")
                == 1
            )


async def test_pagination_includes_all_rows():
    async with aiohttp.ClientSession() as session:
        api = LibrusClient(session, "example-login", "example-password")
        api._authenticated = True
        with aioresponses() as mock:
            mock.get(
                MESSAGES + "/api/inbox/messages?page=1&limit=10",
                payload={"data": [row(i) for i in range(1, 11)], "total": 11},
            )
            mock.get(
                MESSAGES + "/api/inbox/messages?page=2&limit=10",
                payload={"data": [row(11)], "total": 11},
            )
            mock.get(MESSAGES + "/api/inbox/unreadMessagesCount", payload={"data": {"inbox": 4}})
            result = await api.fetch_inbox()
            assert len(result.messages) == 11
            assert result.unread == 4


@pytest.mark.parametrize(
    "second",
    [{"data": [row(1)], "total": 2}, {"data": [], "total": 2}, {"data": [row(2)], "total": 3}],
)
async def test_inconsistent_pagination_fails(second):
    async with aiohttp.ClientSession() as session:
        api = LibrusClient(session, "example-login", "example-password")
        api._authenticated = True
        with aioresponses() as mock:
            mock.get(
                MESSAGES + "/api/inbox/messages?page=1&limit=10",
                payload={"data": [row(1)], "total": 2},
            )
            mock.get(MESSAGES + "/api/inbox/messages?page=2&limit=10", payload=second)
            with pytest.raises(ApiError):
                await api.fetch_inbox()


async def test_full_content_endpoint_is_forbidden():
    async with aiohttp.ClientSession() as session:
        api = LibrusClient(session, "example-login", "example-password")
        with patch.object(api, "_request", new_callable=AsyncMock) as request:
            with pytest.raises(ApiError, match="unsupported_endpoint"):
                await api._api("/inbox/messages/123")
            request.assert_not_called()


async def test_connection_errors_are_redacted():
    async with aiohttp.ClientSession() as session:
        api = LibrusClient(session, "example-login", "example-password")
        with aioresponses() as mock:
            mock.get(AUTH, exception=aiohttp.ClientConnectionError("synthetic-sensitive-token"))
            with pytest.raises(ApiError, match="^connection_failed$"):
                await api.fetch_inbox()


@pytest.mark.parametrize("status,expected", [(403, ApiError), (429, RateLimited)])
async def test_service_errors_do_not_become_bad_password(status, expected):
    async with aiohttp.ClientSession() as session:
        api = LibrusClient(session, "example-login", "example-password")
        with aioresponses() as mock:
            mock.get(AUTH, status=status)
            with pytest.raises(expected) as caught:
                await api.fetch_inbox()
            assert not isinstance(caught.value, AuthError)
