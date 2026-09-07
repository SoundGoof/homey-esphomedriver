"""Push ESPHome SensorState into Homey measure and meter capabilities."""

from __future__ import annotations

from typing import Any, cast

from aioesphomeapi import EntityState, SensorState

from homey_esphomedriver.entities.state.base import (
    AbstractEntityStateUpdateHandler,
)
from homey_esphomedriver.esphome_util import format_uptime, is_missing_number
from homey_esphomedriver.units import convert_units


class SensorEntityStateUpdateHandler(AbstractEntityStateUpdateHandler):
    async def handle(self, state: EntityState, capabilities: list[str]) -> None:
        capability = capabilities[0]
        sensor = cast(SensorState, state)
        if sensor.missing_state or is_missing_number(sensor.state):
            self.set_capability_value(capability, None)
            return

        capability_value: Any = sensor.state
        if capability.startswith("measure_uptime"):
            self.set_capability_value(
                capability, format_uptime(float(capability_value))
            )
            return
        if (
            capability.startswith("measure_")
            or capability.startswith("meter_")
            or capability.startswith("esphome_number.")
        ):
            capability_value = float(capability_value)
        elif capability.startswith("esphome_string."):
            capability_value = str(capability_value)
        elif capability.startswith("esphome_boolean."):
            capability_value = bool(capability_value)

        capability_value = convert_units(self.device, capability, capability_value)
        self.set_capability_value(capability, capability_value)
