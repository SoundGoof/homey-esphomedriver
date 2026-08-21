"""Pair-time mapping of ESPHome entities onto one Homey device.

Per-entity mappers live in this package. Homey system UIs (light, cover,
climate, and similar) bind bare capability IDs, so those domains map one
entity per Homey device. Custom ``esphome_*`` capabilities are always suffixed.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Protocol

from aioesphomeapi import (
    AlarmControlPanelInfo,
    BinarySensorInfo,
    ButtonInfo,
    ClimateInfo,
    CoverInfo,
    DeviceInfo,
    EntityCategory,
    EntityInfo,
    EventInfo,
    FanInfo,
    LightInfo,
    LockInfo,
    MediaPlayerInfo,
    NumberInfo,
    SelectInfo,
    SensorInfo,
    SirenInfo,
    SwitchInfo,
    TemperatureUnit,
    TextSensorInfo,
    ValveInfo,
    WaterHeaterInfo,
)

from homey_esphomedriver.esphome_types import HomeyEspHomeDeviceOption
from homey_esphomedriver.esphome_util import (
    device_info_settings,
    wanted_markers,
)
from homey_esphomedriver.profile import DEFAULT_BRAND_PROFILE, BrandProfile

REFRESH_CAPABILITY = "button.refresh"

REFRESH_CAPABILITY_OPTIONS: dict[str, Any] = json.loads(
    files("homey_esphomedriver")
    .joinpath("homey_template/compose/drivers/templates/esphome-defaults.json")
    .read_text(encoding="utf-8")
)["capabilitiesOptions"][REFRESH_CAPABILITY]

_active_profile: BrandProfile = DEFAULT_BRAND_PROFILE
_current_entity: EntityInfo | None = None

# First non-sensor class wins. Light does not replace sensor. Only doorbell
# events set a Homey class.
_ENTITY_PRIORITY: dict[type[EntityInfo], int] = {
    AlarmControlPanelInfo: 0,
    WaterHeaterInfo: 1,
    ClimateInfo: 2,
    CoverInfo: 3,
    LockInfo: 4,
    ValveInfo: 5,
    MediaPlayerInfo: 6,
    FanInfo: 7,
    SirenInfo: 8,
    EventInfo: 9,
    BinarySensorInfo: 10,
    SensorInfo: 11,
    LightInfo: 12,
    SwitchInfo: 13,
    ButtonInfo: 14,
    NumberInfo: 15,
    SelectInfo: 16,
    TextSensorInfo: 17,
}

# wifi_info / version text sensors duplicate Device Information settings.
_DEVICE_INFO_TEXT_SENSOR_IDS = frozenset(
    {
        "ip_address",
        "mac_address",
        "dns_address",
        "esphome_version",
        "version",
    }
)


@dataclass(frozen=True, slots=True)
class ObjectIdAlias:
    """Maps an ESPHome object id or unit onto a Homey capability id."""

    capability: str
    """Homey capability id to assign."""

    exact: tuple[str, ...] = ()
    """Object ids that match exactly."""

    suffixes: tuple[str, ...] = ()
    """Object id suffixes that match."""

    units: tuple[str, ...] = ()
    """Units of measurement that match."""

    unless_device_class: tuple[str, ...] = ()
    """Skip this alias when the entity has one of these device classes."""

    claim_class: bool = True
    """Whether matching this alias should set the Homey device class."""


def match_object_id_alias(
    object_id: str,
    unit: str | None,
    device_class: str,
    aliases: Sequence[ObjectIdAlias],
) -> ObjectIdAlias | None:
    """Return the first alias matching object id, suffix, or unit.

    Args:
        object_id: ESPHome entity object id.
        unit: Unit of measurement, if any.
        device_class: ESPHome device class used to skip aliases via
            :attr:`ObjectIdAlias.unless_device_class`.
        aliases: Ordered alias table; first match wins.
    """
    object_id = object_id.lower()
    unit_lower = (unit or "").lower().strip()
    device_class = device_class.lower()
    for alias in aliases:
        if alias.unless_device_class and device_class in alias.unless_device_class:
            continue
        if object_id in alias.exact:
            return alias
        if alias.suffixes and object_id.endswith(alias.suffixes):
            return alias
        if alias.units and unit_lower in alias.units:
            return alias
    return None


def lookup_device_class(
    value: str | None,
    mapping: Mapping[str, str],
) -> str | None:
    """Look up a lowered ESPHome device class in a Homey capability/class map."""
    if not value:
        return None
    return mapping.get(value.lower())


def picker_values(entries: Iterable[tuple[str, str]]) -> list[dict[str, object]]:
    """Homey enum picker options ``[{id, title: {en}}]``."""
    return [{"id": item_id, "title": {"en": title}} for item_id, title in entries]


def temperature_unit_label(unit: TemperatureUnit | None) -> str:
    """ESPHome wire unit stored on the capability for runtime conversion."""
    if unit == TemperatureUnit.FAHRENHEIT:
        return "°F"
    if unit == TemperatureUnit.KELVIN:
        return "K"
    return "°C"


def to_celsius(unit: TemperatureUnit | None, value: float) -> float:
    """ESPHome temperature as Homey °C."""
    if unit == TemperatureUnit.FAHRENHEIT:
        return ((value - 32) * 5) / 9
    if unit == TemperatureUnit.KELVIN:
        return value - 273.15
    return value


def celsius_step(unit: TemperatureUnit | None, step: float) -> float:
    """ESPHome temperature step as Homey °C."""
    if unit == TemperatureUnit.FAHRENHEIT:
        return step * 5 / 9
    return step


def _entity_domain(entity: EntityInfo | None) -> str | None:
    """Snake-case domain from ``LightInfo`` → ``light``."""
    if entity is None:
        return None
    name = type(entity).__name__
    if name.endswith("Info"):
        name = name[: -len("Info")]
    chars: list[str] = []
    for index, char in enumerate(name):
        if char.isupper() and index:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars) or None


class _EntityMapper(Protocol):
    def map(
        self,
        entity: EntityInfo,
        homey_device: HomeyEspHomeDeviceOption,
    ) -> None: ...


_MAPPERS: dict[type[EntityInfo], _EntityMapper] | None = None


class DeviceEntityMapper:
    """Maps ESPHome entities onto a Homey pair-time payload."""

    @staticmethod
    def empty_option(
        capabilities: list[str] | None = None,
    ) -> HomeyEspHomeDeviceOption:
        """Empty pair-time payload the mapper mutates in place."""
        return {
            "name": "",
            "data": {},
            "store": {},
            "settings": {},
            "capabilities": capabilities if capabilities is not None else [],
            "capabilitiesOptions": {},
        }

    @staticmethod
    def pair_option(
        device_info: DeviceInfo,
        *,
        host: str,
        port: int,
        noise_psk: str | None,
        data_id: str,
    ) -> HomeyEspHomeDeviceOption:
        """Pair-time payload with connection data and empty capabilities."""
        name = device_info.friendly_name or device_info.name or "ESPHome Device"
        return {
            "name": name,
            "data": {"id": data_id},
            "store": {
                "address": host,
                "host": device_info.name or host,
                "port": port,
                "mac": device_info.mac_address,
                "model": device_info.model,
                "manufacturer": device_info.manufacturer,
                "esphome_version": device_info.esphome_version,
                "noise_psk": noise_psk or "",
            },
            "settings": {
                "host": host,
                "port": str(port),
                **device_info_settings(
                    device_info, host=host, encrypted=bool(noise_psk)
                ),
                "device_class": "auto",
                "show_diagnostics": False,
                "show_configuration": False,
            },
            "capabilities": [],
            "capabilitiesOptions": {},
        }

    @classmethod
    def map(
        cls,
        entities: list[EntityInfo],
        homey_device: HomeyEspHomeDeviceOption,
        *,
        category_only: EntityCategory | None = None,
        profile: BrandProfile | None = None,
    ) -> None:
        """Map supported entity types onto ``homey_device``.

        Args:
            entities: Native API entity infos from the node.
            homey_device: Pair-time device payload to mutate.
            category_only: When set, only map entities in that category
                (used when the user enables diagnostic or configuration caps).
            profile: Brand remaps and filters. Defaults to accept-all.
        """
        global _active_profile, _current_entity
        mappers = _get_mappers()
        previous_profile = _active_profile
        _active_profile = profile or DEFAULT_BRAND_PROFILE
        try:
            for entity in sorted(
                entities,
                key=lambda item: _ENTITY_PRIORITY.get(type(item), 100),
            ):
                if cls._should_skip(
                    entity,
                    category_only=category_only,
                    profile=_active_profile,
                ):
                    continue

                mapper = mappers.get(type(entity))
                if mapper is None:
                    continue
                _current_entity = entity
                try:
                    mapper.map(entity, homey_device)
                finally:
                    _current_entity = None
            _active_profile.after_map(entities, homey_device)
        finally:
            _active_profile = previous_profile

    @classmethod
    def map_device(
        cls,
        entities: list[EntityInfo],
        homey_device: HomeyEspHomeDeviceOption,
        *,
        diagnostics: bool = False,
        configuration: bool = False,
        profile: BrandProfile | None = None,
        services: Sequence[Any] = (),
    ) -> None:
        """Fill a pair-time payload, including ``button.refresh`` and markers.

        Args:
            entities: Native API entity infos from the node.
            homey_device: Pair-time device payload to mutate.
            diagnostics: Also map diagnostic-category entities.
            configuration: Also map configuration-category entities.
            profile: Brand remaps and filters. Defaults to accept-all.
            services: User-defined API actions the node declares. Only their
                presence matters, and only for the action marker.
        """
        cls.map(entities, homey_device, profile=profile)
        if diagnostics:
            cls.map(
                entities,
                homey_device,
                category_only=EntityCategory.DIAGNOSTIC,
                profile=profile,
            )
        if configuration:
            cls.map(
                entities,
                homey_device,
                category_only=EntityCategory.CONFIG,
                profile=profile,
            )
        homey_device["capabilities"].append(REFRESH_CAPABILITY)
        homey_device["capabilitiesOptions"][REFRESH_CAPABILITY] = dict(
            REFRESH_CAPABILITY_OPTIONS
        )
        # Markers belong to the same fill: pairing and Refresh both call this,
        # and a marker only one of them knows about is one the other plans away.
        object_ids = [
            object_id
            for entity in entities
            if (object_id := getattr(entity, "object_id", None)) is not None
        ]
        for marker in wanted_markers(
            object_ids,
            has_actions=bool(services),
            slots=(profile or DEFAULT_BRAND_PROFILE).display_slots,
        ):
            cls.add_marker(homey_device, marker)

    @staticmethod
    def _should_skip(
        entity: EntityInfo,
        *,
        category_only: EntityCategory | None = None,
        profile: BrandProfile | None = None,
    ) -> bool:
        """Skip entities that should not appear on the Homey device."""
        if (
            isinstance(entity, TextSensorInfo)
            and entity.object_id in _DEVICE_INFO_TEXT_SENSOR_IDS
        ):
            return True
        if entity.disabled_by_default:
            return True
        if profile is not None and profile.skip_entity(entity):
            return True
        if category_only is not None:
            return entity.entity_category != category_only
        return entity.entity_category in (
            EntityCategory.DIAGNOSTIC,
            EntityCategory.CONFIG,
        )

    @staticmethod
    def has_entity_type(
        homey_device: HomeyEspHomeDeviceOption,
        entity_type: str,
    ) -> bool:
        """Return whether a capability is already bound to this entity domain."""
        return any(
            options.get("entity_type") == entity_type
            for options in homey_device["capabilitiesOptions"].values()
        )

    @classmethod
    def add_indexed(
        cls,
        homey_device: HomeyEspHomeDeviceOption,
        key: int,
        base: str,
        capability_options: dict[str, Any] | None = None,
    ) -> None:
        """Add ``base``, or ``base.<object_id>`` when ``base`` is already used."""
        suffix = _current_entity.object_id if _current_entity is not None else base
        capability = cls.next_capability_id(homey_device, base, suffix)
        cls.add_capability(homey_device, key, capability, capability_options)

    @classmethod
    def add_suffixed(
        cls,
        homey_device: HomeyEspHomeDeviceOption,
        key: int,
        base: str,
        capability_options: dict[str, Any] | None = None,
    ) -> None:
        """Add ``base.<object_id>`` and a hidden bare id for Flow ``$filter``.

        ``button`` uses ``esphome_button`` so Homey does not emit a system Press
        card for a dummy bare ``button``.
        """
        marker = "esphome_button" if base == "button" else base
        cls.add_marker(homey_device, marker)
        suffix = _current_entity.object_id if _current_entity is not None else base
        cls.add_capability(homey_device, key, f"{base}.{suffix}", capability_options)

    @staticmethod
    def add_marker(
        homey_device: HomeyEspHomeDeviceOption,
        capability_id: str,
    ) -> None:
        """Add a hidden capability that exists only for a Flow ``$filter``.

        Homey matches ``$filter`` against exact capability ids, so a card that
        applies to a whole feature needs one bare id to match on.
        """
        if capability_id not in homey_device["capabilities"]:
            homey_device["capabilities"].append(capability_id)
            homey_device["capabilitiesOptions"][capability_id] = {"uiComponent": None}

    @staticmethod
    def add_capability(
        homey_device: HomeyEspHomeDeviceOption,
        key: int,
        capability: str,
        capability_options: dict[str, Any] | None = None,
    ) -> None:
        """Push a capability and bind it to the ESPHome entity key."""
        if _current_entity is not None:
            capability = _active_profile.capability_id_for(_current_entity, capability)
        homey_device["capabilities"].append(capability)
        options = homey_device["capabilitiesOptions"].setdefault(capability, {})
        options["key"] = key
        if capability_options:
            options.update(capability_options)
        if "entity_type" not in options:
            domain = _entity_domain(_current_entity)
            if domain is not None:
                options["entity_type"] = domain
        if "." in capability and "title" not in options and _current_entity is not None:
            title = _current_entity.name or _current_entity.object_id
            if title:
                options["title"] = title

    @staticmethod
    def next_capability_id(
        homey_device: HomeyEspHomeDeviceOption,
        base_capability: str,
        suffix: str,
    ) -> str:
        """Return ``base`` or ``base.<suffix>`` when the native cap is already used."""
        existing = [
            capability
            for capability in homey_device["capabilities"]
            if capability == base_capability
            or capability.startswith(f"{base_capability}.")
        ]
        if not existing:
            return base_capability
        return f"{base_capability}.{suffix}"

    @staticmethod
    def set_device_class(
        homey_device: HomeyEspHomeDeviceOption,
        device_class: str,
    ) -> None:
        """Keep the first non-sensor class. Light does not replace sensor."""
        override = _active_profile.device_class_for(homey_device, _current_entity)
        if override is not None:
            device_class = override
        current = homey_device.get("class")
        if current == "sensor" and device_class == "light":
            return
        if current and current != "sensor":
            return
        homey_device["class"] = device_class


def _get_mappers() -> dict[type[EntityInfo], _EntityMapper]:
    """Build the type-to-mapper registry once.

    Late imports avoid circular dependencies with per-entity mapper modules.
    """
    global _MAPPERS
    if _MAPPERS is not None:
        return _MAPPERS

    from homey_esphomedriver.entities.mapping.alarm_control_panel import (
        AlarmControlPanelEntityMapper,
    )
    from homey_esphomedriver.entities.mapping.binary_sensor import (
        BinarySensorEntityMapper,
    )
    from homey_esphomedriver.entities.mapping.button import (
        ButtonEntityMapper,
    )
    from homey_esphomedriver.entities.mapping.climate import (
        ClimateEntityMapper,
    )
    from homey_esphomedriver.entities.mapping.cover import (
        CoverEntityMapper,
    )
    from homey_esphomedriver.entities.mapping.event import (
        EventEntityMapper,
    )
    from homey_esphomedriver.entities.mapping.fan import (
        FanEntityMapper,
    )
    from homey_esphomedriver.entities.mapping.light import (
        LightEntityMapper,
    )
    from homey_esphomedriver.entities.mapping.lock import (
        LockEntityMapper,
    )
    from homey_esphomedriver.entities.mapping.media_player import (
        MediaPlayerEntityMapper,
    )
    from homey_esphomedriver.entities.mapping.number import (
        NumberEntityMapper,
    )
    from homey_esphomedriver.entities.mapping.select import (
        SelectEntityMapper,
    )
    from homey_esphomedriver.entities.mapping.sensor import (
        SensorEntityMapper,
    )
    from homey_esphomedriver.entities.mapping.siren import (
        SirenEntityMapper,
    )
    from homey_esphomedriver.entities.mapping.switch import (
        SwitchEntityMapper,
    )
    from homey_esphomedriver.entities.mapping.text_sensor import (
        TextSensorEntityMapper,
    )
    from homey_esphomedriver.entities.mapping.valve import (
        ValveEntityMapper,
    )
    from homey_esphomedriver.entities.mapping.water_heater import (
        WaterHeaterEntityMapper,
    )

    _MAPPERS = {
        AlarmControlPanelInfo: AlarmControlPanelEntityMapper(),
        BinarySensorInfo: BinarySensorEntityMapper(),
        ButtonInfo: ButtonEntityMapper(),
        ClimateInfo: ClimateEntityMapper(),
        CoverInfo: CoverEntityMapper(),
        EventInfo: EventEntityMapper(),
        FanInfo: FanEntityMapper(),
        LightInfo: LightEntityMapper(),
        LockInfo: LockEntityMapper(),
        MediaPlayerInfo: MediaPlayerEntityMapper(),
        NumberInfo: NumberEntityMapper(),
        SelectInfo: SelectEntityMapper(),
        SensorInfo: SensorEntityMapper(),
        SirenInfo: SirenEntityMapper(),
        SwitchInfo: SwitchEntityMapper(),
        TextSensorInfo: TextSensorEntityMapper(),
        ValveInfo: ValveEntityMapper(),
        WaterHeaterInfo: WaterHeaterEntityMapper(),
    }
    return _MAPPERS
