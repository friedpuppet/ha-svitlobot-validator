"""Config and options flows."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_FRESH_SECONDS,
    CONF_GRID_ENTITY,
    CONF_SOURCE_CHAT,
    CONF_TARGET_CHAT,
    CONF_TOKEN,
    CONF_VOLTAGE_ENTITY,
    CONF_VOLTAGE_MAX,
    CONF_VOLTAGE_MIN,
    DEFAULT_FRESH_SECONDS,
    DEFAULT_VOLTAGE_MAX,
    DEFAULT_VOLTAGE_MIN,
    DOMAIN,
)
from .telegram import TelegramAuthError, TelegramBot, TelegramError


def _volts() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(min=0, max=500, step=1, unit_of_measurement="V", mode=selector.NumberSelectorMode.BOX)
    )


def _options_schema(defaults: dict[str, Any]) -> dict:
    return {
        vol.Required(CONF_SOURCE_CHAT, default=defaults.get(CONF_SOURCE_CHAT, vol.UNDEFINED)): selector.TextSelector(),
        vol.Required(CONF_TARGET_CHAT, default=defaults.get(CONF_TARGET_CHAT, vol.UNDEFINED)): selector.TextSelector(),
        vol.Required(CONF_GRID_ENTITY, default=defaults.get(CONF_GRID_ENTITY, vol.UNDEFINED)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor")
        ),
        vol.Required(CONF_VOLTAGE_ENTITY, default=defaults.get(CONF_VOLTAGE_ENTITY, vol.UNDEFINED)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.VOLTAGE)
        ),
        vol.Required(CONF_VOLTAGE_MIN, default=defaults.get(CONF_VOLTAGE_MIN, DEFAULT_VOLTAGE_MIN)): _volts(),
        vol.Required(CONF_VOLTAGE_MAX, default=defaults.get(CONF_VOLTAGE_MAX, DEFAULT_VOLTAGE_MAX)): _volts(),
        vol.Required(CONF_FRESH_SECONDS, default=defaults.get(CONF_FRESH_SECONDS, DEFAULT_FRESH_SECONDS)): selector.NumberSelector(
            selector.NumberSelectorConfig(min=10, max=3600, step=1, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX)
        ),
    }


async def _validate(hass: HomeAssistant, token: str, user_input: dict[str, Any], errors: dict[str, str]) -> dict[str, Any]:
    """Check the token and both chats; return the options with chat ids normalised to ints."""
    bot = TelegramBot(async_get_clientsession(hass), token)
    try:
        await bot.get_me()
    except TelegramAuthError:
        errors[CONF_TOKEN] = "invalid_auth"
        return user_input
    except TelegramError:
        errors["base"] = "cannot_connect"
        return user_input

    options = dict(user_input)
    for key in (CONF_SOURCE_CHAT, CONF_TARGET_CHAT):
        try:
            chat = await bot.get_chat(str(user_input[key]).strip())
        except TelegramError:
            errors[key] = "chat_not_found"
        else:
            options[key] = chat["id"]
    if not errors and options[CONF_SOURCE_CHAT] == options[CONF_TARGET_CHAT]:
        errors[CONF_TARGET_CHAT] = "same_chat"
    if options[CONF_VOLTAGE_MIN] >= options[CONF_VOLTAGE_MAX]:
        errors[CONF_VOLTAGE_MAX] = "bad_range"
    return options


class SvitlobotValidatorConfigFlow(ConfigFlow, domain=DOMAIN):
    """One entry per watched Svitlobot channel."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = dict(user_input)
            name = user_input.pop(CONF_NAME)
            token = user_input.pop(CONF_TOKEN).strip()
            options = await _validate(self.hass, token, user_input, errors)
            if not errors:
                await self.async_set_unique_id(str(options[CONF_SOURCE_CHAT]))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=name, data={CONF_TOKEN: token}, options=options)
            user_input = {CONF_NAME: name, **user_input}

        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "Svitlobot")): selector.TextSelector(),
                vol.Required(CONF_TOKEN): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                **_options_schema(defaults),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SvitlobotValidatorOptionsFlow()


class SvitlobotValidatorOptionsFlow(OptionsFlow):
    """Change chats, sensors and limits (the entry reloads afterwards)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            options = await _validate(self.hass, self.config_entry.data[CONF_TOKEN], user_input, errors)
            if not errors:
                return self.async_create_entry(data=options)
        defaults = user_input or {
            k: (str(v) if k in (CONF_SOURCE_CHAT, CONF_TARGET_CHAT) else v)
            for k, v in self.config_entry.options.items()
        }
        return self.async_show_form(step_id="init", data_schema=vol.Schema(_options_schema(defaults)), errors=errors)
