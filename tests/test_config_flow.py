"""UI onboarding, options and safe password updates."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from custom_components.librus_messages.api import ApiError, AuthError
from custom_components.librus_messages.config_flow import account_key
from custom_components.librus_messages.const import DOMAIN


async def test_setup_and_duplicate_account(hass, client):
    with patch("custom_components.librus_messages.async_setup_entry", new_callable=AsyncMock):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "name": "School",
                "username": "example-login",
                "password": "example-password",
            },
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == "School"
        assert result["result"].unique_id == account_key("example-login")
        second = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
            data={
                "name": "School",
                "username": "example-login",
                "password": "example-password",
            },
        )
        assert second["reason"] == "already_configured"


@pytest.mark.parametrize(
    "error,expected",
    [
        (AuthError("invalid_auth"), "invalid_auth"),
        (ApiError("connection_failed"), "cannot_connect"),
    ],
)
async def test_errors(hass, client, error, expected):
    client.fetch_inbox.side_effect = error
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={
            "name": "School",
            "username": "example-login",
            "password": "example-password",
        },
    )
    assert result["errors"] == {"base": expected}
    assert "example-password" not in str(result)
    client.session.detach.assert_called()


async def test_options(hass, entry, client):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"poll_interval": 10}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["poll_interval"] == 10


@pytest.mark.parametrize("interval", [0, 61, 1.5])
async def test_options_reject_invalid_intervals(hass, entry, client, interval):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"poll_interval": interval}
        )
    assert "poll_interval" not in entry.options


async def test_options_reject_boolean(hass, entry, client):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"poll_interval": True}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_interval"}
    assert "poll_interval" not in entry.options


async def test_reauth_preserves_tracking_marker(hass, entry, client):
    hass.config_entries.async_update_entry(entry, data={**entry.data, "tracking_initialized": True})
    with patch("homeassistant.config_entries.ConfigEntries.async_reload", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"password": "new-example-password"}
        )
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "new-example-password"
    assert entry.data["tracking_initialized"] is True
