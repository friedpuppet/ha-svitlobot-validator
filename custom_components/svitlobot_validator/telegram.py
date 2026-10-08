"""Minimal Telegram Bot API client."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/{method}"
POLL_TIMEOUT = 50
SEND_RETRY_DELAYS = (2, 5)


class TelegramError(Exception):
    """The Bot API call failed."""


class TelegramAuthError(TelegramError):
    """The bot token was rejected."""


class TelegramBot:
    """The few Bot API methods this integration needs."""

    def __init__(self, session: aiohttp.ClientSession, token: str) -> None:
        self._session = session
        self._token = token

    async def _call(self, method: str, timeout: float = 15, **params: Any) -> Any:
        url = API_URL.format(token=self._token, method=method)
        try:
            async with self._session.post(
                url, json=params, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                body = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise TelegramError(f"{method}: {err!r}") from err
        if not body.get("ok"):
            if body.get("error_code") in (401, 404):  # 404: malformed token
                raise TelegramAuthError(f"{method}: {body.get('description')}")
            raise TelegramError(f"{method}: {body.get('error_code')} {body.get('description')}")
        return body["result"]

    async def get_me(self) -> dict[str, Any]:
        return await self._call("getMe")

    async def get_chat(self, chat_id: int | str) -> dict[str, Any]:
        return await self._call("getChat", chat_id=chat_id)

    async def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": POLL_TIMEOUT, "allowed_updates": ["channel_post"]}
        if offset is not None:
            params["offset"] = offset
        return await self._call("getUpdates", timeout=POLL_TIMEOUT + 15, **params)

    async def send_message(self, chat_id: int | str, text: str) -> bool:
        """Send plain text, retrying a couple of times; never raises."""
        for attempt, delay in enumerate((*SEND_RETRY_DELAYS, None)):
            try:
                await self._call(
                    "sendMessage",
                    chat_id=chat_id,
                    text=text,
                    link_preview_options={"is_disabled": True},
                )
            except TelegramError as err:
                _LOGGER.warning("Telegram send failed (attempt %s): %s", attempt + 1, err)
                if delay is None:
                    return False
                await asyncio.sleep(delay)
            else:
                return True
        return False
