"""On-demand refresh using the shared coordinator."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .entity import LibrusEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([RefreshButton(entry.runtime_data)])


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
