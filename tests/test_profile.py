"""BrandProfile parsing and filtering tests.

Brand apps declare an ``esphome`` object in ``driver.compose.json``. These tests
pin the compose key aliases, the project-acceptance rules that decide which
driver claims a node, and the capability override precedence.
"""

from __future__ import annotations

from typing import Any

import pytest
from aioesphomeapi import EntityInfo

from homey_esphomedriver.profile import (
    DEFAULT_BRAND_PROFILE,
    DEFAULT_CLIENT_INFO,
    BrandProfile,
)


def entity(object_id: str, key: int = 1) -> EntityInfo:
    return EntityInfo(object_id=object_id, key=key, name=object_id)


def test_from_manifest_without_esphome_key_returns_default() -> None:
    assert BrandProfile.from_manifest({"id": "my-driver"}) is DEFAULT_BRAND_PROFILE


@pytest.mark.parametrize("manifest", [None, {}, {"esphome": "not-a-mapping"}])
def test_from_manifest_falls_back_to_default(manifest: Any) -> None:
    assert BrandProfile.from_manifest(manifest) is DEFAULT_BRAND_PROFILE


def test_from_manifest_reads_nested_esphome_object() -> None:
    profile = BrandProfile.from_manifest({"esphome": {"projects": ["Brand.AQ-1"]}})
    assert profile.projects == frozenset({"Brand.AQ-1"})


def test_default_profile_accepts_every_project() -> None:
    """The generic io.esphome app omits projects, so nothing is filtered out."""
    assert DEFAULT_BRAND_PROFILE.accepts_project("Anything.At-All")
    assert DEFAULT_BRAND_PROFILE.accepts_discovery({})
    assert DEFAULT_BRAND_PROFILE.client_info == DEFAULT_CLIENT_INFO


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        ({"clientInfo": "Brand App"}, "Brand App"),
        ({"client_info": "Brand App"}, "Brand App"),
        ({}, DEFAULT_CLIENT_INFO),
    ],
)
def test_client_info_aliases(keys: dict[str, Any], expected: str) -> None:
    assert BrandProfile.from_compose(keys).client_info == expected


@pytest.mark.parametrize(
    "key", ["projects", "projectNames", "project_names"]
)
def test_projects_aliases(key: str) -> None:
    profile = BrandProfile.from_compose({key: ["Brand.AQ-1", "Brand.AQ-2"]})
    assert profile.projects == frozenset({"Brand.AQ-1", "Brand.AQ-2"})


@pytest.mark.parametrize(
    "key",
    ["projectPrefix", "projectPrefixes", "project_prefix", "project_prefixes"],
)
def test_project_prefix_aliases(key: str) -> None:
    profile = BrandProfile.from_compose({key: "Brand."})
    assert profile.project_prefix == ("Brand.",)


@pytest.mark.parametrize(
    "key",
    ["hiddenEntities", "hidden_entities", "skipObjectIds", "skip_object_ids"],
)
def test_hidden_entities_aliases(key: str) -> None:
    profile = BrandProfile.from_compose({key: ["status_led"]})
    assert profile.skip_entity(entity("status_led")) is True
    assert profile.skip_entity(entity("relay")) is False


def test_scalar_string_is_accepted_where_a_list_is_expected() -> None:
    """Compose authors write a bare string as often as a list."""
    profile = BrandProfile.from_compose({"projects": "Brand.AQ-1"})
    assert profile.projects == frozenset({"Brand.AQ-1"})


def test_accepts_project_by_exact_name() -> None:
    profile = BrandProfile.from_compose({"projects": ["Brand.AQ-1"]})
    assert profile.accepts_project("Brand.AQ-1") is True
    assert profile.accepts_project("Brand.AQ-2") is False


def test_accepts_project_by_prefix() -> None:
    profile = BrandProfile.from_compose({"projectPrefix": ["Brand."]})
    assert profile.accepts_project("Brand.AQ-9") is True
    assert profile.accepts_project("Other.AQ-9") is False


def test_projects_and_prefix_are_ored() -> None:
    profile = BrandProfile.from_compose(
        {"projects": ["Exact.One"], "projectPrefix": ["Brand."]}
    )
    assert profile.accepts_project("Exact.One") is True
    assert profile.accepts_project("Brand.Whatever") is True
    assert profile.accepts_project("Nope.Nope") is False


def test_empty_projects_list_accepts_nothing() -> None:
    """An explicit empty list is a filter, unlike an absent key."""
    profile = BrandProfile.from_compose({"projects": []})
    assert profile.projects == frozenset()
    assert profile.accepts_project("Brand.AQ-1") is False


def test_accepts_discovery_reads_txt_project_name() -> None:
    profile = BrandProfile.from_compose({"projects": ["Brand.AQ-1"]})
    assert profile.accepts_discovery({"project_name": "Brand.AQ-1"}) is True
    assert profile.accepts_discovery({"project_name": "Other"}) is False


def test_accepts_discovery_without_project_name_is_rejected_when_filtered() -> None:
    """A node advertising no project cannot satisfy a projects filter."""
    profile = BrandProfile.from_compose({"projects": ["Brand.AQ-1"]})
    assert profile.accepts_discovery({}) is False


def test_capability_id_for_returns_default_when_unmapped() -> None:
    assert DEFAULT_BRAND_PROFILE.capability_id_for(entity("relay"), "onoff") == "onoff"


def test_capability_id_for_uses_device_entities() -> None:
    profile = BrandProfile.from_compose({"deviceEntities": {"relay": "onoff.pump"}})
    assert profile.capability_id_for(entity("relay"), "onoff") == "onoff.pump"


def test_capability_overrides_take_precedence_over_device_entities() -> None:
    """The (object_id, default) pair is more specific, so it wins."""
    profile = BrandProfile(
        device_entities={"relay": "onoff.generic"},
        capability_overrides={("relay", "onoff"): "onoff.specific"},
    )
    assert profile.capability_id_for(entity("relay"), "onoff") == "onoff.specific"


def test_capability_overrides_fall_through_on_different_default() -> None:
    profile = BrandProfile(
        device_entities={"relay": "onoff.generic"},
        capability_overrides={("relay", "button"): "onoff.specific"},
    )
    assert profile.capability_id_for(entity("relay"), "onoff") == "onoff.generic"


def test_capability_overrides_parses_nested_compose_mapping() -> None:
    profile = BrandProfile.from_compose(
        {"capabilityOverrides": {"relay": {"onoff": "onoff.pump"}}}
    )
    assert profile.capability_overrides == {("relay", "onoff"): "onoff.pump"}


def test_capability_overrides_skips_malformed_entries() -> None:
    profile = BrandProfile.from_compose({"capabilityOverrides": {"relay": "not-a-map"}})
    assert profile.capability_overrides == {}


def test_device_class_for_returns_none_without_entity() -> None:
    profile = BrandProfile.from_compose({"deviceClassOverrides": {"relay": "socket"}})
    assert profile.device_class_for(None, None) is None
    assert profile.device_class_for(None, entity("relay")) == "socket"
    assert profile.device_class_for(None, entity("other")) is None


def test_native_app_suggestion() -> None:
    profile = BrandProfile.from_compose({"nativeApps": {"Brand.AQ-1": "Brand"}})
    assert profile.native_app_suggestion("Brand.AQ-1") == "Brand"
    assert profile.native_app_suggestion("Unknown") is None


def test_replace_returns_a_modified_copy() -> None:
    profile = BrandProfile.from_compose({"clientInfo": "Original"})
    updated = profile.replace(client_info="Changed")
    assert profile.client_info == "Original"
    assert updated.client_info == "Changed"


def test_after_map_default_is_a_noop() -> None:
    assert DEFAULT_BRAND_PROFILE.after_map([entity("relay")], None) is None
