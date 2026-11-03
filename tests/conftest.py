"""Synthetic fixtures only. Tests never connect to Librus."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.librus_messages.api import Inbox, Message
from custom_components.librus_messages.const import DOMAIN


def message(number=1, *, read=False):
    return Message(
        str(number),
        "Example teacher",
        f"Example subject {number}",
        "Przykładowa wiadomość\nDrugi wiersz",
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=number),
        read,
    )


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def entry(hass):
    value = MockConfigEntry(
        domain=DOMAIN,
        title="Librus",
        unique_id="synthetic-account",
        data={"username": "example-login", "password": "example-password"},
    )
    value.add_to_hass(hass)
    return value


@pytest.fixture
def client():
    value = Mock()
    value.fetch_inbox = AsyncMock(return_value=Inbox((message(),), 1))
    value.read_message = AsyncMock(return_value="Pełna treść\nDrugi wiersz")
    with (
        patch("custom_components.librus_messages.create_client", return_value=value),
        patch("custom_components.librus_messages.config_flow.create_client", return_value=value),
    ):
        yield value
