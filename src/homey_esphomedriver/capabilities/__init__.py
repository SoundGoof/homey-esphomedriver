"""Homey capability bookkeeping for a live ESPHome device.

Owns add/remove/refresh of capabilities and category toggles so
:class:`~homey_esphomedriver.esphome_device.EspHomeDevice` stays focused on
lifecycle and session wiring.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from aioesphomeapi import EntityCategory, EntityInfo

from homey_esphomedriver.capabilities.refresh import plan_capability_refresh
from homey_esphomedriver.entities.mapping import (
    REFRESH_CAPABILITY,
    REFRESH_CAPABILITY_OPTIONS,
    DeviceEntityMapper,
)

if TYPE_CHECKING:
    from homey_esphomedriver.esphome_device import EspHomeDevice


class DeviceCapabilityHandler:
    """Add, remove, and refresh Homey capabilities on a live device."""

    def __init__(self, device: EspHomeDevice) -> None:
        self._device = device

    async def ensure(self) -> None:
        """Register refresh and command listeners; add refresh on older pairings."""
        if not self._device.has_capability(REFRESH_CAPABILITY):
            await self._add_capabilities(
                [REFRESH_CAPABILITY],
                {REFRESH_CAPABILITY: dict(REFRESH_CAPABILITY_OPTIONS)},
            )
        self._device.register_capability_listener(REFRESH_CAPABILITY, self.refresh)
        self._device._commands.register_listeners()

    async def refresh(self, _value: Any = True, **_kwargs: Any) -> None:
        """Add/remove capabilities from a live remap; keep unchanged Homey ids."""
        device = self._device
        entities, services = await device._require_client().list_entities_services()
        scratch = DeviceEntityMapper.empty_option()
        DeviceEntityMapper.map_device(
            entities,
            scratch,
            profile=device.brand_profile,
            diagnostics=device.get_setting("show_diagnostics"),
            configuration=device.get_setting("show_configuration"),
            services=services,
        )

        to_remove, to_add, to_update = plan_capability_refresh(
            device._capabilities_options, scratch["capabilitiesOptions"]
        )
        added = dict(to_add)
        # The mirror of the removal filter below: a capability whose options
        # Homey kept as null is planned as an update, but the capability itself
        # is gone. Setting options on one the device does not have leaves it
        # unregistered — and for a Flow marker that means its cards stay
        # missing. Add those instead of updating them.
        readd = {
            capability_id: options
            for capability_id, options in to_update
            if not device.has_capability(capability_id)
        }
        added.update(readd)
        to_update = [item for item in to_update if item[0] not in readd]
        # Homey keeps a null options entry after a capability is removed, so
        # the plan names it again on every later refresh. Only ask Homey to
        # remove what the device still has.
        await self._remove_capabilities(
            [
                capability_id
                for capability_id in to_remove
                if device.has_capability(capability_id)
            ]
        )
        await self._add_capabilities(list(added), added)
        for capability_id, options in to_update:
            await device.set_capability_options(capability_id, options)

        mapped_class = scratch.get("class")
        if mapped_class and device.get_setting("device_class") == "auto":
            await device.set_store_value("auto_class", mapped_class)
            await device.set_class(mapped_class)

        diagnostic, configuration = self._capabilities_by_category(entities)
        await device.set_store_value("diagnostic_capabilities", diagnostic)
        await device.set_store_value("configuration_capabilities", configuration)

        if to_remove or added or to_update:
            await device._state_handler.init()

    async def apply_category(
        self,
        enabled: bool,
        category: EntityCategory,
        store_key: str,
    ) -> None:
        """Add or remove capabilities for one entity category from the live device."""
        device = self._device
        if not enabled:
            # Batch — per-cap removeCapability reinitializes and times out
            # on entity-heavy nodes inside on_settings ACK.
            removed = device.get_store().get(store_key) or []
            await self._remove_capabilities(removed)
            await device.set_store_value(store_key, [])
            if removed:
                await device._state_handler.init()
            return

        entities, _services = await device._require_client().list_entities_services()
        existing = device.get_capabilities()
        scratch = DeviceEntityMapper.empty_option(list(existing))
        DeviceEntityMapper.map(
            entities,
            scratch,
            category_only=category,
            profile=device.brand_profile,
        )
        present = set(existing)
        added = [
            capability_id
            for capability_id in scratch["capabilities"]
            if capability_id not in present
        ]
        await self._add_capabilities(
            added,
            {
                capability_id: scratch["capabilitiesOptions"][capability_id]
                for capability_id in added
            },
        )
        await device.set_store_value(store_key, added)
        if added:
            await device._state_handler.init()

    async def _add_capabilities(
        self,
        capability_ids: list[str],
        options: dict[str, dict[str, Any]],
    ) -> None:
        """Batch-add capabilities and register listeners for the new ids."""
        if not capability_ids:
            return
        device = self._device
        await device._client_emit(
            "addCapabilities",
            data={"capabilityIds": capability_ids, "capabilitiesOptions": options},
        )
        device._capabilities.extend(capability_ids)
        device._capabilities_options.update(options)
        for capability_id in capability_ids:
            device._commands.register_listener_for_capability(capability_id)
        device._commands.register_multi_listeners(capability_ids)

    async def _remove_capabilities(self, capability_ids: Sequence[str]) -> None:
        """Batch-remove capabilities and drop their local state."""
        if not capability_ids:
            return
        device = self._device
        await device._client_emit(
            "removeCapabilities",
            data={"capabilityIds": capability_ids},
        )
        removed = set(capability_ids)
        device._capabilities = [
            capability_id
            for capability_id in device._capabilities
            if capability_id not in removed
        ]
        for capability_id in capability_ids:
            del device._capabilities_options[capability_id]
            device._state.pop(capability_id, None)

    def _capabilities_by_category(
        self, entities: list[EntityInfo]
    ) -> tuple[list[str], list[str]]:
        """Split current caps into diagnostic vs configuration by entity key."""
        diagnostic_keys: set[int] = set()
        configuration_keys: set[int] = set()
        for entity in entities:
            if entity.entity_category == EntityCategory.DIAGNOSTIC:
                diagnostic_keys.add(entity.key)
            elif entity.entity_category == EntityCategory.CONFIG:
                configuration_keys.add(entity.key)

        diagnostic: list[str] = []
        configuration: list[str] = []
        for capability_id, stored in self._device._capabilities_options.items():
            # WORKAROUND: Homey may persist options as null (Athom will return {}).
            key = (stored or {}).get("key")
            if key is None:
                continue
            key = int(key)
            if key in diagnostic_keys:
                diagnostic.append(capability_id)
            elif key in configuration_keys:
                configuration.append(capability_id)
        return diagnostic, configuration
