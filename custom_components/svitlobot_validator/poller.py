"""Long-poll the bot's updates and hand posts from the source channel to the validator."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
from typing import Any

from .storage import Persisted
from .telegram import TelegramAuthError, TelegramBot

_LOGGER = logging.getLogger(__name__)

BACKOFF_START = 5
BACKOFF_MAX = 60
AUTH_RETRY = 300


class Poller:
    """getUpdates loop; the offset is persisted so posts aren't checked twice."""

    def __init__(
        self,
        bot: TelegramBot,
        persisted: Persisted,
        source_chat: int,
        on_post: Callable[[dict[str, Any]], Awaitable[Any]],
    ) -> None:
        self._bot = bot
        self._persisted = persisted
        self._source_chat = source_chat
        self._on_post = on_post

    async def run(self) -> None:
        backoff = BACKOFF_START
        while True:
            try:
                updates = await self._bot.get_updates(self._persisted.data.get("offset"))
            except TelegramAuthError as err:
                _LOGGER.error("Telegram rejected the bot token: %s", err)
                await asyncio.sleep(AUTH_RETRY)
                continue
            except Exception as err:  # noqa: BLE001 - keep polling whatever happens
                _LOGGER.warning("Telegram getUpdates failed, retrying in %s s: %s", backoff, err)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)
                continue
            backoff = BACKOFF_START
            for update in updates:
                self._persisted.data["offset"] = update["update_id"] + 1
                post = update.get("channel_post")
                if not post or post.get("chat", {}).get("id") != self._source_chat:
                    continue
                try:
                    await self._on_post(post)
                except Exception:
                    _LOGGER.exception("Failed to check post %s", post.get("message_id"))
            if updates:
                self._persisted.async_schedule_save()
