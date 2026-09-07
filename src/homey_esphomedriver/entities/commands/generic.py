"""Multi-domain onoff plus button / number / select command handlers."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from aioesphomeapi import ClimateMode

from homey_esphomedriver.entities.commands.base import AbstractEntityCommandHandler

if TYPE_CHECKING:
    from homey_esphomedriver.esphome_client import EspHomeClient

WRITE_COMMANDS: dict[str, tuple[str, Callable[[Any], Any]]] = {
    "number": ("number_command", float),
    "select": ("select_command", str),
    "switch": ("switch_command", bool),
}
"""Native API write command per entity domain, with the coercion its state takes.

Shared by the capability listeners and by settings-page fields mapped onto an
entity, so the two paths cannot drift on how a domain is written.
"""


def write_entity_value(
    client: EspHomeClient, domain: str, key: int, value: Any
) -> None:
    """Send ``value`` to the entity ``key`` of ``domain``.

    Raises:
        KeyError: If the domain has no write command.
        TypeError, ValueError: If the value cannot be coerced for the domain.
    """
    name, coerce = WRITE_COMMANDS[domain]
    client.command(name, key, coerce(value))


class GenericEntityCommandHandler(AbstractEntityCommandHandler):
    CAPABILITIES = ("onoff", "button", "number", "select")
    VALUELESS_CAPABILITIES = ("button",)

    async def onoff(
        self,
        value: Any,
        capability_id: str = "onoff",
        **_kwargs: Any,
    ) -> None:
        key = self._get_entity_key(capability_id)
        entity_type = self._get_entity_type(capability_id)
        client = self._require_client()

        if entity_type == "light":
            client.command("light_command", key, state=bool(value))
            return
        if entity_type == "fan":
            client.command("fan_command", key, state=bool(value))
            return
        if entity_type == "valve":
            client.command("valve_command", key, position=1.0 if value else 0.0)
            return
        if entity_type == "climate":
            if value:
                client.command(
                    "climate_command",
                    key,
                    mode=ClimateMode(
                        int(
                            self.device.get_capability_options(capability_id)[
                                "climate_on_mode"
                            ]
                        )
                    ),
                )
            else:
                client.command("climate_command", key, mode=ClimateMode.OFF)
            return
        if entity_type == "water_heater":
            client.command("water_heater_command", key, on=bool(value))
            return
        if entity_type == "siren":
            client.command("siren_command", key, state=bool(value))
            return
        if entity_type == "switch":
            write_entity_value(client, "switch", key, value)
            return

        raise ValueError(f"Unsupported entity type for onoff: {entity_type}")

    async def button(self, *, capability_id: str, **_kwargs: Any) -> None:
        self._require_client().command(
            "button_command", self._get_entity_key(capability_id)
        )

    async def number(
        self,
        value: Any,
        *,
        capability_id: str,
        **_kwargs: Any,
    ) -> None:
        write_entity_value(
            self._require_client(),
            "number",
            self._get_entity_key(capability_id),
            value,
        )

    async def select(
        self,
        value: Any,
        *,
        capability_id: str,
        **_kwargs: Any,
    ) -> None:
        write_entity_value(
            self._require_client(),
            "select",
            self._get_entity_key(capability_id),
            value,
        )
