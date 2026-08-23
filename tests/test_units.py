"""Unit conversion tests.

ESPHome states carry no unit, so the pair-time ``esphome_unit`` stored in
capability options is the only source of truth at runtime. These tests pin the
conversion table and the capability-to-converter lookup.
"""

from __future__ import annotations

from typing import Any

import pytest

from homey_esphomedriver.units import (
    convert_absolute_humidity,
    convert_apparent_power,
    convert_area,
    convert_blood_glucose,
    convert_co,
    convert_conductivity,
    convert_current,
    convert_data_rate,
    convert_data_size,
    convert_distance,
    convert_duration,
    convert_energy,
    convert_energy_distance,
    convert_frequency,
    convert_irradiance,
    convert_o3,
    convert_power,
    convert_pressure,
    convert_rain,
    convert_rain_intensity,
    convert_reactive_energy,
    convert_reactive_power,
    convert_so2,
    convert_speed_kmh,
    convert_speed_ms,
    convert_temperature,
    convert_temperature_from_celsius,
    convert_units,
    convert_voltage,
    convert_volume_l,
    convert_volume_m3,
    convert_water_flow,
    convert_weight,
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


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [("A", 2.0, 2.0), ("mA", 1500.0, 1.5)],
)
def test_convert_current(unit: str, value: float, expected: float) -> None:
    assert convert_current(unit, value) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [("Hz", 50.0, 50.0), ("kHz", 1.0, 1_000.0), ("MHz", 1.0, 1_000_000.0)],
)
def test_convert_frequency(unit: str, value: float, expected: float) -> None:
    assert convert_frequency(unit, value) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [("m", 3.0, 3.0), ("km", 1.0, 1_000.0), ("in", 1.0, 0.0254)],
)
def test_convert_distance(unit: str, value: float, expected: float) -> None:
    assert convert_distance(unit, value) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [("g", 12.0, 12.0), ("kg", 1.0, 1_000.0), ("lb", 1.0, 453.592)],
)
def test_convert_weight(unit: str, value: float, expected: float) -> None:
    assert convert_weight(unit, value) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [("mm", 12.0, 12.0), ("cm", 1.0, 10.0), ("in", 1.0, 25.4)],
)
def test_convert_rain(unit: str, value: float, expected: float) -> None:
    assert convert_rain(unit, value) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [("mm/h", 3.0, 3.0), ("mm/d", 24.0, 1.0), ("in/h", 1.0, 25.4)],
)
def test_convert_rain_intensity(unit: str, value: float, expected: float) -> None:
    assert convert_rain_intensity(unit, value) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [("m³", 1.0, 1.0), ("L", 1_000.0, 1.0), ("gal", 1.0, 0.00378541)],
)
def test_convert_volume_m3(unit: str, value: float, expected: float) -> None:
    assert convert_volume_m3(unit, value) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [("L/min", 2.0, 2.0), ("L/s", 1.0, 60.0), ("gal/min", 1.0, 3.785411784)],
)
def test_convert_water_flow(unit: str, value: float, expected: float) -> None:
    assert convert_water_flow(unit, value) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [("ppm", 9.0, 9.0), ("ppb", 1_000.0, 1.0)],
)
def test_convert_co(unit: str, value: float, expected: float) -> None:
    assert convert_co(unit, value) == pytest.approx(expected)


def test_convert_o3_and_so2_use_gas_specific_ppb_factors() -> None:
    """ppb → μg/m³ uses 1.96 for O₃ and 2.62 for SO₂."""
    assert convert_o3("ppb", 1.0) == pytest.approx(1.96)
    assert convert_so2("ppm", 1.0) == pytest.approx(2620.0)


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [("g/m³", 8.0, 8.0), ("mg/m³", 1500.0, 1.5)],
)
def test_convert_absolute_humidity(unit: str, value: float, expected: float) -> None:
    assert convert_absolute_humidity(unit, value) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [("VA", 12.0, 12.0), ("kVA", 1.5, 1500.0)],
)
def test_convert_apparent_power(unit: str, value: float, expected: float) -> None:
    assert convert_apparent_power(unit, value) == pytest.approx(expected)


def test_convert_area() -> None:
    assert convert_area("m²", 4.0) == 4.0
    assert convert_area("km²", 1.0) == pytest.approx(1_000_000.0)


def test_convert_blood_glucose() -> None:
    assert convert_blood_glucose("mg/dL", 90.0) == 90.0
    assert convert_blood_glucose("mmol/L", 1.0) == pytest.approx(18.0182)


def test_convert_conductivity() -> None:
    assert convert_conductivity("µS/cm", 400.0) == 400.0
    assert convert_conductivity("mS/cm", 1.0) == pytest.approx(1_000.0)


def test_convert_energy_distance() -> None:
    assert convert_energy_distance("kWh/100km", 15.0) == 15.0
    assert convert_energy_distance("Wh/km", 10.0) == pytest.approx(1.0)


def test_convert_irradiance() -> None:
    assert convert_irradiance("W/m²", 800.0) == 800.0
    assert convert_irradiance("BTU/(h⋅ft²)", 1.0) == pytest.approx(3.154591)


def test_convert_reactive_power_and_energy() -> None:
    assert convert_reactive_power("var", 12.0) == 12.0
    assert convert_reactive_power("kvar", 2.0) == pytest.approx(2_000.0)
    assert convert_reactive_energy("kvarh", 2.0) == pytest.approx(2_000.0)


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


class TestImportableOffDevice:
    """The `homey` package only exists on a Homey.

    `units`, `entities.state` and `entities.state.base` annotate with
    `homey.device.Device` but must not import it at runtime, or none of them
    can be imported off-device — including by these tests. Nothing else here
    imports `state`, so without this a re-added module-level import would keep
    the suite green while breaking every consumer outside the Homey runtime.
    """

    @pytest.mark.parametrize(
        "module",
        [
            "homey_esphomedriver.units",
            "homey_esphomedriver.entities.state",
            "homey_esphomedriver.entities.state.base",
        ],
    )
    def test_module_imports_without_the_homey_runtime(self, module: str) -> None:
        import importlib

        assert importlib.import_module(module) is not None
