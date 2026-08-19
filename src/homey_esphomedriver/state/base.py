"""Shared helpers for pushing ESPHome entity states into Homey capabilities."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from aioesphomeapi import EntityState

from homey_esphomedriver.esphome_util import debug_log

if TYPE_CHECKING:
    from homey.device import Device

    from homey_esphomedriver.esphome_driver import EspHomeDriver


class AbstractEntityStateUpdateHandler:
    """Base with capability I/O helpers used by concrete entity handlers."""

    def __init__(self, device: Device) -> None:
        self.device = device

    async def handle(self, state: EntityState, capabilities: list[str]) -> None:
        """Apply ``state`` to the Homey capabilities bound to this entity.

        Args:
            state: Latest Native API entity state.
            capabilities: Capability ids stored against ``state.key``.
        """
        raise NotImplementedError

    def uninit(self) -> None:
        """Release timers or other handler resources. Override when needed."""

    def has_capability(self, capability_id: str) -> bool:
        return self.device.has_capability(capability_id)

    def find_capability(self, capabilities: list[str], base: str) -> str | None:
        """Return the mapped capability whose id is ``base`` or ``base.<suffix>``."""
        for capability in capabilities:
            if capability == base or capability.startswith(f"{base}."):
                return capability
        return None

    def set_capability_value(self, capability_id: str, value: Any) -> None:
        """Write a capability without awaiting.

        Also fires base ``esphome_*`` Flow cards for sub-capabilities. Homey only
        auto-triggers ``<full_id>_changed`` (e.g. ``esphome_number.foo_changed``),
        which cannot be declared for dynamic object IDs at compose time.
        """
        if not self.has_capability(capability_id):
            self.error(f"Unavailable capability requested: {capability_id}")
            return

        previous = self.device.get_capability_value(capability_id)
        task = asyncio.ensure_future(
            self._set_capability_value_and_trigger(capability_id, value, previous)
        )
        task.add_done_callback(self._on_set_capability_done)

    async def _set_capability_value_and_trigger(
        self,
        capability_id: str,
        value: Any,
        previous: Any,
    ) -> None:
        await self.device.set_capability_value(capability_id, value)
        if previous == value:
            return
        await self._trigger_esphome_flow_if_needed(capability_id, value)

    async def _trigger_esphome_flow_if_needed(
        self,
        capability_id: str,
        value: Any,
    ) -> None:
        if "." not in capability_id:
            return

        base, _suffix = capability_id.split(".", 1)
        if base not in {
            "esphome_number",
            "esphome_select",
            "esphome_string",
            "esphome_boolean",
        }:
            return

        driver = self._esphome_driver()
        name = self._capability_title(capability_id)
        if base == "esphome_number" and isinstance(value, (int, float)):
            await driver.trigger_esphome_number_changed(
                self.device,
                float(value),
                name,
            )
        elif base == "esphome_select" and value is not None:
            await driver.trigger_esphome_select_changed(
                self.device,
                str(value),
                name,
            )
        elif base == "esphome_string" and value is not None:
            await driver.trigger_esphome_string_changed(
                self.device,
                str(value),
                name,
            )
        elif base == "esphome_boolean" and isinstance(value, bool):
            await driver.trigger_esphome_boolean_changed(
                self.device,
                value,
                name,
            )

    def _esphome_driver(self) -> EspHomeDriver:
        return self.device.driver  # type: ignore[return-value]

    def _capability_title(self, capability_id: str) -> str:
        title = self.device.get_capability_options(capability_id).get("title")
        if isinstance(title, str) and title:
            return title
        if isinstance(title, dict):
            text = title.get("en")
            if isinstance(text, str) and text:
                return text
        return capability_id

    def handle_on_off(
        self,
        state: bool,
        capability: str,
        invert: bool = False,
    ) -> None:
        """Apply a boolean ESPHome state to an onoff/alarm capability."""
        self.set_capability_value(capability, (not state) if invert else state)

    def log(self, *args: object) -> None:
        self.device.log(*args)

    def debug(self, *args: object) -> None:
        debug_log(self.device.log, *args)

    def error(self, *args: object) -> None:
        self.device.error(*args)

    def _on_set_capability_done(self, task: asyncio.Future[Any]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self.error(error)
