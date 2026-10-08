"""Fixtures for Svitlobot Validator tests: a fake Telegram bot instead of the real API."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.svitlobot_validator.const import (
    CONF_FRESH_SECONDS,
    CONF_GRID_ENTITY,
    CONF_SOURCE_CHAT,
    CONF_TARGET_CHAT,
    CONF_TOKEN,
    CONF_VOLTAGE_ENTITY,
    CONF_VOLTAGE_MAX,
    CONF_VOLTAGE_MIN,
    DOMAIN,
)
from custom_components.svitlobot_validator.telegram import TelegramAuthError, TelegramError

GRID = "binary_sensor.grid"
VOLTAGE = "sensor.meter_voltage"
SOURCE = -1001111111111
TARGET = -1002222222222
VERDICT = "sensor.svitlobot_last_verdict"
TEST_BUTTON = "button.svitlobot_send_test_message"
GOOD_TOKEN = "123:abc"


class FakeBot:
    """Stands in for TelegramBot; updates are fed by the test."""

    chats = {SOURCE: "svitlobot_test", TARGET: None, "@svitlobot_test": "svitlobot_test"}

    def __init__(self) -> None:
        self.token = GOOD_TOKEN
        self.sent: list[tuple[int, str]] = []
        self.offsets: list[int | None] = []
        self._queue: asyncio.Queue[list[dict[str, Any]]] = asyncio.Queue()
        self._polled = asyncio.Event()

    async def get_me(self) -> dict[str, Any]:
        if self.token != GOOD_TOKEN:
            raise TelegramAuthError("Unauthorized")
        return {"id": 123, "username": "test_bot"}

    async def get_chat(self, chat_id: int | str) -> dict[str, Any]:
        key = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
        if key not in self.chats:
            raise TelegramError("chat not found")
        return {"id": SOURCE if key == "@svitlobot_test" else key, "type": "channel"}

    async def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        self.offsets.append(offset)
        self._polled.set()
        return await self._queue.get()

    async def send_message(self, chat_id: int | str, text: str) -> bool:
        self.sent.append((chat_id, text))
        return True

    async def feed(self, hass: HomeAssistant, updates: list[dict[str, Any]]) -> None:
        """Deliver updates and wait until the poller has handled them and polls again."""
        self._polled.clear()
        await self._queue.put(updates)
        await asyncio.wait_for(self._polled.wait(), 5)
        await hass.async_block_till_done()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """The integration depends on the recorder (outage history)."""
    yield


@pytest.fixture
def bot():
    fake = FakeBot()

    def factory(session, token):
        fake.token = token
        return fake

    with (
        patch("custom_components.svitlobot_validator.TelegramBot", side_effect=factory),
        patch("custom_components.svitlobot_validator.config_flow.TelegramBot", side_effect=factory),
    ):
        yield fake


def make_entry(**options: Any) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Svitlobot",
        unique_id=str(SOURCE),
        data={CONF_TOKEN: GOOD_TOKEN},
        options={
            CONF_SOURCE_CHAT: SOURCE,
            CONF_TARGET_CHAT: TARGET,
            CONF_GRID_ENTITY: GRID,
            CONF_VOLTAGE_ENTITY: VOLTAGE,
            CONF_VOLTAGE_MIN: 170,
            CONF_VOLTAGE_MAX: 280,
            CONF_FRESH_SECONDS: 60,
            **options,
        },
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


_update_id = 1000


def channel_post(text: str, chat_id: int = SOURCE, age: float = 0, message_id: int = 1605) -> dict[str, Any]:
    global _update_id
    _update_id += 1
    return {
        "update_id": _update_id,
        "channel_post": {
            "message_id": message_id,
            "chat": {"id": chat_id, "type": "channel", "username": "svitlobot_test"},
            "date": int(dt_util.utcnow().timestamp() - age),
            "text": text,
        },
    }


OUTAGE = "🔴 14:12 Світло зникло        \n🕓 Воно було 1год 54хв"
RESTORED = "🟢 14:33 Світло з'явилося        \n🕓 Його не було 21хв"
