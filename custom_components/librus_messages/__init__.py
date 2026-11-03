"""Librus Messages integration."""

from aiohttp import CookieJar
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import Inbox, LibrusClient
from .const import DOMAIN
from .coordinator import LibrusCoordinator, TrackingStore

PLATFORMS = [Platform.SENSOR, Platform.EVENT, Platform.BUTTON]
type LibrusConfigEntry = ConfigEntry[LibrusCoordinator]


def create_client(hass: HomeAssistant, username: str, password: str) -> LibrusClient:
    """HA owns the shared connector; this account owns its cookies/session."""
    session = async_create_clientsession(
        hass,
        auto_cleanup=False,
        cookie_jar=CookieJar(),
        headers={"Accept": "application/json, text/plain, */*"},
    )
    return LibrusClient(session, username, password)


async def async_setup_entry(hass: HomeAssistant, entry: LibrusConfigEntry) -> bool:
    client = create_client(hass, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    coordinator = LibrusCoordinator(hass, entry, client)
    try:
        try:
            await coordinator.async_config_entry_first_refresh()
        except ConfigEntryNotReady:
            if not coordinator.initialized:
                raise
            # Keep the saved sync time visible after a restart while offline.
            # Message sensors stay unavailable until a complete fetch succeeds.
            coordinator.data = Inbox((), 0)
        entry.runtime_data = coordinator
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await coordinator.async_shutdown()
        client.session.detach()
        raise
    entry.async_on_unload(client.session.detach)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: LibrusConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: LibrusConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_shutdown()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: LibrusConfigEntry) -> None:
    await TrackingStore(hass, entry.entry_id).async_remove()
    ir.async_delete_issue(hass, DOMAIN, f"tracking_reset_{entry.entry_id}")
