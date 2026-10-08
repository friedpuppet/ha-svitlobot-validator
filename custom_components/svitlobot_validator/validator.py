"""Check Svitlobot's posts against the voltage meter before the inverter."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
import logging
import re
from typing import Any

from homeassistant.components.recorder import get_instance, history
from homeassistant.const import STATE_ON
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    VERDICT_BACK_TO_NORMAL,
    VERDICT_CONFIRMED,
    VERDICT_FALSE_ALARM,
    VERDICT_NONE,
    VERDICT_OUT_OF_RANGE,
)
from .storage import Persisted

_LOGGER = logging.getLogger(__name__)

# Svitlobot: "🔴 14:12 Світло зникло" / "🟢 14:33 Світло з'явилося"
OUTAGE_RE = re.compile(r"Світло\s+зникло", re.IGNORECASE)
RESTORED_RE = re.compile(r"Світло\s+з\W?явилося", re.IGNORECASE)

STALE_CHECK_INTERVAL = timedelta(seconds=30)
# How far back to look for the start of the current brownout.
HISTORY_LOOKBACK = timedelta(hours=24)

type Reading = tuple[datetime, float | None]  # None: the meter was unavailable


def is_outage_post(text: str) -> bool:
    return bool(OUTAGE_RE.search(text or ""))


def is_restored_post(text: str) -> bool:
    return bool(RESTORED_RE.search(text or ""))


def _volts(value: float) -> str:
    return f"{value:.0f} В"


def _hhmm(when: datetime) -> str:
    return dt_util.as_local(when).strftime("%H:%M")


def _float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def meter_history(hass: HomeAssistant, entity_id: str, start: datetime, end: datetime) -> list[Reading]:
    """The meter's readings from the recorder (runs in the recorder's executor)."""
    states = history.state_changes_during_period(hass, start, end, entity_id, no_attributes=True)
    return [(state.last_changed, _float(state.state)) for state in states.get(entity_id, [])]


def _span(values: list[float]) -> str:
    """ "140–169 В", or "145 В" when it didn't move."""
    low, high = min(values), max(values)
    return _volts(low) if round(low) == round(high) else f"{low:.0f}–{_volts(high)}"


def _range_text(values: list[float]) -> str:
    span = _span(values)
    return f"на рівні {span}" if "–" not in span else f"в межах {span}"


class Validator:
    """Decides what a Svitlobot post really means and reports it.

    The meter before the inverter shows what the building gets. Our own grid sensor is the
    inverter's view and is deliberately not used: an inverter problem isn't a grid problem.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        persisted: Persisted,
        *,
        voltage_entity: str,
        voltage_min: float,
        voltage_max: float,
        fresh_seconds: float,
        pinger_entity: str | None,
        send: Callable[[str], Awaitable[bool]],
        send_owner: Callable[[str], Awaitable[bool]] | None,
    ) -> None:
        self.hass = hass
        self._persisted = persisted
        self._voltage_entity = voltage_entity
        self._vmin = voltage_min
        self._vmax = voltage_max
        self._fresh = fresh_seconds
        self._pinger_entity = pinger_entity
        self._send = send
        self._send_owner = send_owner
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsub_stale: CALLBACK_TYPE | None = None

    # --- persisted state -------------------------------------------------

    @property
    def brownout_since(self) -> datetime | None:
        """Set while a brownout was reported and Svitlobot hasn't said the power is back."""
        value = self._persisted.data.get("brownout_since")
        return dt_util.parse_datetime(value) if value else None

    @property
    def brownout(self) -> bool:
        return self.brownout_since is not None

    def _set_brownout(self, since: datetime | None) -> None:
        self._persisted.data["brownout_since"] = since.isoformat() if since else None
        self._persisted.async_schedule_save()
        if since and self._unsub_stale is None:
            self._unsub_stale = async_track_time_interval(self.hass, self._stale_check, STALE_CHECK_INTERVAL)
        elif not since and self._unsub_stale is not None:
            self._unsub_stale()
            self._unsub_stale = None
        self._notify()

    @property
    def last(self) -> dict[str, Any]:
        return self._persisted.data.get("last") or {"verdict": VERDICT_NONE}

    # --- lifecycle --------------------------------------------------------

    @callback
    def async_start(self) -> None:
        if self.brownout:
            self._set_brownout(self.brownout_since)  # restart the stale check

    @callback
    def async_stop(self) -> None:
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
        value = _float(state.state)
        if value is None:
            return None
        age = (dt_util.utcnow() - state.last_reported).total_seconds()
        return value if age <= self._fresh else None

    def _in_range(self, value: float) -> bool:
        return self._vmin <= value <= self._vmax

    async def _history(self, start: datetime) -> list[Reading]:
        try:
            return await get_instance(self.hass).async_add_executor_job(
                meter_history, self.hass, self._voltage_entity, start, dt_util.utcnow()
            )
        except Exception:
            _LOGGER.exception("Could not read the meter history")
            return []

    @callback
    def _stale_check(self, _now: datetime) -> None:
        """The meter went silent: a real outage now, so no "back to normal" post."""
        if self.meter_voltage() is None:
            self._set_brownout(None)

    # --- the checks -------------------------------------------------------

    async def async_check(self, text: str, link: str | None = None) -> str | None:
        """Check one Svitlobot post; return the verdict, or None if there is nothing to say."""
        if is_outage_post(text):
            return await self._check_outage(link)
        if is_restored_post(text) and self.brownout:
            return await self._back_to_normal()
        return None

    async def _check_outage(self, link: str | None) -> str:
        voltage = self.meter_voltage()
        lines: list[str] = []
        owner_message = None
        if voltage is None:
            verdict = VERDICT_CONFIRMED
        elif self._in_range(voltage):
            verdict = VERDICT_FALSE_ALARM
            lines.append(f"⚠️ Мережа є: {_volts(voltage)}. Повідомлення Світлобота, ймовірно, помилкове.")
            owner_message = self._owner_message(voltage)
        else:
            verdict = VERDICT_OUT_OF_RANGE
            lines.append("⚡ Мережа є, але її параметри поза нормою.")
            lines.append(self._voltage_detail(voltage))
            since, values = await self._brownout_readings(voltage)
            lines.append(f"Від {_hhmm(since)} напруга коливається {_range_text(values)}.")
            self._set_brownout(since)

        message = "\n".join(lines) if lines else None
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
        if owner_message and self._send_owner:
            await self._send_owner(owner_message)
        return verdict

    async def _brownout_readings(self, voltage: float) -> tuple[datetime, list[float]]:
        """Start of the current out-of-range stretch (first bad reading after the last good one) and its readings."""
        now = dt_util.utcnow()
        since: datetime | None = None
        values: list[float] = []
        for when, value in await self._history(now - HISTORY_LOOKBACK):
            if value is None:
                continue  # an HA restart or a short dropout; a good reading is what ends a stretch
            if self._in_range(value):
                since, values = None, []
            else:
                since = since or when
                values.append(value)
        return since or now, values + [voltage]

    async def _back_to_normal(self) -> str | None:
        """Svitlobot says the power is back after a reported brownout: sum it up."""
        since = self.brownout_since
        self._set_brownout(None)
        voltage = self.meter_voltage()
        if voltage is None or since is None:
            return None  # the meter is silent: it became a real outage meanwhile

        # The stretch ends at the first good reading after the last bad one.
        # A second early: the reading that opened the stretch sits exactly on ``since``.
        history_start = since - timedelta(seconds=1)
        readings = [(when, value) for when, value in await self._history(history_start) if value is not None]
        last_bad = max((i for i, (_, v) in enumerate(readings) if not self._in_range(v)), default=None)
        if last_bad is None:
            bad, end = [], None
        else:
            bad = [v for _, v in readings[: last_bad + 1] if not self._in_range(v)]
            end = readings[last_bad + 1][0] if last_bad + 1 < len(readings) else None

        lines = []
        if self._in_range(voltage):
            lines.append(f"✅ Напруга в нормі: {_volts(voltage)}.")
            if bad:
                period = f"З {_hhmm(since)} до {_hhmm(end)}" if end else f"Від {_hhmm(since)}"
                lines.append(f"{period} напруга була поза нормою: {_span(bad)}.")
        else:
            side = "нижча" if voltage < self._vmin else "вища"
            lines.append(f"⚡ Напруга досі поза нормою: {_volts(voltage)} — {side} за допустиму.")
            lines.append(f"Від {_hhmm(since)} напруга коливається {_range_text(bad + [voltage])}.")
        await self._send("\n".join(lines))
        return VERDICT_BACK_TO_NORMAL

    def _voltage_detail(self, voltage: float) -> str:
        # The real limits are set on an offline voltage relay, so the post doesn't quote ours.
        if voltage < self._vmin:
            return f"Напруга {_volts(voltage)} — нижча за допустиму."
        return f"Напруга {_volts(voltage)} — вища за допустиму."

    def _owner_message(self, voltage: float) -> str:
        """Private note: the grid is fine, so something on our side made Svitlobot report an outage."""
        lines = [
            f"🔧 Світлобот написав «Світло зникло», але лічильник перед інвертором показує {_volts(voltage)}. "
            "Ймовірно, збій у вас."
        ]
        if self._pinger_entity and (pinger := self.hass.states.get(self._pinger_entity)):
            if pinger.state == STATE_ON:
                lines.append("Пінгер на зв'язку — схоже, збій Світлобота.")
            else:
                lines.append(f"Пінгер не відповідає з {_hhmm(pinger.last_changed)}.")
        return "\n".join(lines)
