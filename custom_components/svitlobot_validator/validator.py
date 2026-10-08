"""Check Svitlobot's "power is out" posts against our own grid sensor and the meter before the inverter."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
import logging
import re
from typing import Any

from homeassistant.components.recorder import get_instance, history
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

_LOGGER = logging.getLogger(__name__)

# Svitlobot: "🔴 14:12 Світло зникло"
OUTAGE_RE = re.compile(r"Світло\s+зникло", re.IGNORECASE)

STALE_CHECK_INTERVAL = timedelta(seconds=30)
# How far back to look for the start of the current outage.
HISTORY_LOOKBACK = timedelta(hours=24)


def is_outage_post(text: str) -> bool:
    return bool(OUTAGE_RE.search(text or ""))


def _volts(value: float) -> str:
    return f"{value:.0f} В"


def _float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def outage_history(
    hass: HomeAssistant, grid_entity: str, voltage_entity: str, now: datetime
) -> tuple[datetime | None, list[float]]:
    """From the recorder: when the grid sensor last went off, and the meter's readings since then.

    Runs in the recorder's executor. Restarts re-write the same "off" state (with a new
    ``last_changed``), so the start is the first "off" after the last "on", not the last row.
    """
    grid = history.state_changes_during_period(
        hass, now - HISTORY_LOOKBACK, now, grid_entity, no_attributes=True
    ).get(grid_entity, [])
    since: datetime | None = None
    for state in grid:
        if state.state == STATE_ON:
            since = None
        elif state.state == STATE_OFF and since is None:
            since = state.last_changed
    if since is None:
        return None, []
    meter = history.state_changes_during_period(
        hass, since, now, voltage_entity, no_attributes=True
    ).get(voltage_entity, [])
    return since, [v for state in meter if (v := _float(state.state)) is not None]


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
        if self.brownout:
            # Grid came back while HA was down: too late for the "back to normal" post.
            self._set_brownout(grid is None or grid.state != STATE_ON)
        self._unsubs.append(
            async_track_state_change_event(self.hass, [self._grid_entity], self._on_grid)
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

    @callback
    def _on_grid(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if new is not None and new.state == STATE_ON and self.brownout:
            self._set_brownout(False)
            voltage = self.meter_voltage()
            text = "✅ Напруга повернулася в норму"
            text += f": {_volts(voltage)}." if voltage is not None else "."
            text += " Світлобот невдовзі оновить статус."
            self.hass.async_create_task(self._send(text), eager_start=True)

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
            grid_line = f"⚠️ Мережа є: {_volts(voltage)}." if voltage is not None else "⚠️ Мережа є."
            lines.append(f"{grid_line} Повідомлення Світлобота, ймовірно, помилкове.")
        elif voltage is not None:
            verdict = VERDICT_OUT_OF_RANGE
            lines.append("⚡ Мережа є, але її параметри поза нормою.")
            lines.append(self._voltage_detail(voltage))
            if range_line := await self._range_line(voltage):
                lines.append(range_line)
            self._set_brownout(True)
        else:
            verdict = VERDICT_CONFIRMED

        message = None
        if lines:
            if link:
                lines.append(f"Джерело: {link}")
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

    async def _range_line(self, voltage: float) -> str | None:
        """ "Від 14:12 напруга коливається в межах 140–177 В." from the recorder's history."""
        try:
            since, values = await get_instance(self.hass).async_add_executor_job(
                outage_history, self.hass, self._grid_entity, self._voltage_entity, dt_util.utcnow()
            )
        except Exception:
            _LOGGER.exception("Could not read the outage history")
            return None
        if since is None:
            return None
        low, high = min(values + [voltage]), max(values + [voltage])
        start = dt_util.as_local(since).strftime("%H:%M")
        if round(low) == round(high):
            return f"Від {start} напруга тримається на рівні {_volts(low)}."
        return f"Від {start} напруга коливається в межах {low:.0f}–{_volts(high)}."

    def _voltage_detail(self, voltage: float) -> str:
        allowed = f"{self._vmin:.0f}–{self._vmax:.0f} В"
        if voltage < self._vmin:
            return f"Напруга {_volts(voltage)} — нижча за допустиму ({allowed})."
        if voltage > self._vmax:
            return f"Напруга {_volts(voltage)} — вища за допустиму ({allowed})."
        return f"Напруга {_volts(voltage)} — у допустимих межах ({allowed}), але наш датчик мережі її не фіксує."
