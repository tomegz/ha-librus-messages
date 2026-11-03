"""Inbox sensors."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory

from .const import PRIVATE_ATTRIBUTES
from .entity import LibrusEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities(
        [
            UnreadSensor(entry.runtime_data),
            LatestSensor(entry.runtime_data),
            LastSyncSensor(entry.runtime_data),
        ]
    )


class UnreadSensor(LibrusEntity, SensorEntity):
    _attr_icon = "mdi:email"

    def __init__(self, coordinator):
        super().__init__(coordinator, "unread_messages")

    @property
    def native_value(self):
        return self.coordinator.data.unread


class LatestSensor(LibrusEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _unrecorded_attributes = PRIVATE_ATTRIBUTES

    def __init__(self, coordinator):
        super().__init__(coordinator, "latest_message")

    @property
    def native_value(self):
        message = self.coordinator.data.latest
        return message.sent_at if message else None

    @property
    def extra_state_attributes(self):
        # Do not expose stale school data on an unavailable entity.
        message = self.coordinator.data.latest if self.available else None
        if message is None:
            return {}
        attributes = message.attributes()
        if self.coordinator.body_message_id == message.message_id:
            attributes["body"] = self.coordinator.body
        return attributes


class LastSyncSensor(LibrusEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator):
        super().__init__(coordinator, "last_successful_sync")

    @property
    def available(self):
        return True

    @property
    def native_value(self):
        return self.coordinator.last_sync
