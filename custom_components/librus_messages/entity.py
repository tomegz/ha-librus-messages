"""Common entity identity without exposing account credentials."""

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LibrusCoordinator


class LibrusEntity(CoordinatorEntity[LibrusCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: LibrusCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.entry.title,
            manufacturer="Librus",
            model="Messages",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://wiadomosci.librus.pl/nowy/inbox",
        )
