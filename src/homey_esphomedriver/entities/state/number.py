"""Push ESPHome NumberState into Homey esphome_number capabilities."""

from __future__ import annotations

from typing import cast

from aioesphomeapi import EntityState, NumberState

from homey_esphomedriver.entities.state.base import (
    AbstractEntityStateUpdateHandler,
)
from homey_esphomedriver.esphome_util import is_missing_number


class NumberEntityStateUpdateHandler(AbstractEntityStateUpdateHandler):
    async def handle(self, state: EntityState, capabilities: list[str]) -> None:
        capability = capabilities[0]
        number = cast(NumberState, state)
        if number.missing_state or is_missing_number(number.state):
            self.set_capability_value(capability, None)
            return

        self.set_capability_value(capability, float(number.state))
