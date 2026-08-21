"""Brand-specific filters and remaps for a Homey ESPHome driver.

Brand apps declare an ``esphome`` object in ``driver.compose.json``.
:class:`EspHomeDriver` reads it from ``self.manifest``. A class-level
``brand_profile`` on the driver still wins when set — use that for
``after_map`` or a Python-only table.

The generic esphome-homey app (``io.esphome``) omits ``projects`` so every
project is accepted. Brand apps that ship one driver per SKU set ``projects``
(exact ESPHome ``project.name``); brand-wide or multi-variant drivers can use
``projectPrefix``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from aioesphomeapi import EntityInfo

from homey_esphomedriver.display_slots import DisplaySlots
from homey_esphomedriver.esphome_types import HomeyEspHomeDeviceOption

DEFAULT_CLIENT_INFO = "Homey ESPHome"
"""Identifies the generic Homey ESPHome app on the node's API hello."""


@dataclass(frozen=True, slots=True)
class BrandProfile:
    """
    Override surface consulted at pair, map, and connect time.

    Built from the driver manifest ``esphome`` object, or assigned as a
    class-level ``brand_profile`` when ``after_map`` needs Python.
    """

    client_info: str = DEFAULT_CLIENT_INFO
    """Name shown on the node for this Homey client."""

    projects: frozenset[str] | None = None
    """Exact ESPHome ``project.name`` values this driver accepts."""

    project_prefix: tuple[str, ...] | None = None
    """Prefixes matched with ``startswith``.

    ``None`` with :attr:`projects` unset accepts every project.
    """

    hidden_entities: frozenset[str] = field(default_factory=frozenset)
    """Extra entity object ids to omit, on top of diagnostic/config defaults."""

    device_entities: Mapping[str, str] = field(default_factory=dict)
    """Entity object id to Homey capability id."""

    device_class_overrides: Mapping[str, str] = field(default_factory=dict)
    """Entity object id to Homey device class."""

    native_apps: Mapping[str, str] = field(default_factory=dict)
    """ESPHome ``project.name`` to Homey App Store name (``io.esphome`` only)."""

    capability_overrides: Mapping[tuple[str, str], str] = field(default_factory=dict)
    """``(object_id, default_capability_id)`` to capability id. Python-only."""

    setting_entities: Mapping[str, str] = field(default_factory=dict)
    """Homey settings key to ESPHome entity object id.

    Homey device settings are declared statically in ``driver.compose.json``;
    there is no API to add a field per device at pair time. A driver that ships
    for a known product can therefore declare a settings field and name the
    entity it writes to, and core keeps the two in step: writing the entity when
    the setting changes, and refreshing the setting when the node reports a new
    value. Suits configuration entities — a calibration offset belongs beside
    Host and Port rather than on the tile.
    """

    display_slots: DisplaySlots = field(default_factory=DisplaySlots)
    """Naming for the Homey display-slot convention.

    Defaults match the documented convention, so nodes built from the README
    need no compose configuration; the key exists so the convention is not
    hardcoded in core.
    """

    @classmethod
    def from_manifest(cls, manifest: Mapping[str, Any] | None) -> BrandProfile:
        """Build a profile from a Homey driver manifest's ``esphome`` object.

        Args:
            manifest: Driver manifest, typically ``self.manifest``.

        Returns:
            :data:`DEFAULT_BRAND_PROFILE` when ``esphome`` is missing.
        """
        if not manifest:
            return DEFAULT_BRAND_PROFILE
        raw = manifest.get("esphome")
        if not isinstance(raw, Mapping):
            return DEFAULT_BRAND_PROFILE
        return cls.from_compose(raw)

    @classmethod
    def from_compose(cls, data: Mapping[str, Any]) -> BrandProfile:
        """Build a profile from a ``driver.compose.json`` ``esphome`` object.

        Args:
            data: The ``esphome`` object. CamelCase compose keys are preferred;
                snake_case and a few legacy aliases are also accepted.
        """
        return cls(
            client_info=str(
                _pick(data, "clientInfo", "client_info") or DEFAULT_CLIENT_INFO
            ),
            projects=_optional_names(data, "projects", "projectNames", "project_names"),
            project_prefix=_optional_tuple(
                data,
                "projectPrefix",
                "projectPrefixes",
                "project_prefix",
                "project_prefixes",
            ),
            hidden_entities=frozenset(
                _string_list(
                    _pick(
                        data,
                        "hiddenEntities",
                        "hidden_entities",
                        "skipObjectIds",
                        "skip_object_ids",
                    )
                    or ()
                )
            ),
            device_entities=_string_map(
                _pick(
                    data,
                    "deviceEntities",
                    "device_entities",
                    "capabilityByObjectId",
                    "capability_by_object_id",
                )
                or {}
            ),
            device_class_overrides=_string_map(
                _pick(data, "deviceClassOverrides", "device_class_overrides") or {}
            ),
            native_apps=_string_map(_pick(data, "nativeApps", "native_apps") or {}),
            capability_overrides=_capability_overrides(
                _pick(data, "capabilityOverrides", "capability_overrides")
            ),
            setting_entities=_string_map(
                _pick(data, "settingEntities", "setting_entities") or {}
            ),
            display_slots=DisplaySlots.from_compose(
                _pick(data, "displaySlots", "display_slots")
            ),
        )

    def replace(self, **kwargs: object) -> BrandProfile:
        """Return a copy with the given fields overridden."""
        return replace(self, **kwargs)

    def accepts_project(self, project_name: str) -> bool:
        """Return whether this driver should pair or drive the given project.

        Args:
            project_name: ESPHome firmware ``project.name``.
        """
        names = self.projects
        prefixes = self.project_prefix
        if names is None and prefixes is None:
            return True
        if names is not None and project_name in names:
            return True
        if prefixes is not None and any(
            project_name.startswith(prefix) for prefix in prefixes
        ):
            return True
        return False

    def accepts_discovery(self, txt: Mapping[str, str]) -> bool:
        """Return whether mDNS TXT ``project_name`` is allowed for this driver."""
        if self.projects is None and self.project_prefix is None:
            return True
        return self.accepts_project(txt.get("project_name") or "")

    def native_app_suggestion(self, project_name: str) -> str | None:
        """Return a Homey App Store name when a dedicated app exists."""
        return self.native_apps.get(project_name)

    def skip_entity(self, entity: EntityInfo) -> bool:
        """Return whether the brand wants this entity omitted from the Homey device."""
        return entity.object_id in self.hidden_entities

    def capability_id_for(self, entity: EntityInfo, default: str) -> str:
        """Return a remapped capability id, or ``default`` when unset."""
        pair = self.capability_overrides.get((entity.object_id, default))
        if pair is not None:
            return pair
        return self.device_entities.get(entity.object_id, default)

    def device_class_for(
        self,
        homey_device: HomeyEspHomeDeviceOption,
        entity: EntityInfo | None,
    ) -> str | None:
        """Return a Homey class override for ``entity``.

        Args:
            homey_device: Mapped device so subclasses can inspect already-added
                capabilities. Unused by the default implementation.
            entity: Entity currently being mapped.

        Returns:
            Override class, or ``None`` to keep the mapper default.
        """
        del homey_device
        if entity is None:
            return None
        return self.device_class_overrides.get(entity.object_id)

    def after_map(
        self,
        entities: Sequence[EntityInfo],
        homey_device: HomeyEspHomeDeviceOption,
    ) -> None:
        """Hook after all entities are mapped.

        Override on a class-level ``brand_profile`` for brand-specific
        post-processing. The default implementation is a no-op.
        """
        del entities, homey_device


DEFAULT_BRAND_PROFILE = BrandProfile()
"""Profile that accepts every project and applies no remaps."""


def _pick(data: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [str(item) for item in value]
    return []


def _optional_names(data: Mapping[str, Any], *keys: str) -> frozenset[str] | None:
    raw = _pick(data, *keys)
    if raw is None:
        return None
    return frozenset(_string_list(raw))


def _optional_tuple(data: Mapping[str, Any], *keys: str) -> tuple[str, ...] | None:
    raw = _pick(data, *keys)
    if raw is None:
        return None
    return tuple(_string_list(raw))


def _string_map(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _capability_overrides(value: object) -> dict[tuple[str, str], str]:
    if not isinstance(value, Mapping):
        return {}
    parsed: dict[tuple[str, str], str] = {}
    for object_id, mapping in value.items():
        if not isinstance(mapping, Mapping):
            continue
        for default, capability_id in mapping.items():
            parsed[(str(object_id), str(default))] = str(capability_id)
    return parsed
