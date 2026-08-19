"""Runtime dispatch of ESPHome entity states onto Homey capabilities.

Builds a key-to-capabilities index from pair-time options, then routes each
:class:`~aioesphomeapi.EntityState` to the matching handler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aioesphomeapi import (
    AlarmControlPanelEntityState,
    BinarySensorState,
    ClimateState,
    CoverState,
    EntityState,
    Event,
    FanState,
    LightState,
    LockEntityState,
    MediaPlayerEntityState,
    NumberState,
    SelectState,
    SensorState,
    SirenState,
    SwitchState,
    TextSensorState,
    ValveState,
    WaterHeaterState,
)

from homey_esphomedriver.esphome_util import debug_log
from homey_esphomedriver.state.alarm_control_panel import (
    AlarmControlPanelEntityStateUpdateHandler,
)
from homey_esphomedriver.state.base import AbstractEntityStateUpdateHandler
from homey_esphomedriver.state.binary_sensor import (
    BinarySensorEntityStateUpdateHandler,
)
from homey_esphomedriver.state.climate import (
    ClimateEntityStateUpdateHandler,
)
from homey_esphomedriver.state.cover import (
    CoverEntityStateUpdateHandler,
)
from homey_esphomedriver.state.event import (
    EventEntityStateUpdateHandler,
)
from homey_esphomedriver.state.fan import (
    FanEntityStateUpdateHandler,
)
from homey_esphomedriver.state.light import (
    LightEntityStateUpdateHandler,
)
from homey_esphomedriver.state.lock import (
    LockEntityStateUpdateHandler,
)
from homey_esphomedriver.state.media_player import (
    MediaPlayerEntityStateUpdateHandler,
)
from homey_esphomedriver.state.number import (
    NumberEntityStateUpdateHandler,
)
from homey_esphomedriver.state.select import (
    SelectEntityStateUpdateHandler,
)
from homey_esphomedriver.state.sensor import (
    SensorEntityStateUpdateHandler,
)
from homey_esphomedriver.state.siren import (
    SirenEntityStateUpdateHandler,
)
from homey_esphomedriver.state.switch import (
    SwitchEntityStateUpdateHandler,
)
from homey_esphomedriver.state.text_sensor import (
    TextSensorEntityStateUpdateHandler,
)
from homey_esphomedriver.state.valve import (
    ValveEntityStateUpdateHandler,
)
from homey_esphomedriver.state.water_heater import (
    WaterHeaterEntityStateUpdateHandler,
)

if TYPE_CHECKING:
    from homey.device import Device


class DeviceEntityStateHandler:
    """Owns the key→capability index and dispatches subscribed states."""

    def __init__(self, device: Device) -> None:
        self._device = device
        self._key_to_capabilities: dict[int, list[str]] = {}
        self._handlers_by_type: dict[
            type[EntityState], AbstractEntityStateUpdateHandler
        ] = {
            AlarmControlPanelEntityState: AlarmControlPanelEntityStateUpdateHandler(
                device
            ),
            BinarySensorState: BinarySensorEntityStateUpdateHandler(device),
            ClimateState: ClimateEntityStateUpdateHandler(device),
            CoverState: CoverEntityStateUpdateHandler(device),
            Event: EventEntityStateUpdateHandler(device),
            FanState: FanEntityStateUpdateHandler(device),
            LightState: LightEntityStateUpdateHandler(device),
            LockEntityState: LockEntityStateUpdateHandler(device),
            MediaPlayerEntityState: MediaPlayerEntityStateUpdateHandler(device),
            NumberState: NumberEntityStateUpdateHandler(device),
            SelectState: SelectEntityStateUpdateHandler(device),
            SensorState: SensorEntityStateUpdateHandler(device),
            SirenState: SirenEntityStateUpdateHandler(device),
            SwitchState: SwitchEntityStateUpdateHandler(device),
            TextSensorState: TextSensorEntityStateUpdateHandler(device),
            ValveState: ValveEntityStateUpdateHandler(device),
            WaterHeaterState: WaterHeaterEntityStateUpdateHandler(device),
        }

    def init(self) -> None:
        """Build the entity-key index from capability options set at pair time."""
        self._key_to_capabilities.clear()

        for capability in self._device.get_capabilities():
            key = self._device.get_capability_options(capability).get("key")
            if key is None:
                continue
            self._key_to_capabilities.setdefault(int(key), []).append(capability)

    def uninit(self) -> None:
        """Release per-handler timers and other resources."""
        for handler in self._handlers_by_type.values():
            handler.uninit()

    async def handle_state(self, state: EntityState) -> None:
        """Route one ESPHome state update to the matching Homey capabilities."""
        capabilities = self._key_to_capabilities.get(state.key, [])
        if not capabilities:
            return
        self._debug(
            f"Handling entity state key={state.key} caps={capabilities}: {state}"
        )
        handler = self._handlers_by_type.get(type(state))
        if handler is None:
            return
        await handler.handle(state, capabilities)

    def _debug(self, *args: object) -> None:
        debug_log(self._device.log, *args)
