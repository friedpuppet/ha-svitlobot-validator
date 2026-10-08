"""Check Svitlobot's "power is out" posts against our own grid sensor and the meter before the inverter."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
import re
from typing import Any

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    VERDICT_CONFIRMED,
    VERDICT_FALSE_ALARM,
    VERDICT_NONE,
    VERDICT_OUT_OF_RANGE,
)
from .storage import Persisted

# Svitlobot: "🔴 14:12 Світло зникло"
OUTAGE_RE = re.compile(r"Світло\s+зникло", re.IGNORECASE)

STALE_CHECK_INTERVAL = timedelta(seconds=30)


def is_outage_post(text: str) -> bool:
    return bool(OUTAGE_RE.search(text or ""))


def _volts(value: float) -> str:
    return f"{value:.0f} В"


class Validator:
    """Decides what a Svitlobot outage post really means and reports it."""

    def __init__(
        self,
        hass: HomeAssistant,
        persisted: Persisted,
        *,
        grid_entity: str,
        voltage_entity: str,
        voltage_min: float,
        voltage_max: float,
        fresh_seconds: float,
        send: Callable[[str], Awaitable[bool]],
    ) -> None:
        self.hass = hass
        self._persisted = persisted
        self._grid_entity = grid_entity
        self._voltage_entity = voltage_entity
        self._vmin = voltage_min
        self._vmax = voltage_max
        self._fresh = fresh_seconds
        self._send = send
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._unsub_stale: CALLBACK_TYPE | None = None
        # Since when our grid sensor says "off", and the meter's voltage range since then.
        self.outage_since: datetime | None = None
        self.voltage_low: float | None = None
        self.voltage_high: float | None = None

    # --- persisted state -------------------------------------------------

    @property
    def brownout(self) -> bool:
        """A brownout was reported and the "back to normal" post is still owed."""
        return bool(self._persisted.data.get("brownout"))

    def _set_brownout(self, value: bool) -> None:
        self._persisted.data["brownout"] = value
        self._persisted.async_schedule_save()
        if value and self._unsub_stale is None:
            self._unsub_stale = async_track_time_interval(self.hass, self._stale_check, STALE_CHECK_INTERVAL)
        elif not value and self._unsub_stale is not None:
            self._unsub_stale()
            self._unsub_stale = None
        self._notify()

    @property
    def last(self) -> dict[str, Any]:
        return self._persisted.data.get("last") or {"verdict": VERDICT_NONE}

    # --- lifecycle --------------------------------------------------------

    @callback
    def async_start(self) -> None:
        grid = self.hass.states.get(self._grid_entity)
        if grid is not None and grid.state == STATE_OFF:
            self._start_outage(grid.last_changed)
        if self.brownout:
            # Grid came back while HA was down: too late for the "back to normal" post.
            self._set_brownout(grid is None or grid.state != STATE_ON)
        self._unsubs.append(
            async_track_state_change_event(self.hass, [self._grid_entity], self._on_grid)
        )
        self._unsubs.append(
            async_track_state_change_event(self.hass, [self._voltage_entity], self._on_voltage)
        )

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._unsub_stale is not None:
            self._unsub_stale()
            self._unsub_stale = None

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    # --- sensors ----------------------------------------------------------

    def meter_voltage(self) -> float | None:
        """The meter's voltage if it is still reporting (it is powered from the grid)."""
        state = self.hass.states.get(self._voltage_entity)
        if state is None:
            return None
        try:
            value = float(state.state)
        except ValueError:
            return None
        age = (dt_util.utcnow() - state.last_reported).total_seconds()
        return value if age <= self._fresh else None

    def _start_outage(self, since: datetime) -> None:
        self.outage_since = since
        self.voltage_low = self.voltage_high = None
        self._track_voltage(self.meter_voltage())

    def _track_voltage(self, value: float | None) -> None:
        if value is None or self.outage_since is None:
            return
        self.voltage_low = value if self.voltage_low is None else min(self.voltage_low, value)
        self.voltage_high = value if self.voltage_high is None else max(self.voltage_high, value)

    @callback
    def _on_grid(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if new is None:
            return
        if new.state == STATE_OFF and (old is None or old.state != STATE_OFF):
            self._start_outage(new.last_changed)
        elif new.state == STATE_ON:
            self.outage_since = None
            self.voltage_low = self.voltage_high = None
            if self.brownout:
                self._set_brownout(False)
                voltage = self.meter_voltage()
                text = "✅ Напруга повернулась у норму"
                text += f": {_volts(voltage)}." if voltage is not None else "."
                text += " Чекайте оновлення від Світлобота."
                self.hass.async_create_task(self._send(text), eager_start=True)

    @callback
    def _on_voltage(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if new is None:
            return
        try:
            self._track_voltage(float(new.state))
        except ValueError:
            return

    @callback
    def _stale_check(self, _now: datetime) -> None:
        """The meter went silent: a real outage now, so no "back to normal" post."""
        if self.meter_voltage() is None:
            self._set_brownout(False)

    # --- the check --------------------------------------------------------

    async def async_check(self, text: str, link: str | None = None) -> str | None:
        """Check one post; return the verdict, or None if it isn't an outage post."""
        if not is_outage_post(text):
            return None

        grid = self.hass.states.get(self._grid_entity)
        voltage = self.meter_voltage()
        lines: list[str] = []
        if grid is not None and grid.state == STATE_ON:
            verdict = VERDICT_FALSE_ALARM
            lines.append("⚠️ Електрика насправді є, повідомлення від Світлобота є, ймовірно, помилковим.")
            if voltage is not None:
                lines.append(f"Напруга: {_volts(voltage)}.")
        elif voltage is not None:
            verdict = VERDICT_OUT_OF_RANGE
            lines.append("⚡ Електрика є, але параметри за межами допустимих.")
            lines.append(self._voltage_detail(voltage))
            if self.outage_since is not None and self.voltage_low is not None:
                since = dt_util.as_local(self.outage_since).strftime("%H:%M")
                lines.append(
                    f"З {since} напруга була від {_volts(self.voltage_low)} до {_volts(self.voltage_high)}."
                )
            self._set_brownout(True)
        else:
            verdict = VERDICT_CONFIRMED

        message = None
        if lines:
            if link:
                lines.append(f"Пост Світлобота: {link}")
            message = "\n".join(lines)

        self._persisted.data["last"] = {
            "verdict": verdict,
            "checked_at": dt_util.utcnow().isoformat(),
            "post_link": link,
            "voltage": voltage,
            "message": message,
        }
        self._persisted.async_schedule_save()
        self._notify()
        if message:
            await self._send(message)
        return verdict

    def _voltage_detail(self, voltage: float) -> str:
        allowed = f"{self._vmin:.0f}–{self._vmax:.0f} В"
        if voltage < self._vmin:
            return f"Напруга {_volts(voltage)} — нижче допустимої ({allowed})."
        if voltage > self._vmax:
            return f"Напруга {_volts(voltage)} — вище допустимої ({allowed})."
        return f"Напруга {_volts(voltage)} у межах {allowed}, але наш датчик мережі її не бачить."
