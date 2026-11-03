"""Explicit message reading and metadata refresh buttons."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .entity import LibrusEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([RefreshButton(entry.runtime_data), ReadLatestButton(entry.runtime_data)])


class ReadLatestButton(LibrusEntity, ButtonEntity):
    """Pressing this button fetches content and marks the latest message read."""

    _attr_icon = "mdi:email-open-outline"

    def __init__(self, coordinator):
        super().__init__(coordinator, "read_latest_message")

    @property
    def available(self):
        return super().available and self.coordinator.data.latest is not None

    async def async_press(self) -> None:
        await self.coordinator.async_read_latest_message()


class RefreshButton(LibrusEntity, ButtonEntity):
    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator):
        super().__init__(coordinator, "refresh")

    @property
    def available(self):
        return True

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
