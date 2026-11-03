"""UI configuration, password updates and polling options."""

import asyncio
import hashlib
from typing import Any

import probatio as vol
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from . import create_client
from .api import ApiError, AuthError
from .const import CONF_INTERVAL, DEFAULT_INTERVAL, DOMAIN


def account_key(username: str) -> str:
    return hashlib.sha256(username.encode()).hexdigest()


class LibrusConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(self, username: str, password: str) -> str | None:
        if not username or not password:
            return "invalid_auth"
        client = create_client(self.hass, username, password)
        try:
            async with asyncio.timeout(180):
                await client.fetch_inbox()
        except AuthError:
            return "invalid_auth"
        except ApiError, TimeoutError:
            return "cannot_connect"
        finally:
            client.session.detach()
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            await self.async_set_unique_id(account_key(username))
            self._abort_if_unique_id_configured()
            error = await self._validate(username, user_input[CONF_PASSWORD])
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title=user_input[CONF_NAME].strip() or "Librus",
                    data={CONF_USERNAME: username, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
        return self.async_show_form(
            step_id="user",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default="Librus"): str,
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
        )

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def _password_step(self, step_id, entry, user_input):
        errors = {}
        if user_input is not None:
            error = await self._validate(entry.data[CONF_USERNAME], user_input[CONF_PASSWORD])
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id=step_id,
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
        )

    async def async_step_reauth_confirm(self, user_input=None):
        return await self._password_step("reauth_confirm", self._get_reauth_entry(), user_input)

    async def async_step_reconfigure(self, user_input=None):
        return await self._password_step("reconfigure", self._get_reconfigure_entry(), user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return LibrusOptionsFlow()


class LibrusOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            value = user_input.get(CONF_INTERVAL)
            if type(value) is not int or not 1 <= value <= 60:
                errors["base"] = "invalid_interval"
            else:
                return self.async_create_entry(title="", data={CONF_INTERVAL: value})
        return self.async_show_form(
            step_id="init",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_INTERVAL,
                        default=self.config_entry.options.get(CONF_INTERVAL, DEFAULT_INTERVAL),
                    ): vol.All(int, vol.Range(min=1, max=60)),
                }
            ),
        )
