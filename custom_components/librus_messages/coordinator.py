"""Refresh and persist processing state independently of notification delivery."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.file import WriteError
from homeassistant.util.json import SerializationError

from .api import ApiError, AuthError, Inbox, LibrusClient, Message, RateLimited, message_order
from .const import CONF_INITIALIZED, CONF_INTERVAL, DEFAULT_INTERVAL, DOMAIN, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)


class TrackingError(HomeAssistantError):
    """Tracking could not be safely persisted."""


class TrackingStore(Store[dict[str, Any]]):
    """Store with atomic writes and observable failures (HA otherwise logs them)."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        super().__init__(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}", private=True, atomic_writes=True
        )

    async def _async_write_data(self, data: dict) -> None:
        try:
            await super()._async_write_data(data)
        except OSError, WriteError, SerializationError:
            raise TrackingError("tracking_write_failed") from None

    async def _async_migrate_func(self, old_major_version, old_minor_version, old_data):
        # No prior schema exists. Do not discard an unsupported version as corruption.
        raise TrackingError("unsupported_tracking_version")


class LibrusCoordinator(DataUpdateCoordinator[Inbox]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: LibrusClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Librus Messages",
            config_entry=entry,
            update_interval=timedelta(minutes=entry.options.get(CONF_INTERVAL, DEFAULT_INTERVAL)),
        )
        self.entry = entry
        self.client = client
        self.store = TrackingStore(hass, entry.entry_id)
        self.seen: set[str] = set()
        self.initialized = False
        self.last_sync: datetime | None = None
        self.publish: Callable[[Message], None] | None = None
        self.events_ready = False
        self._recovering = False
        self._lock = asyncio.Lock()
        self._rate_limit_until = 0.0
        self.body_message_id: str | None = None
        self.body: str | None = None

    async def async_read_latest_message(self) -> None:
        """Read the current latest message only after an explicit button press."""
        async with self._lock:
            if not self.last_update_success or not self.data or not self.data.latest:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="no_latest_message"
                )
            message_id = self.data.latest.message_id
            if self.body_message_id == message_id:
                return
            if self.hass.loop.time() < self._rate_limit_until:
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="read_rate_limited"
                )
            try:
                body = await self.client.read_message(message_id)
            except AuthError:
                self.entry.async_start_reauth(self.hass)
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="read_auth_failed"
                ) from None
            except RateLimited as err:
                self._rate_limit_until = self.hass.loop.time() + err.retry_after
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="read_rate_limited"
                ) from None
            except ApiError:
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="read_failed"
                ) from None
            self.body_message_id = message_id
            self.body = body
        self.async_update_listeners()
        # The detail response can still say unread. Ask the inbox for the truth.
        await self.async_request_refresh()

    async def _async_setup(self) -> None:
        try:
            stored = await self.store.async_load()
        except KeyError, TypeError, ValueError:
            # Malformed JSON envelopes; JSON decoding corruption itself is
            # handled by HA Store, which preserves a .corrupt copy and returns None.
            stored = None
            self._recovering = True
        except HomeAssistantError, OSError:
            raise UpdateFailed("tracking_load_failed") from None
        if stored is not None:
            try:
                ids = stored["seen_ids"]
                if not isinstance(ids, list) or not all(
                    isinstance(value, str) and re.fullmatch(r"[0-9]{1,30}", value) for value in ids
                ):
                    raise ValueError
                last_sync = datetime.fromisoformat(stored["last_sync"])
                if last_sync.tzinfo is None:
                    raise ValueError
                self.seen = set(ids)
                self.last_sync = last_sync
                self.initialized = True
            except KeyError, TypeError, ValueError:
                self._recovering = True
        elif self.entry.data.get(CONF_INITIALIZED):
            self._recovering = True

    async def _save(self, ids: set[str], timestamp: datetime) -> None:
        try:
            await self.store.async_save(
                {"seen_ids": sorted(ids), "last_sync": timestamp.isoformat()}
            )
        except HomeAssistantError, OSError:
            raise UpdateFailed("tracking_write_failed") from None

    async def _async_update_data(self) -> Inbox:
        async with self._lock:
            if self.hass.loop.time() < self._rate_limit_until:
                raise UpdateFailed("rate_limited")
            try:
                # A finite overall deadline also bounds pagination of a very large inbox.
                async with asyncio.timeout(180):
                    inbox = await self.client.fetch_inbox()
            except AuthError:
                raise ConfigEntryAuthFailed("Librus authentication failed; sign in again") from None
            except RateLimited as err:
                self._rate_limit_until = self.hass.loop.time() + err.retry_after
                raise UpdateFailed("rate_limited", retry_after=err.retry_after) from None
            except ApiError as err:
                raise UpdateFailed(str(err)) from None
            now = dt_util.utcnow()
            if not self.initialized:
                baseline = {message.message_id for message in inbox.messages}
                await self._save(baseline, now)
                self.seen = baseline
                self.initialized = True
                self.hass.config_entries.async_update_entry(
                    self.entry, data={**self.entry.data, CONF_INITIALIZED: True}
                )
                if self._recovering:
                    ir.async_create_issue(
                        self.hass,
                        DOMAIN,
                        f"tracking_reset_{self.entry.entry_id}",
                        is_fixable=False,
                        is_persistent=True,
                        severity=ir.IssueSeverity.WARNING,
                        translation_key="tracking_reset",
                        data={"entry_id": self.entry.entry_id},
                    )
                    self._recovering = False
            else:
                if self.events_ready and self.publish is not None:
                    for message in sorted(inbox.messages, key=message_order):
                        if message.message_id not in self.seen:
                            self.publish(message)
                            # Keep in-memory progress even if saving fails. This avoids
                            # a notification storm on repeated disk failures; a crash
                            # before the save may replay the event as documented.
                            self.seen.add(message.message_id)
                # Before event platform/HA startup is ready, leave unseen IDs unseen.
                await self._save(self.seen, now)
            self.last_sync = now
            latest = inbox.latest
            if latest is None or latest.message_id != self.body_message_id:
                self.body_message_id = None
                self.body = None
            return inbox
