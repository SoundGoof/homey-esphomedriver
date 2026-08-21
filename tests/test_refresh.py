"""Refresh-capability planning tests.

``plan_capability_refresh`` diffs by ``(entity key, capability base)`` so
remapping cannot reshuffle Flow cards bound to bare Homey ids. Caps without a
``key`` match on Homey id.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from aioesphomeapi import BinarySensorInfo, EntityCategory

from homey_esphomedriver.capabilities import DeviceCapabilityHandler
from homey_esphomedriver.capabilities.refresh import (
    capability_base,
    plan_capability_refresh,
)
from homey_esphomedriver.entities.commands import DeviceEntityCommandHandler
from homey_esphomedriver.entities.state import DeviceEntityStateHandler


def test_capability_base() -> None:
    assert capability_base("onoff") == "onoff"
    assert capability_base("measure_temperature.1") == "measure_temperature"
    assert capability_base("esphome_number.pump") == "esphome_number"


def test_plan_keeps_current_id_when_identity_matches() -> None:
    """Matched (key, base) pairs keep the current Homey id, not the scratch id."""
    current = {"onoff": {"key": 1, "title": "Relay"}}
    desired = {"onoff.relay": {"key": 1, "title": "Relay"}}
    to_remove, to_add, to_update = plan_capability_refresh(current, desired)
    assert to_remove == []
    assert to_add == []
    assert to_update == []


def test_plan_updates_when_options_change() -> None:
    current = {"onoff": {"key": 1, "title": "Old"}}
    desired = {"onoff": {"key": 1, "title": "New"}}
    to_remove, to_add, to_update = plan_capability_refresh(current, desired)
    assert to_remove == []
    assert to_add == []
    assert to_update == [("onoff", {"key": 1, "title": "New"})]


def test_plan_removes_gone_entities_and_adds_new_ones() -> None:
    current = {"onoff": {"key": 1}, "measure_temperature": {"key": 2}}
    desired = {"measure_temperature": {"key": 2}, "onoff.pump": {"key": 3}}
    to_remove, to_add, to_update = plan_capability_refresh(current, desired)
    assert to_remove == ["onoff"]
    assert to_update == []
    assert to_add == [("onoff.pump", {"key": 3})]


def test_plan_indexes_scratch_when_bare_id_already_taken() -> None:
    """A new entity whose scratch id is already taken gets ``base.key``."""
    current = {"onoff": {"key": 1}}
    desired: dict[str, dict[str, Any]] = {
        "onoff": {"key": 2},
        "onoff.relay": {"key": 1},
    }
    to_remove, to_add, to_update = plan_capability_refresh(current, desired)
    assert to_remove == []
    assert to_update == []
    assert to_add == [("onoff.2", {"key": 2})]


def test_plan_adds_missing_flow_filter_marker() -> None:
    current = {
        "button.refresh": {},
        "button.play": {"key": 1},
    }
    desired = {
        "button.refresh": {},
        "esphome_button": {"uiComponent": None},
        "button.play": {"key": 1},
    }
    to_remove, to_add, to_update = plan_capability_refresh(current, desired)
    assert to_remove == []
    assert to_update == []
    assert to_add == [("esphome_button", {"uiComponent": None})]


def test_plan_removes_obsolete_flow_filter_marker() -> None:
    current = {
        "button.refresh": {},
        "esphome_boolean": {"uiComponent": None},
        "onoff": {"key": 1},
    }
    desired = {
        "button.refresh": {},
        "onoff": {"key": 1},
    }
    to_remove, to_add, to_update = plan_capability_refresh(current, desired)
    assert to_remove == ["esphome_boolean"]
    assert to_add == []
    assert to_update == []


def test_plan_survives_capability_options_stored_as_null() -> None:
    """A stored ``None`` is removed when the desired set does not name it."""
    current: dict[str, Any] = {
        "onoff": {"key": 1},
        "esphome_string.old_slot": None,
        "esphome_boolean": {"uiComponent": None},
    }
    desired: dict[str, Any] = {
        "onoff": {"key": 1},
        "esphome_boolean": {"uiComponent": None},
    }

    to_remove, to_add, to_update = plan_capability_refresh(current, desired)

    assert to_remove == ["esphome_string.old_slot"]
    assert to_add == []
    assert to_update == []


def test_plan_keeps_a_null_options_capability_the_node_still_has() -> None:
    """A null entry the desired set still names is filled in, not removed."""
    current: dict[str, Any] = {"esphome_boolean": None}
    desired: dict[str, Any] = {"esphome_boolean": {"uiComponent": None}}

    to_remove, to_add, to_update = plan_capability_refresh(current, desired)

    assert to_remove == []
    assert to_update == [("esphome_boolean", {"uiComponent": None})]


def test_category_split_survives_null_capability_options() -> None:
    """Category split skips null options instead of AttributeError on ``.get``."""
    handler = DeviceCapabilityHandler.__new__(DeviceCapabilityHandler)
    handler._device = SimpleNamespace(
        _capabilities_options={
            "measure_temperature": {"key": 1},
            "esphome_string.broken": None,
            "onoff": {"key": 2},
        }
    )
    entities = [
        BinarySensorInfo(
            object_id="status",
            key=1,
            name="Status",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        BinarySensorInfo(
            object_id="relay",
            key=2,
            name="Relay",
            entity_category=EntityCategory.CONFIG,
        ),
    ]

    diagnostic, configuration = handler._capabilities_by_category(entities)

    assert diagnostic == ["measure_temperature"]
    assert configuration == ["onoff"]


def test_command_resolve_survives_null_capability_options() -> None:
    """Resolve reads null options as ``{}`` instead of raising via the SDK."""
    commands = DeviceEntityCommandHandler.__new__(DeviceEntityCommandHandler)
    commands._device = SimpleNamespace(
        _capabilities_options={
            "button.refresh": None,
            "button.play": {"key": 1},
            "esphome_number.level": None,
        }
    )
    stub = object()
    commands._handlers = {
        "button": (stub, "press"),
        "number": (stub, "set_value"),
    }

    assert commands._resolve("button.refresh") is None
    assert commands._resolve("button.play") == (stub, "press")
    assert commands._resolve("esphome_number.level") is None


def test_state_index_survives_null_capability_options() -> None:
    """State init indexes caps with a key and skips null options as empty."""
    state = DeviceEntityStateHandler.__new__(DeviceEntityStateHandler)
    state._device = SimpleNamespace(
        get_capabilities=lambda: [
            "onoff",
            "esphome_string.broken",
            "measure_temperature",
        ],
        get_capability_value=lambda _cap: None,
        _capabilities_options={
            "onoff": {"key": 1},
            "esphome_string.broken": None,
            "measure_temperature": {"key": 2},
        },
    )
    state._key_to_capabilities = {}
    state._last_states = {}
    state._handlers_by_type = {}

    asyncio.run(state.init())

    assert state._key_to_capabilities == {1: ["onoff"], 2: ["measure_temperature"]}


def test_markers_survive_a_refresh_when_the_desired_set_includes_them() -> None:
    """A live refresh must not plan away the Flow ``$filter`` markers.

    Markers carry no ``key``, so the planner matches them by id: any marker
    missing from the desired set is removed, which unregisters every card
    filtered on it. `_refresh_capabilities` therefore has to add them to the
    scratch itself — `map_device` only knows about entities.
    """
    current: dict[str, dict[str, Any]] = {
        "measure_temperature": {"key": 111},
        "esphome_string.homey_t_headline": {"key": 222},
        "esphome_string": {"uiComponent": None},
        "esphome_display": {"uiComponent": None},
        "esphome_action": {"uiComponent": None},
    }
    desired = dict(current)

    to_remove, to_add, _to_update = plan_capability_refresh(current, desired)

    assert to_remove == []
    assert to_add == []


def test_markers_left_out_of_the_desired_set_are_removed() -> None:
    """Pins why the scratch must carry them: otherwise they are planned away."""
    current: dict[str, dict[str, Any]] = {
        "esphome_string": {"uiComponent": None},
        "esphome_display": {"uiComponent": None},
        "esphome_action": {"uiComponent": None},
    }
    desired: dict[str, dict[str, Any]] = {"esphome_string": {"uiComponent": None}}

    to_remove, _to_add, _to_update = plan_capability_refresh(current, desired)

    assert sorted(to_remove) == ["esphome_action", "esphome_display"]
