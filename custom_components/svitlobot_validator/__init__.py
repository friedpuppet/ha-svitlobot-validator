"""Svitlobot Validator: check Svitlobot's outage posts against local sensors and report to Telegram."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_TEXT,
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
    MAX_POST_AGE_SECONDS,
    SERVICE_CHECK,
)
from .poller import Poller
from .storage import Persisted
from .telegram import TelegramBot
from .validator import Validator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BUTTON, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class RuntimeData:
    bot: TelegramBot
    persisted: Persisted
    validator: Validator
    target_chat: int


type SvitlobotValidatorConfigEntry = ConfigEntry[RuntimeData]


def post_link(post: dict[str, Any]) -> str | None:
    """t.me link to a channel post (public channels by username, private ones by id)."""
    chat, message_id = post.get("chat", {}), post.get("message_id")
    if message_id is None:
        return None
    if username := chat.get("username"):
        return f"https://t.me/{username}/{message_id}"
    chat_id = str(chat.get("id", ""))
    if chat_id.startswith("-100"):
        return f"https://t.me/c/{chat_id[4:]}/{message_id}"
    return None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the ``check`` service (acts on every loaded entry)."""

    async def check(call: ServiceCall) -> ServiceResponse:
        verdicts = []
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            verdicts.append(await entry.runtime_data.validator.async_check(call.data[ATTR_TEXT]))
        return {"verdicts": verdicts}

    hass.services.async_register(
        DOMAIN,
        SERVICE_CHECK,
        check,
        schema=vol.Schema({vol.Required(ATTR_TEXT): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SvitlobotValidatorConfigEntry) -> bool:
    opts = entry.options
    bot = TelegramBot(async_get_clientsession(hass), entry.data[CONF_TOKEN])
    target_chat = int(opts[CONF_TARGET_CHAT])

    persisted = Persisted(hass, entry.entry_id)
    await persisted.async_load()

    async def send(text: str) -> bool:
        return await bot.send_message(target_chat, text)

    validator = Validator(
        hass,
        persisted,
        grid_entity=opts[CONF_GRID_ENTITY],
        voltage_entity=opts[CONF_VOLTAGE_ENTITY],
        voltage_min=opts.get(CONF_VOLTAGE_MIN, DEFAULT_VOLTAGE_MIN),
        voltage_max=opts.get(CONF_VOLTAGE_MAX, DEFAULT_VOLTAGE_MAX),
        fresh_seconds=opts.get(CONF_FRESH_SECONDS, DEFAULT_FRESH_SECONDS),
        send=send,
    )

    async def on_post(post: dict[str, Any]) -> None:
        age = dt_util.utcnow().timestamp() - post.get("date", 0)
        if age > MAX_POST_AGE_SECONDS:
            _LOGGER.debug("Skipping post %s: %.0f s old", post.get("message_id"), age)
            return
        text = post.get("text") or post.get("caption") or ""
        verdict = await validator.async_check(text, post_link(post))
        if verdict:
            _LOGGER.info("Post %s checked: %s", post.get("message_id"), verdict)

    entry.runtime_data = RuntimeData(bot, persisted, validator, target_chat)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    validator.async_start()
    entry.async_on_unload(validator.async_stop)
    poller = Poller(bot, persisted, int(opts[CONF_SOURCE_CHAT]), on_post)
    entry.async_create_background_task(hass, poller.run(), f"{DOMAIN} poller {entry.entry_id}")
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SvitlobotValidatorConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.persisted.async_flush()
    return unloaded


async def _async_entry_updated(hass: HomeAssistant, entry: SvitlobotValidatorConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
