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
# The time Svitlobot gives for the outage: "🔴 09:09 Світло зникло".
POST_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s+Світло", re.IGNORECASE)
# The grid has been back and fine for this long, but Svitlobot hasn't said so: we do (it posts ~4–5 min late).
RESTORE_WAIT = timedelta(minutes=10)
# Svitlobot posts a few minutes late; a reported time further back than this is not trusted.
POST_TIME_MAX_AGE = timedelta(minutes=30)

WATCH_INTERVAL = timedelta(seconds=30)
# How far back to look for the start of the current brownout.
HISTORY_LOOKBACK = timedelta(hours=24)

type Reading = tuple[datetime, float | None]  # None: the meter was unavailable


def is_outage_post(text: str) -> bool:
    return bool(OUTAGE_RE.search(text or ""))


def is_restored_post(text: str) -> bool:
    return bool(RESTORED_RE.search(text or ""))


def post_time(text: str, now: datetime) -> datetime | None:
    """The outage time from a Svitlobot post (local HH:MM, today or just before midnight), as UTC."""
    if not (match := POST_TIME_RE.search(text or "")):
        return None
    hour, minute = int(match[1]), int(match[2])
    if hour > 23 or minute > 59:
        return None
    local_now = dt_util.as_local(now)
    when = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if when > local_now + timedelta(minutes=1):
        when -= timedelta(days=1)
    when = dt_util.as_utc(when)
    return when if now - when <= POST_TIME_MAX_AGE else None


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
        grid_entity: str | None = None,
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
        self._grid_entity = grid_entity
        self._send = send
        self._send_owner = send_owner
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsub_watch: CALLBACK_TYPE | None = None
        self._started = dt_util.utcnow()

    # --- persisted state -------------------------------------------------

    # The watch after a post that says "the grid is there", until Svitlobot says the power is back:
    #   alive:    a false alarm was reported, the voltage was fine (``alive_since``);
    #   brownout: the meter is alive but out of range (``brownout_since``);
    #   outage:   either of them turned into a real outage (``outage_since``, the last meter reading).

    def _time(self, key: str) -> datetime | None:
        value = self._persisted.data.get(key)
        return dt_util.parse_datetime(value) if value else None

    @property
    def brownout_since(self) -> datetime | None:
        return self._time("brownout_since")

    @property
    def outage_since(self) -> datetime | None:
        return self._time("outage_since")

    @property
    def alive_since(self) -> datetime | None:
        return self._time("alive_since")

    @property
    def back_since(self) -> datetime | None:
        """While a brownout or an outage is watched: since when the meter has been back and in range."""
        return self._time("back_since")

    def _set_back(self, when: datetime | None) -> None:
        self._persisted.data["back_since"] = when.isoformat() if when else None
        self._persisted.async_schedule_save()

    @property
    def brownout(self) -> bool:
        return self.brownout_since is not None

    @property
    def watching(self) -> bool:
        return any(t is not None for t in (self.alive_since, self.brownout_since, self.outage_since))

    def _set_watch(
        self,
        *,
        alive_since: datetime | None = None,
        brownout_since: datetime | None = None,
        outage_since: datetime | None = None,
    ) -> None:
        for key, value in (("alive_since", alive_since), ("brownout_since", brownout_since), ("outage_since", outage_since)):
            self._persisted.data[key] = value.isoformat() if value else None
        self._persisted.data["back_since"] = None
        self._persisted.data["inverter_note_sent"] = False
        self._persisted.async_schedule_save()
        if self.watching and self._unsub_watch is None:
            self._unsub_watch = async_track_time_interval(self.hass, self._watch_check, WATCH_INTERVAL)
        elif not self.watching and self._unsub_watch is not None:
            self._unsub_watch()
            self._unsub_watch = None
        self._notify()

    @property
    def last(self) -> dict[str, Any]:
        return self._persisted.data.get("last") or {"verdict": VERDICT_NONE}

    # --- lifecycle --------------------------------------------------------

    @callback
    def async_start(self) -> None:
        self._started = dt_util.utcnow()
        if self.watching:  # restart the watch
            self._set_watch(
                alive_since=self.alive_since, brownout_since=self.brownout_since, outage_since=self.outage_since
            )

    @callback
    def async_stop(self) -> None:
        if self._unsub_watch is not None:
            self._unsub_watch()
            self._unsub_watch = None

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

    def _last_reading_time(self) -> datetime:
        """When the meter last reported (it may already be ``unavailable``)."""
        state = self.hass.states.get(self._voltage_entity)
        if state is not None and _float(state.state) is not None:
            return state.last_reported
        return dt_util.utcnow()

    async def _watch_check(self, _now: datetime) -> None:
        """Follow a "the grid is there" post: tell the channel if it becomes a real outage, or comes back out of range."""
        if (dt_util.utcnow() - self._started).total_seconds() < self._fresh:
            return  # just started: the meter may simply not have reported yet
        voltage = self.meter_voltage()
        since = self.brownout_since or self.alive_since
        if since is not None and voltage is None:
            brownout = self.brownout_since is not None
            when = max(self._last_reading_time(), since)
            self._set_watch(outage_since=when)  # before awaiting: a second tick must not post again
            lines = [f"🔴 Мережа зникла о {_hhmm(when)}."]
            if brownout and (bad := (await self._bad_stretch(since))[0]):
                lines.append(f"Перед тим з {_hhmm(since)} напруга була поза нормою: {_span(bad)}.")
            _LOGGER.info("The reported brownout turned into an outage at %s", when)
            await self._send("\n".join(lines))
        elif (self.outage_since or self.brownout_since) and voltage is not None and self._in_range(voltage):
            await self._back_in_range()
        elif self.brownout_since is not None and voltage is not None:
            self._set_back(None)  # out of range again
        elif voltage is None and self.back_since is not None:
            self._set_back(None)  # silent again
        elif self.outage_since is not None and voltage is not None:
            state = self.hass.states.get(self._voltage_entity)
            back = min(state.last_reported, dt_util.utcnow()) if state else dt_util.utcnow()
            self._set_watch(brownout_since=back)
            _LOGGER.info("The meter is back at %s V, still out of range", voltage)
            await self._send(
                f"⚡ Мережа повернулась о {_hhmm(back)}, але напруга поза нормою.\n{self._voltage_detail(voltage)}"
            )

    async def _back_in_range(self) -> None:
        """The grid is back and fine; if Svitlobot stays silent for ``RESTORE_WAIT``, say so ourselves."""
        now = dt_util.utcnow()
        if (back := self.back_since) is None:
            state = self.hass.states.get(self._voltage_entity)
            self._set_back(min(state.last_reported, now) if state else now)
            return
        if now - back < RESTORE_WAIT:
            return
        voltage = self.meter_voltage()
        if self._grid_entity and (grid := self.hass.states.get(self._grid_entity)) and grid.state != STATE_ON:
            # The building has the grid, the flat doesn't (the relay or the inverter): Svitlobot is right.
            if not self._persisted.data.get("inverter_note_sent"):
                self._persisted.data["inverter_note_sent"] = True
                self._persisted.async_schedule_save()
                if self._send_owner:
                    await self._send_owner(
                        f"🔧 Лічильник перед інвертором бачить {_volts(voltage)} з {_hhmm(back)}, "
                        "а інвертор мережу не приймає."
                    )
            return
        brownout_since = self.brownout_since
        self._set_watch(alive_since=back)  # Svitlobot still thinks it's dark: a later outage must be posted too
        lines = [f"✅ Мережа повернулась о {_hhmm(back)}, напруга в нормі: {_volts(voltage)}."]
        if brownout_since is not None and (bad := (await self._bad_stretch(brownout_since))[0]):
            lines.append(f"З {_hhmm(brownout_since)} до {_hhmm(back)} напруга була поза нормою: {_span(bad)}.")
        _LOGGER.info("The grid is back since %s and Svitlobot hasn't said so", back)
        await self._send("\n".join(lines))
        if self._send_owner:
            await self._send_owner("\n".join(["ℹ️ Світлобот досі не написав «Світло з'явилося».", *self._pinger_lines()]))

    async def _bad_stretch(self, since: datetime) -> tuple[list[float], datetime | None]:
        """Out-of-range readings from ``since`` to the last one, and when the stretch ended (the next good reading)."""
        # A second early, so the reading that sits exactly on ``since`` is a change and not the start state;
        # the start state (stamped with the period's start) is the reading before ``since`` and is dropped.
        history = await self._history(since - timedelta(seconds=1))
        readings = [(when, value) for when, value in history if value is not None and when >= since]
        last_bad = max((i for i, (_, v) in enumerate(readings) if not self._in_range(v)), default=None)
        if last_bad is None:
            return [], None
        bad = [v for _, v in readings[: last_bad + 1] if not self._in_range(v)]
        end = readings[last_bad + 1][0] if last_bad + 1 < len(readings) else None
        return bad, end

    # --- the checks -------------------------------------------------------

    async def async_check(self, text: str, link: str | None = None) -> str | None:
        """Check one Svitlobot post; return the verdict, or None if there is nothing to say."""
        if is_outage_post(text):
            return await self._check_outage(link, post_time(text, dt_util.utcnow()))
        if is_restored_post(text) and self.brownout:
            return await self._back_to_normal()
        if is_restored_post(text) and self.watching:
            self._set_watch()  # after a real outage or a false alarm Svitlobot is right now: nothing to add
        return None

    async def _bad_since_post(self, reported: datetime | None) -> list[Reading]:
        """Out-of-range readings since the outage time Svitlobot gave (a minute early: it truncates to minutes)."""
        if reported is None:
            return []
        readings = await self._history(reported - timedelta(minutes=1))
        return [(when, value) for when, value in readings if value is not None and not self._in_range(value)]

    async def _check_outage(self, link: str | None, reported: datetime | None = None) -> str:
        voltage = self.meter_voltage()
        lines: list[str] = []
        owner_message = None
        self._set_watch()  # a new post starts over
        if voltage is None:
            verdict = VERDICT_CONFIRMED
        elif self._in_range(voltage) and (bad := await self._bad_since_post(reported)):
            # The voltage is fine right now, but it wasn't when Svitlobot lost its pinger: a brownout, not a false alarm.
            verdict = VERDICT_OUT_OF_RANGE
            since, values = bad[0][0], [value for _, value in bad]
            lines.append("⚡ Мережа є, але її параметри були поза нормою.")
            lines.append(f"Зараз напруга {_volts(voltage)}, а від {_hhmm(since)} {self._excursion_text(values)}.")
            self._set_watch(brownout_since=since)
        elif self._in_range(voltage):
            verdict = VERDICT_FALSE_ALARM
            self._set_watch(alive_since=dt_util.utcnow())
            lines.append(f"⚠️ Мережа є: {_volts(voltage)}. Повідомлення Світлобота, ймовірно, помилкове.")
            owner_message = self._owner_message(voltage)
        else:
            verdict = VERDICT_OUT_OF_RANGE
            lines.append("⚡ Мережа є, але її параметри поза нормою.")
            lines.append(self._voltage_detail(voltage))
            since, values = await self._brownout_readings(voltage)
            lines.append(f"Від {_hhmm(since)} напруга коливається {_range_text(values)}.")
            self._set_watch(brownout_since=since)

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
        self._set_watch()
        voltage = self.meter_voltage()
        if voltage is None or since is None:
            return None  # the meter is silent: it became a real outage meanwhile

        bad, end = await self._bad_stretch(since)

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

    def _excursion_text(self, values: list[float]) -> str:
        """ "вона опускалася до 174 В" / "піднімалася до 263 В" / "коливалася в межах 174–263 В"."""
        low, high = min(values), max(values)
        if high < self._vmin:
            return f"вона опускалася до {_volts(low)}"
        if low > self._vmax:
            return f"вона піднімалася до {_volts(high)}"
        return f"вона коливалася в межах {low:.0f}–{_volts(high)}"

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
        return "\n".join([*lines, *self._pinger_lines()])

    def _pinger_lines(self) -> list[str]:
        if self._pinger_entity and (pinger := self.hass.states.get(self._pinger_entity)):
            if pinger.state == STATE_ON:
                return ["Пінгер на зв'язку — схоже, збій Світлобота."]
            return [f"Пінгер не відповідає з {_hhmm(pinger.last_changed)}."]
        return []
