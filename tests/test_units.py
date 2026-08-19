"""Unit conversion tests.

ESPHome states carry no unit, so the pair-time ``esphome_unit`` stored in
capability options is the only source of truth at runtime. These tests pin the
conversion table and the capability-to-converter lookup.
"""

from __future__ import annotations

from typing import Any

import pytest

from homey_esphomedriver.units import (
    convert_data_rate,
    convert_data_size,
    convert_duration,
    convert_energy,
    convert_power,
    convert_pressure,
    convert_speed_kmh,
    convert_speed_ms,
    convert_temperature,
    convert_temperature_from_celsius,
    convert_units,
    convert_voltage,
    convert_volume_l,
    kelvin_to_mireds,
    mireds_to_kelvin,
)


class FakeDevice:
    """Minimal stand-in exposing only what :func:`convert_units` reads."""

    def __init__(self, options: dict[str, dict[str, Any]] | None = None) -> None:
        self._options = options or {}

    def get_capability_options(self, capability_id: str) -> dict[str, Any]:
        return self._options.get(capability_id, {})


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [
        ("°C", 21.0, 21.0),
        ("K", 273.15, 0.0),
        ("°K", 293.15, 20.0),
        ("°F", 32.0, 0.0),
        ("F", 212.0, 100.0),
        ("unknown", 5.0, 5.0),
    ],
)
def test_convert_temperature(unit: str, value: float, expected: float) -> None:
    assert convert_temperature(unit, value) == pytest.approx(expected)


@pytest.mark.parametrize("unit", ["K", "°K", "°F", "F", "°C"])
def test_temperature_round_trip(unit: str) -> None:
    """Celsius -> entity unit -> Celsius must be lossless."""
    celsius = 21.5
    native = convert_temperature_from_celsius(unit, celsius)
    assert convert_temperature(unit, native) == pytest.approx(celsius)


def test_mireds_kelvin_round_trip() -> None:
    assert mireds_to_kelvin(250) == pytest.approx(4000)
    assert kelvin_to_mireds(4000) == pytest.approx(250)
    assert mireds_to_kelvin(kelvin_to_mireds(2700)) == pytest.approx(2700)


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [
        ("mW", 1500.0, 1.5),
        ("W", 42.0, 42.0),
        ("kW", 1.5, 1500.0),
        ("MW", 1.0, 1_000_000.0),
        ("BTU/h", 1.0, 0.2930710702),
    ],
)
def test_convert_power(unit: str, value: float, expected: float) -> None:
    assert convert_power(unit, value) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [
        ("Wh", 1000.0, 1.0),
        ("kWh", 2.5, 2.5),
        ("MWh", 1.0, 1000.0),
        ("MJ", 3.6, 1.0),
        ("J", 3_600_000.0, 1.0),
        ("kJ", 3_600.0, 1.0),
        ("GJ", 0.0036, 1.0),
    ],
)
def test_convert_energy(unit: str, value: float, expected: float) -> None:
    assert convert_energy(unit, value) == pytest.approx(expected)


def test_convert_energy_delegates_recursively() -> None:
    """J/kJ/GJ route through MJ; cal/kcal/Gcal route through Mcal."""
    assert convert_energy("cal", 1_000_000.0) == pytest.approx(1.162222)
    assert convert_energy("kcal", 1_000.0) == pytest.approx(1.162222)
    assert convert_energy("Gcal", 0.001) == pytest.approx(1.162222)


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [("mV", 3300.0, 3.3), ("V", 5.0, 5.0), ("kV", 1.0, 1000.0)],
)
def test_convert_voltage(unit: str, value: float, expected: float) -> None:
    assert convert_voltage(unit, value) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [("hPa", 1013.0, 1013.0), ("Pa", 101300.0, 1013.0), ("bar", 1.0, 1000.0)],
)
def test_convert_pressure(unit: str, value: float, expected: float) -> None:
    assert convert_pressure(unit, value) == pytest.approx(expected)


def test_convert_speed_kmh_shortcuts_native_unit() -> None:
    assert convert_speed_kmh("km/h", 12.0) == 12.0
    assert convert_speed_kmh("m/s", 10.0) == pytest.approx(36.0)
    assert convert_speed_ms("km/h", 36.0) == pytest.approx(10.0)


def test_convert_volume_l_shortcuts_before_m3_delegation() -> None:
    assert convert_volume_l("L", 3.0) == 3.0
    assert convert_volume_l("mL", 1500.0) == pytest.approx(1.5)
    assert convert_volume_l("gal", 1.0) == pytest.approx(3.78541)
    assert convert_volume_l("m³", 1.0) == pytest.approx(1000.0)


def test_convert_duration_and_data() -> None:
    assert convert_duration("h", 2.0) == pytest.approx(7200.0)
    assert convert_duration("ms", 1500.0) == pytest.approx(1.5)
    assert convert_data_rate("kB/s", 1.0) == pytest.approx(8000.0)
    assert convert_data_size("KiB", 1.0) == pytest.approx(1024.0)


def test_convert_units_applies_stored_unit() -> None:
    device = FakeDevice({"measure_temperature": {"esphome_unit": "°F"}})
    assert convert_units(device, "measure_temperature", 212.0) == pytest.approx(100.0)


def test_convert_units_uses_base_capability_for_indexed_ids() -> None:
    """``measure_temperature.1`` must resolve the same converter as the base id."""
    device = FakeDevice({"measure_temperature.1": {"esphome_unit": "K"}})
    assert convert_units(device, "measure_temperature.1", 273.15) == pytest.approx(0.0)


def test_convert_units_passes_through_without_stored_unit() -> None:
    device = FakeDevice({"measure_temperature": {}})
    assert convert_units(device, "measure_temperature", 99.0) == 99.0


def test_convert_units_passes_through_unknown_capability() -> None:
    """A capability with no converter keeps its value even with a unit stored."""
    device = FakeDevice({"esphome_number.thing": {"esphome_unit": "widgets"}})
    assert convert_units(device, "esphome_number.thing", 7.0) == 7.0


@pytest.mark.parametrize("value", ["on", None, "", "12.5"])
def test_convert_units_passes_through_non_numeric(value: Any) -> None:
    """Strings are never coerced: a numeric-looking string stays a string."""
    device = FakeDevice({"measure_power": {"esphome_unit": "kW"}})
    result = convert_units(device, "measure_power", value)
    assert result is value


def test_convert_units_accepts_ints_as_numeric() -> None:
    device = FakeDevice({"measure_power": {"esphome_unit": "kW"}})
    assert convert_units(device, "measure_power", 2) == pytest.approx(2000.0)
