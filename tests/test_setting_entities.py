"""Settings-page fields backed by an ESPHome entity.

`settingEntities` maps a Homey settings key onto an entity object id, because
Homey settings are declared statically per driver and cannot be generated per
device at pair time. These tests pin the two halves that are easy to get wrong:
how a mapped entity domain is written, and when a reported value counts as a
mismatch worth writing.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from homey_esphomedriver.entities.commands.generic import (
    WRITE_COMMANDS,
    write_entity_value,
)
from homey_esphomedriver.settings import setting_matches


@pytest.mark.parametrize(
    ("wanted", "reported"),
    [
        (21.0, 21.0),
        (21.0, 21.0000001),
        ("heat", "heat"),
        (True, True),
        (False, False),
    ],
)
def test_matching_values_are_not_rewritten(wanted: Any, reported: Any) -> None:
    """The node is authoritative for what it holds; only a real drift writes."""
    assert setting_matches(wanted, reported) is True


@pytest.mark.parametrize(
    ("wanted", "reported"),
    [
        (21.0, 22.0),
        (21.0, 21.1),
        ("heat", "cool"),
        (True, False),
        ("1", "1.0"),
    ],
)
def test_differing_values_are_a_mismatch(wanted: Any, reported: Any) -> None:
    assert setting_matches(wanted, reported) is False


def test_a_select_option_is_compared_as_text() -> None:
    """Options that only look numeric must not be forced through ``float``."""
    assert setting_matches("01", "1") is False
    assert setting_matches("auto", "auto") is True


def test_a_float_against_a_placeholder_is_a_mismatch_not_an_error() -> None:
    assert setting_matches(21.0, "") is False
    assert setting_matches(21.0, None) is False


def test_each_writable_domain_has_its_own_command() -> None:
    """A mapping names an object id, so the entity domain is whatever the YAML
    author chose. Writing every one of them as a number sends a dropdown a
    float it rejects and a switch a value it ignores.
    """
    assert WRITE_COMMANDS["number"][0] == "number_command"
    assert WRITE_COMMANDS["select"][0] == "select_command"
    assert WRITE_COMMANDS["switch"][0] == "switch_command"

    assert WRITE_COMMANDS["number"][1]("21.5") == 21.5
    assert WRITE_COMMANDS["select"][1](21) == "21"
    assert WRITE_COMMANDS["switch"][1](False) is False


def test_write_entity_value_sends_the_coerced_state() -> None:
    client = MagicMock()
    write_entity_value(client, "number", 7, "21.5")
    client.command.assert_called_once_with("number_command", 7, 21.5)


def test_write_entity_value_rejects_an_unwritable_domain() -> None:
    with pytest.raises(KeyError):
        write_entity_value(MagicMock(), "sensor", 7, 1.0)
