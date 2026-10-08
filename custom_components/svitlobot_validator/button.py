"""Button that posts a test message to the target chat."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SvitlobotValidatorConfigEntry
from .entity import ValidatorEntity

TEST_MESSAGE = "🧪 Тестове повідомлення від Svitlobot Validator"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SvitlobotValidatorConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([TestMessageButton(entry)])


class TestMessageButton(ValidatorEntity, ButtonEntity):
    """Check that the bot can post to the target chat."""

    _attr_icon = "mdi:send-check"

    def __init__(self, entry: SvitlobotValidatorConfigEntry) -> None:
        super().__init__(entry, "test_message")
        self._entry = entry

    async def async_press(self) -> None:
        data = self._entry.runtime_data
        if not await data.bot.send_message(data.target_chat, TEST_MESSAGE):
            raise HomeAssistantError("Telegram did not accept the message, see the log")
