"""New-message occurrences, emitted only after the event entity and HA are ready."""

from homeassistant.components.event import EventEntity
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, callback

from .const import EVENT_NEW_MESSAGE, PRIVATE_ATTRIBUTES
from .entity import LibrusEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([NewMessageEvent(entry.runtime_data)])


class NewMessageEvent(LibrusEntity, EventEntity):
    _attr_event_types = [EVENT_NEW_MESSAGE]
    _attr_icon = "mdi:email-plus"
    _unrecorded_attributes = PRIVATE_ATTRIBUTES

    def __init__(self, coordinator):
        super().__init__(coordinator, "new_message")
        self._unsub_start = None

    @property
    def available(self):
        # An event describes the last occurrence, not connectivity. Avoid a
        # transition from unavailable replaying restored message attributes.
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.coordinator.publish = self._publish
        self.async_on_remove(self._stop)
        if self.hass.state is CoreState.running:
            self._start()
        else:
            self._unsub_start = self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._start
            )

    @callback
    def _start(self, _event=None) -> None:
        self._unsub_start = None
        self.coordinator.events_ready = True
        self.coordinator.entry.async_create_background_task(
            self.hass, self.coordinator.async_request_refresh(), "Librus startup refresh"
        )

    @callback
    def _stop(self) -> None:
        if self._unsub_start is not None:
            self._unsub_start()
            self._unsub_start = None
        self.coordinator.events_ready = False
        self.coordinator.publish = None

    @callback
    def _publish(self, message) -> None:
        self._trigger_event(EVENT_NEW_MESSAGE, message.attributes())
        self.async_write_ha_state()
