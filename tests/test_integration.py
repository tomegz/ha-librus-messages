"""Actual HA entities, config entry lifecycle, storage and event detection."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir

from custom_components.librus_messages.api import ApiError, AuthError, Inbox, RateLimited
from custom_components.librus_messages.const import DOMAIN
from custom_components.librus_messages.coordinator import TrackingError

from .conftest import message


async def setup(hass, entry):
    hass.set_state(CoreState.starting)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def start_events(hass):
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()


async def test_first_baseline_entities_and_burst(hass, entry, client, hass_storage):
    coordinator = await setup(hass, entry)
    assert hass.states.get("sensor.librus_unread_messages").state == "1"
    assert hass.states.get("event.librus_new_message").state == "unknown"
    assert coordinator.seen == {"1"}
    events = []
    hass.bus.async_listen(
        "state_changed",
        lambda event: (
            events.append(event.data)
            if event.data["entity_id"] == "event.librus_new_message"
            else None
        ),
    )
    await start_events(hass)
    client.fetch_inbox.return_value = Inbox((message(3, read=True), message(2), message(1)), 2)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    payloads = [
        e["new_state"].attributes
        for e in events
        if e["new_state"] and e["new_state"].attributes.get("message_id")
    ]
    assert [p["message_id"] for p in payloads] == ["2", "3"]
    assert coordinator.seen == {"1", "2", "3"}
    assert hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]["seen_ids"] == ["1", "2", "3"]
    count = len(events)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert len(events) == count
    assert hass.states.get("sensor.librus_latest_message").attributes["message_id"] == "3"
    client.read_message.assert_not_called()


async def test_reload_dedup_and_catchup_before_start(hass, entry, client, hass_storage):
    await setup(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    client.fetch_inbox.return_value = Inbox((message(2, read=True), message(1)), 1)
    coordinator = await setup(hass, entry)
    assert coordinator.seen == {"1"}
    assert hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]["seen_ids"] == ["1"]
    await start_events(hass)
    assert coordinator.seen == {"1", "2"}
    assert hass.states.get("event.librus_new_message").attributes["message_id"] == "2"
    before = hass.states.get("event.librus_new_message").state
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("event.librus_new_message").state == before


async def test_failure_preserves_state_and_last_sync(hass, entry, client, hass_storage):
    coordinator = await setup(hass, entry)
    previous = hass.states.get("sensor.librus_last_successful_sync").state
    stored = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"].copy()
    client.fetch_inbox.side_effect = ApiError("connection_failed")
    await coordinator.async_refresh()
    assert hass.states.get("sensor.librus_unread_messages").state == "unavailable"
    assert hass.states.get("sensor.librus_latest_message").state == "unavailable"
    assert "preview" not in hass.states.get("sensor.librus_latest_message").attributes
    assert hass.states.get("sensor.librus_last_successful_sync").state == previous
    assert hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"] == stored


async def test_archive_and_empty_inbox_do_not_emit_old_messages(hass, entry, client):
    client.fetch_inbox.return_value = Inbox((message(2), message(1)), 2)
    coordinator = await setup(hass, entry)
    await start_events(hass)
    client.fetch_inbox.return_value = Inbox((message(1),), 1)
    await coordinator.async_refresh()
    assert hass.states.get("sensor.librus_latest_message").attributes["message_id"] == "1"
    assert hass.states.get("event.librus_new_message").state == "unknown"
    client.fetch_inbox.return_value = Inbox((), 0)
    await coordinator.async_refresh()
    assert hass.states.get("sensor.librus_latest_message").state == "unknown"
    assert "message_id" not in hass.states.get("sensor.librus_latest_message").attributes
    assert hass.states.get("sensor.librus_unread_messages").state == "0"
    assert coordinator.seen == {"1", "2"}


@pytest.mark.parametrize("stored", [None, {"seen_ids": "broken", "last_sync": "bad"}])
async def test_lost_tracking_rebaselines_with_visible_warning(
    hass, entry, client, hass_storage, stored
):
    hass.config_entries.async_update_entry(entry, data={**entry.data, "tracking_initialized": True})
    if stored is not None:
        hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
            "version": 1,
            "minor_version": 1,
            "data": stored,
        }
    coordinator = await setup(hass, entry)
    await start_events(hass)
    assert coordinator.seen == {"1"}
    assert hass.states.get("event.librus_new_message").state == "unknown"
    assert (DOMAIN, f"tracking_reset_{entry.entry_id}") in ir.async_get(hass).issues


async def test_write_failure_does_not_reannounce_in_memory(hass, entry, client):
    coordinator = await setup(hass, entry)
    await start_events(hass)
    client.fetch_inbox.return_value = Inbox((message(2), message(1)), 2)
    with patch.object(
        coordinator.store, "async_save", new_callable=AsyncMock, side_effect=TrackingError("failed")
    ):
        await coordinator.async_refresh()
        before = hass.states.get("event.librus_new_message").state
        assert coordinator.seen == {"1", "2"}
        await coordinator.async_refresh()
        assert hass.states.get("event.librus_new_message").state == before
    await coordinator.async_refresh()
    assert coordinator.last_update_success


async def test_refresh_button_and_removal(hass, entry, client, hass_storage):
    coordinator = await setup(hass, entry)
    with patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock) as refresh:
        await hass.services.async_call(
            "button", "press", {"entity_id": "button.librus_refresh"}, blocking=True
        )
        refresh.assert_awaited_once()
    await hass.config_entries.async_remove(entry.entry_id)
    assert f"{DOMAIN}.{entry.entry_id}" not in hass_storage
    client.session.detach.assert_called()
    client.read_message.assert_not_called()


@pytest.mark.parametrize("already_read", [False, True])
async def test_read_button_only_reads_latest_and_uses_reported_unread_count(
    hass, entry, client, hass_storage, already_read
):
    client.fetch_inbox.return_value = Inbox(
        (message(2, read=already_read), message(1)), 1 if already_read else 2
    )
    coordinator = await setup(hass, entry)
    client.read_message.assert_not_called()
    client.fetch_inbox.return_value = Inbox((message(2, read=True), message(1)), 1)
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.librus_read_latest_message"}, blocking=True
    )
    client.read_message.assert_awaited_once_with("2")
    latest = hass.states.get("sensor.librus_latest_message")
    assert latest.attributes["body"] == "Pełna treść\nDrugi wiersz"
    assert latest.attributes["read"] is True
    assert hass.states.get("sensor.librus_unread_messages").state == "1"
    assert hass.states.get("event.librus_new_message").state == "unknown"
    assert "body" not in hass.states.get("event.librus_new_message").attributes
    assert "body" in latest.state_info["unrecorded_attributes"]
    assert set(hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]) == {"seen_ids", "last_sync"}
    await coordinator.async_read_latest_message()
    client.read_message.assert_awaited_once()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert "body" not in hass.states.get("sensor.librus_latest_message").attributes
    client.read_message.assert_awaited_once()


async def test_body_is_discarded_when_latest_changes_and_is_not_reused_for_older_message(
    hass, entry, client
):
    coordinator = await setup(hass, entry)
    await coordinator.async_read_latest_message()
    for inbox in (Inbox((message(2), message(1)), 2), Inbox((message(1),), 1), Inbox((), 0)):
        client.fetch_inbox.return_value = inbox
        await coordinator.async_refresh()
        assert "body" not in hass.states.get("sensor.librus_latest_message").attributes
        assert coordinator.body is None
    assert hass.states.get("button.librus_read_latest_message").state == "unavailable"
    client.read_message.assert_awaited_once_with("1")


async def test_concurrent_press_reads_once_and_new_message_never_gets_old_body(hass, entry, client):
    coordinator = await setup(hass, entry)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def read(_):
        entered.set()
        await release.wait()
        return "Body of message one"

    client.read_message.side_effect = read
    with patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock):
        first = asyncio.create_task(coordinator.async_read_latest_message())
        await entered.wait()
        second = asyncio.create_task(coordinator.async_read_latest_message())
        release.set()
        await asyncio.gather(first, second)
    client.read_message.assert_awaited_once_with("1")
    client.fetch_inbox.return_value = Inbox((message(2), message(1)), 1)
    await coordinator.async_refresh()
    latest = hass.states.get("sensor.librus_latest_message")
    assert latest.attributes["message_id"] == "2"
    assert "body" not in latest.attributes


async def test_read_error_does_not_invent_a_body_or_change_unread_count(hass, entry, client):
    coordinator = await setup(hass, entry)
    client.read_message.side_effect = ApiError("invalid_message_body")
    with pytest.raises(HomeAssistantError) as caught:
        await coordinator.async_read_latest_message()
    assert caught.value.translation_key == "read_failed"
    assert "body" not in hass.states.get("sensor.librus_latest_message").attributes
    assert hass.states.get("sensor.librus_unread_messages").state == "1"
    assert coordinator.body is None


async def test_read_rate_limit_blocks_repeated_reads_and_polls(hass, entry, client):
    coordinator = await setup(hass, entry)
    client.read_message.side_effect = RateLimited(300)
    for _ in range(2):
        with pytest.raises(HomeAssistantError) as caught:
            await coordinator.async_read_latest_message()
        assert caught.value.translation_key == "read_rate_limited"
    client.read_message.assert_awaited_once()
    calls = client.fetch_inbox.call_count
    await coordinator.async_refresh()
    assert client.fetch_inbox.call_count == calls


async def test_read_auth_failure_starts_reauth(hass, entry, client):
    coordinator = await setup(hass, entry)
    client.read_message.side_effect = AuthError("invalid_auth")
    with patch.object(type(entry), "async_start_reauth") as reauth:
        with pytest.raises(HomeAssistantError) as caught:
            await coordinator.async_read_latest_message()
        assert caught.value.translation_key == "read_auth_failed"
        reauth.assert_called_once_with(hass)


async def test_failed_refresh_disables_read_button_and_hides_cached_body(hass, entry, client):
    coordinator = await setup(hass, entry)
    await coordinator.async_read_latest_message()
    client.fetch_inbox.side_effect = ApiError("connection_failed")
    await coordinator.async_refresh()
    assert "body" not in hass.states.get("sensor.librus_latest_message").attributes
    assert hass.states.get("button.librus_read_latest_message").state == "unavailable"
    with pytest.raises(ServiceValidationError):
        await coordinator.async_read_latest_message()
    client.read_message.assert_awaited_once()


async def test_polish_read_button_id_matches_dashboard_example(hass, entry, client):
    hass.config.language = "pl"
    await setup(hass, entry)
    assert hass.states.get("button.librus_przeczytaj_najnowsza_wiadomosc") is not None


async def test_restart_while_offline_retains_last_sync(hass, entry, client):
    coordinator = await setup(hass, entry)
    timestamp = coordinator.last_sync
    assert await hass.config_entries.async_unload(entry.entry_id)
    client.fetch_inbox.side_effect = ApiError("connection_failed")
    coordinator = await setup(hass, entry)
    assert coordinator.seen == {"1"}
    assert coordinator.last_sync == timestamp
    assert hass.states.get("sensor.librus_unread_messages").state == "unavailable"
    assert (
        hass.states.get("sensor.librus_last_successful_sync").state
        == timestamp.replace(microsecond=0).isoformat()
    )
    client.fetch_inbox.side_effect = None
    client.fetch_inbox.return_value = Inbox((message(2), message(1)), 2)
    await start_events(hass)
    assert coordinator.seen == {"1", "2"}


async def test_rate_limit_blocks_manual_retries(hass, entry, client):
    coordinator = await setup(hass, entry)
    client.fetch_inbox.side_effect = RateLimited(300)
    await coordinator.async_refresh()
    calls = client.fetch_inbox.call_count
    await coordinator.async_refresh()
    assert client.fetch_inbox.call_count == calls
    assert coordinator.seen == {"1"}


async def test_permission_error_does_not_reset_tracking(hass, entry, client):
    with patch(
        "custom_components.librus_messages.coordinator.TrackingStore.async_load",
        side_effect=PermissionError,
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    client.fetch_inbox.assert_not_called()
    assert not ir.async_get(hass).issues


async def test_future_storage_version_is_preserved(hass, entry, client, hass_storage):
    key = f"{DOMAIN}.{entry.entry_id}"
    stored = {"version": 99, "minor_version": 1, "data": {"future": True}}
    hass_storage[key] = stored.copy()
    assert not await hass.config_entries.async_setup(entry.entry_id)
    client.fetch_inbox.assert_not_called()
    assert hass_storage[key] == stored


async def test_write_error_is_not_swallowed_by_store(hass, entry, client):
    from homeassistant.helpers.update_coordinator import UpdateFailed
    from homeassistant.util.file import WriteError

    coordinator = await setup(hass, entry)
    with patch(
        "homeassistant.helpers.storage.Store._async_write_data", side_effect=WriteError("synthetic")
    ):
        with pytest.raises(UpdateFailed, match="tracking_write_failed"):
            await coordinator._save(coordinator.seen, coordinator.last_sync)


async def test_actual_account_cookie_jars_are_isolated(hass):
    from custom_components.librus_messages import create_client

    first = create_client(hass, "synthetic-first", "synthetic-password")
    second = create_client(hass, "synthetic-second", "synthetic-password")
    try:
        first.session.cookie_jar.update_cookies({"synthetic_cookie": "test"})
        assert len(first.session.cookie_jar) == 1
        assert len(second.session.cookie_jar) == 0
    finally:
        first.session.detach()
        second.session.detach()


async def test_readme_automation_delivers_bursts_without_reload_replay(hass, entry, client):
    import re
    from pathlib import Path

    import yaml
    from homeassistant.core import callback
    from homeassistant.setup import async_setup_component

    coordinator = await setup(hass, entry)
    delivered = []

    @callback
    def notify(call):
        delivered.append(dict(call.data))

    hass.services.async_register("notify", "mobile_app_your_phone", notify)
    readme = await hass.async_add_executor_job(Path("README.md").read_text)
    example = re.search(r"```yaml\n(.*?)\n```", readme, re.S).group(1)
    assert await async_setup_component(
        hass, "automation", {"automation": [yaml.safe_load(example)]}
    )
    await start_events(hass)
    client.fetch_inbox.return_value = Inbox((message(3), message(2), message(1)), 3)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert [call["title"] for call in delivered] == ["Example subject 2", "Example subject 3"]
    assert delivered[0]["data"]["url"] == "/lovelace/0"
    assert delivered[0]["data"]["actions"][1]["uri"].endswith("/2")
    assert delivered[0]["message"].endswith("...")
    client.read_message.assert_not_called()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(delivered) == 2
