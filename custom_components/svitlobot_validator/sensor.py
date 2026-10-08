"""Sensor with the verdict on the last Svitlobot outage post."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SvitlobotValidatorConfigEntry
from .const import VERDICTS
from .entity import ValidatorEntity
from .validator import Validator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SvitlobotValidatorConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([LastVerdictSensor(entry, entry.runtime_data.validator)])


class LastVerdictSensor(ValidatorEntity, SensorEntity):
    """none / false_alarm / out_of_range / confirmed, with the details as attributes."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = VERDICTS
    _attr_icon = "mdi:message-alert-outline"

    def __init__(self, entry: SvitlobotValidatorConfigEntry, validator: Validator) -> None:
        super().__init__(entry, "last_verdict")
        self._validator = validator

    @property
    def native_value(self) -> str:
        return self._validator.last["verdict"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        last = self._validator.last
        return {
            "checked_at": last.get("checked_at"),
            "post_link": last.get("post_link"),
            "voltage": last.get("voltage"),
            "message": last.get("message"),
            "brownout": self._validator.brownout,
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._validator.async_add_listener(self._on_change))

    @callback
    def _on_change(self) -> None:
        self.async_write_ha_state()
