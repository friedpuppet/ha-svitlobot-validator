"""The Bot API client against a mocked api.telegram.org."""

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.svitlobot_validator import telegram
from custom_components.svitlobot_validator.telegram import TelegramAuthError, TelegramBot, TelegramError

URL = "https://api.telegram.org/bot123:abc/{}"


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr(telegram, "SEND_RETRY_DELAYS", (0, 0))


async def test_get_updates_params(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(URL.format("getUpdates"), json={"ok": True, "result": [{"update_id": 7}]})
    bot = TelegramBot(async_get_clientsession(hass), "123:abc")
    assert await bot.get_updates(5) == [{"update_id": 7}]
    _, _, body, _ = aioclient_mock.mock_calls[0]
    assert body == {"timeout": 50, "allowed_updates": ["channel_post"], "offset": 5}


async def test_errors(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(URL.format("getMe"), json={"ok": False, "error_code": 401, "description": "Unauthorized"})
    aioclient_mock.post(URL.format("getChat"), json={"ok": False, "error_code": 400, "description": "chat not found"})
    bot = TelegramBot(async_get_clientsession(hass), "123:abc")
    with pytest.raises(TelegramAuthError):
        await bot.get_me()
    with pytest.raises(TelegramError):
        await bot.get_chat(-100)


async def test_send_message_retries(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(URL.format("sendMessage"), json={"ok": False, "error_code": 500, "description": "oops"})
    bot = TelegramBot(async_get_clientsession(hass), "123:abc")
    assert await bot.send_message(-100, "hi") is False
    assert aioclient_mock.call_count == 3
    _, _, body, _ = aioclient_mock.mock_calls[0]
    assert body == {"chat_id": -100, "text": "hi", "link_preview_options": {"is_disabled": True}}
