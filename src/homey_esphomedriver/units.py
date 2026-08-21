"""Normalize ESPHome units to Homey-expected bases.

Pairing stores the source unit in ``capabilitiesOptions.esphome_unit``. Runtime
applies these converters so Homey Energy and measure UI see consistent values.
Color temperature uses the same module for mireds to Kelvin.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homey.device import Device

UnitConverter = Callable[[str, float], float]


def convert_units(device: Device, capability_id: str, value: Any) -> Any:
    """Convert ``value`` using the unit stored on the capability options.

    Indexed caps (``measure_temperature.1``) use the same converter as the base
    capability. ESPHome states do not carry a unit, so the pair-time
    ``esphome_unit`` is the only source of truth.

    Args:
        device: Homey device holding capability options.
        capability_id: Capability whose ``esphome_unit`` should be applied.
        value: Raw ESPHome state value.

    Returns:
        Converted value, or ``value`` unchanged when it is not numeric or has
        no stored unit.
    """
    if not isinstance(value, (int, float)):
        return value

    stored_unit = device.get_capability_options(capability_id).get("esphome_unit")
    if not stored_unit:
        return value

    return _get_converter(capability_id)(str(stored_unit), float(value))


def convert_temperature(unit: str, value: float) -> float:
    """Normalize temperature to °C for Homey's measure_temperature."""
    match unit:
        case "K" | "°K":
            return value - 273.15
        case "°F" | "F":
            return ((value - 32) * 5) / 9
        case _:
            return value


def convert_temperature_from_celsius(unit: str, value: float) -> float:
    """Convert Homey °C back to the ESPHome climate entity unit."""
    match unit:
        case "K" | "°K":
            return value + 273.15
        case "°F" | "F":
            return (value * 9) / 5 + 32
        case _:
            return value


def mireds_to_kelvin(mireds: float) -> float:
    """Convert color temperature from mireds to Kelvin."""
    return 1_000_000 / mireds


def kelvin_to_mireds(kelvin: float) -> float:
    """Convert color temperature from Kelvin to mireds."""
    return 1_000_000 / kelvin


def convert_power(unit: str, value: float) -> float:
    """Normalize power to W for Homey's measure_power / Energy."""
    match unit:
        case "mW":
            return value / 1_000
        case "W":
            return value
        case "kW":
            return value * 1_000
        case "MW":
            return value * 1_000_000
        case "GW":
            return value * 1_000_000_000
        case "TW":
            return value * 1_000_000_000_000
        case "BTU/h":
            return value * 0.2930710702
        case _:
            return value


def convert_energy(unit: str, value: float) -> float:
    """Normalize energy to kWh for Homey's meter_power / Energy."""
    match unit:
        case "J":
            return convert_energy("MJ", value / 1_000_000)
        case "kJ":
            return convert_energy("MJ", value / 1_000)
        case "MJ":
            return value / 3.6
        case "GJ":
            return convert_energy("MJ", value * 1_000)
        case "mWh":
            return value / 1_000_000
        case "Wh":
            return value / 1_000
        case "kWh":
            return value
        case "MWh":
            return value * 1_000
        case "GWh":
            return value * 1_000_000
        case "TWh":
            return value * 1_000_000_000
        case "cal":
            return convert_energy("Mcal", value / 1_000_000)
        case "kcal":
            return convert_energy("Mcal", value / 1_000)
        case "Mcal":
            return value * 1.162222
        case "Gcal":
            return convert_energy("Mcal", value * 1_000)
        case _:
            return value


def convert_current(unit: str, value: float) -> float:
    """Normalize current to A."""
    match unit:
        case "A":
            return value
        case "mA":
            return value / 1_000
        case _:
            return value


def convert_voltage(unit: str, value: float) -> float:
    """Normalize voltage to V."""
    match unit:
        case "V":
            return value
        case "mV":
            return value / 1_000
        case "μV":
            return value / 1_000_000
        case "kV":
            return value * 1_000
        case "MV":
            return value * 1_000_000
        case _:
            return value


def convert_frequency(unit: str, value: float) -> float:
    """Normalize frequency to Hz."""
    match unit:
        case "Hz":
            return value
        case "kHz":
            return value * 1_000
        case "MHz":
            return value * 1_000_000
        case "GHz":
            return value * 1_000_000_000
        case _:
            return value


def convert_pressure(unit: str, value: float) -> float:
    """Normalize pressure to mbar (hPa-equivalent)."""
    match unit:
        case "mbar" | "hPa":
            return value
        case "cbar":
            return value * 10
        case "bar":
            return value * 1_000
        case "mPa":
            return value / 100_000
        case "Pa":
            return value / 100
        case "kPa":
            return value * 10
        case "inHg":
            return value * 33.8639
        case "psi":
            return value * 68.9476
        case "inH₂O":
            return value * 2.4884
        case _:
            return value


def convert_distance(unit: str, value: float) -> float:
    """Normalize distance to m."""
    match unit:
        case "mm":
            return value / 1_000
        case "cm":
            return value / 100
        case "m":
            return value
        case "km":
            return value * 1_000
        case "in":
            return value * 0.0254
        case "ft":
            return value * 0.3048
        case "yd":
            return value * 0.9144
        case "mi":
            return value * 1609.344
        case _:
            return value


def convert_weight(unit: str, value: float) -> float:
    """Normalize weight to g."""
    match unit:
        case "μg":
            return value / 1_000_000
        case "mg":
            return value / 1_000
        case "g":
            return value
        case "kg":
            return value * 1_000
        case "oz":
            return value * 28.3495
        case "lb":
            return value * 453.592
        case _:
            return value


def convert_rain(unit: str, value: float) -> float:
    """Normalize precipitation depth to mm."""
    match unit:
        case "cm":
            return value * 10
        case "mm":
            return value
        case "in":
            return value * 25.4
        case _:
            return value


def convert_rain_intensity(unit: str, value: float) -> float:
    """Normalize precipitation intensity to mm/h."""
    match unit:
        case "mm/d":
            return value / 24
        case "mm/h":
            return value
        case "in/d":
            return (value * 25.4) / 24
        case "in/h":
            return value * 25.4
        case _:
            return value


def convert_speed_ms(unit: str, value: float) -> float:
    """Normalize speed to m/s."""
    match unit:
        case "mm/d":
            return value / 86_400_000
        case "mm/h":
            return value / 3_600_000
        case "m/s":
            return value
        case "km/h":
            return value / 3.6
        case "mm/s":
            return value / 1_000
        case "in/d":
            return value / (141_732 * 24)
        case "in/h":
            return value / 141_732
        case "in/s":
            return value * 0.0254
        case "ft/s":
            return value / 3.281
        case "mph":
            return value / 2.237
        case "kn":
            return value * 0.5144
        case "Beaufort":
            return 0.836 * (value**1.5)
        case _:
            return value


def convert_speed_kmh(unit: str, value: float) -> float:
    """Normalize speed to km/h (Homey wind strength)."""
    if unit == "km/h":
        return value
    return convert_speed_ms(unit, value) * 3.6


def convert_volume_m3(unit: str, value: float) -> float:
    """Normalize volume to m³."""
    match unit:
        case "mL":
            return value / 1_000_000
        case "L":
            return value / 1_000
        case "m³":
            return value
        case "ft³":
            return value / 35.31469989
        case "CCF":
            return value * 2.832
        case "MCF":
            return value * 28.317
        case "gal":
            return value * 0.00378541
        case _:
            return value


def convert_volume_l(unit: str, value: float) -> float:
    """Normalize volume to L."""
    if unit == "L":
        return value
    if unit == "mL":
        return value / 1_000
    if unit == "gal":
        return value * 3.78541
    return convert_volume_m3(unit, value) * 1_000


def convert_water_flow(unit: str, value: float) -> float:
    """Normalize volume flow rate to L/min."""
    match unit:
        case "L/min":
            return value
        case "L/s":
            return value * 60
        case "L/h":
            return value / 60
        case "mL/s":
            return (value / 1_000) * 60
        case "m³/s":
            return value * 1_000 * 60
        case "m³/min":
            return value * 1_000
        case "m³/h":
            return value * (1_000 / 60)
        case "ft³/min":
            return value * 28.316846592
        case "gal/min":
            return value * 3.785411784
        case "gal/d":
            return (value * 3.785411784) / 1_440
        case _:
            return value


def convert_data_rate(unit: str, value: float) -> float:
    """Normalize data rate to bit/s (Homey ``b/s``)."""
    match unit:
        case "bit/s":
            return value
        case "kbit/s":
            return value * 1_000
        case "Mbit/s":
            return value * 1_000_000
        case "Gbit/s":
            return value * 1_000_000_000
        case "B/s":
            return value * 8
        case "kB/s":
            return value * 1_000 * 8
        case "MB/s":
            return value * 1_000_000 * 8
        case "GB/s":
            return value * 1_000_000_000 * 8
        case "KiB/s":
            return value * 1_024 * 8
        case "MiB/s":
            return value * 1_024 * 1_024 * 8
        case "GiB/s":
            return value * 1_024 * 1_024 * 1_024 * 8
        case _:
            return value


def convert_data_size(unit: str, value: float) -> float:
    """Normalize data size to bytes."""
    match unit:
        case "bit":
            return value / 8
        case "kbit":
            return value * 1_000 / 8
        case "Mbit":
            return value * 1_000_000 / 8
        case "Gbit":
            return value * 1_000_000_000 / 8
        case "B":
            return value
        case "kB":
            return value * 1_000
        case "MB":
            return value * 1_000_000
        case "GB":
            return value * 1_000_000_000
        case "TB":
            return value * 1_000_000_000_000
        case "PB":
            return value * 1_000_000_000_000_000
        case "KiB":
            return value * 1_024
        case "MiB":
            return value * 1_024**2
        case "GiB":
            return value * 1_024**3
        case "TiB":
            return value * 1_024**4
        case "PiB":
            return value * 1_024**5
        case _:
            return value


def _convert_concentration_to_ug_m3(
    unit: str,
    value: float,
    ppb_factor: float,
) -> float:
    """Convert ppb/ppm concentrations to μg/m³ using a gas-specific factor."""
    match unit:
        case "ppb":
            return value * ppb_factor
        case "ppm":
            return value * ppb_factor * 1_000
        case "μg/m³":
            return value
        case _:
            return value


def _convert_concentration_to_ppm(
    unit: str,
    value: float,
    ppb_factor: float,
) -> float:
    """Convert ppb/μg/m³/mg/m³ concentrations to ppm."""
    match unit:
        case "ppb":
            return value / 1_000
        case "ppm":
            return value
        case "μg/m³":
            return value / ppb_factor
        case "mg/m³":
            return (value / ppb_factor) * 1_000
        case _:
            return value


def convert_co(unit: str, value: float) -> float:
    """Normalize CO to ppm."""
    if unit == "ppm":
        return value
    return _convert_concentration_to_ppm(unit, value, 1.15)


def convert_o3(unit: str, value: float) -> float:
    """Normalize ozone to μg/m³."""
    if unit == "μg/m³":
        return value
    return _convert_concentration_to_ug_m3(unit, value, 1.96)


def convert_so2(unit: str, value: float) -> float:
    """Normalize SO₂ to μg/m³."""
    if unit == "μg/m³":
        return value
    return _convert_concentration_to_ug_m3(unit, value, 2.62)


def convert_absolute_humidity(unit: str, value: float) -> float:
    """Normalize absolute humidity to g/m³."""
    match unit:
        case "g/m³":
            return value
        case "mg/m³":
            return value / 1_000
        case _:
            return value


def convert_apparent_power(unit: str, value: float) -> float:
    """Normalize apparent power to VA."""
    match unit:
        case "mVA":
            return value / 1_000
        case "VA":
            return value
        case "kVA":
            return value * 1_000
        case _:
            return value


def convert_area(unit: str, value: float) -> float:
    """Normalize area to m²."""
    match unit:
        case "mm²":
            return value / 1_000_000
        case "cm²":
            return value / 10_000
        case "m²":
            return value
        case "km²":
            return value * 1_000_000
        case "in²":
            return value * 0.00064516
        case "ft²":
            return value * 0.09290304
        case "yd²":
            return value * 0.83612736
        case "mi²":
            return value * 2_589_988.110336
        case "ac":
            return value * 4046.8564224
        case "ha":
            return value * 10_000
        case _:
            return value


def convert_blood_glucose(unit: str, value: float) -> float:
    """Normalize blood glucose to mg/dL."""
    match unit:
        case "mg/dL":
            return value
        case "mmol/L":
            return value * 18.0182
        case _:
            return value


def convert_conductivity(unit: str, value: float) -> float:
    """Normalize conductivity to µS/cm."""
    match unit:
        case "S/cm":
            return value * 1_000_000
        case "mS/cm":
            return value * 1_000
        case "µS/cm":
            return value
        case _:
            return value


def convert_duration(unit: str, value: float) -> float:
    """Normalize duration to seconds."""
    match unit:
        case "d":
            return value * 86_400
        case "h":
            return value * 3_600
        case "min":
            return value * 60
        case "s":
            return value
        case "ms":
            return value / 1_000
        case "µs":
            return value / 1_000_000
        case _:
            return value


def convert_energy_distance(unit: str, value: float) -> float:
    """Normalize energy-per-distance to kWh/100km."""
    match unit:
        case "kWh/100km":
            return value
        case "Wh/km":
            return value * 0.1
        case _:
            return value


def convert_irradiance(unit: str, value: float) -> float:
    """Normalize irradiance to W/m²."""
    match unit:
        case "W/m²":
            return value
        case "BTU/(h⋅ft²)":
            return value * 3.154591
        case _:
            return value


def convert_reactive_power(unit: str, value: float) -> float:
    """Normalize reactive power to var."""
    match unit:
        case "mvar":
            return value / 1_000
        case "var":
            return value
        case "kvar":
            return value * 1_000
        case _:
            return value


def convert_reactive_energy(unit: str, value: float) -> float:
    """Normalize reactive energy to varh."""
    match unit:
        case "varh":
            return value
        case "kvarh":
            return value * 1_000
        case _:
            return value


_CONVERTERS: dict[str, UnitConverter] = {
    "measure_absolute_humidity": convert_absolute_humidity,
    "measure_apparent_power": convert_apparent_power,
    "measure_area": convert_area,
    "measure_blood_glucose": convert_blood_glucose,
    "measure_conductivity": convert_conductivity,
    "measure_current": convert_current,
    "measure_data_rate": convert_data_rate,
    "measure_data_size": convert_data_size,
    "measure_distance": convert_distance,
    "measure_duration": convert_duration,
    "measure_energy_distance": convert_energy_distance,
    "measure_frequency": convert_frequency,
    "measure_irradiance": convert_irradiance,
    "measure_o3": convert_o3,
    "measure_co": convert_co,
    "measure_power": convert_power,
    "measure_pressure": convert_pressure,
    "measure_reactive_power": convert_reactive_power,
    "measure_so2": convert_so2,
    "measure_speed": convert_speed_ms,
    "measure_temperature": convert_temperature,
    "measure_dew_point": convert_temperature,
    "measure_battery_voltage": convert_voltage,
    "measure_voltage": convert_voltage,
    "measure_content_volume": convert_volume_l,
    "measure_rain": convert_rain,
    "measure_rain_intensity": convert_rain_intensity,
    "measure_water": convert_water_flow,
    "measure_weight": convert_weight,
    "measure_wind_strength": convert_speed_kmh,
    "measure_gust_strength": convert_speed_kmh,
    "meter_gas": convert_volume_m3,
    "meter_power": convert_energy,
    "meter_reactive_energy": convert_reactive_energy,
    "meter_water": convert_volume_m3,
}


def _get_converter(capability_id: str) -> UnitConverter:
    """Resolve converter for a capability, ignoring Homey index suffixes."""
    base = capability_id.split(".", 1)[0]
    return _CONVERTERS.get(base, _identity)


def _identity(_unit: str, value: float) -> float:
    return value


# The unit a capability's value carries once `convert_units` has normalized it.
# Homey defines a base unit per capability, so this is a property of the
# capability rather than of any device, and a Flow writing a reading to a
# display slot can label it without the user typing a unit by hand.
_BASE_UNITS: dict[str, str] = {
    "measure_absolute_humidity": "g/m³",
    "measure_apparent_power": "VA",
    "measure_area": "m²",
    "measure_battery": "%",
    "measure_battery_voltage": "V",
    "measure_blood_glucose": "mg/dL",
    "measure_co": "ppm",
    "measure_co2": "ppm",
    "measure_conductivity": "µS/cm",
    "measure_content_volume": "L",
    "measure_current": "A",
    "measure_data_rate": "bit/s",
    "measure_data_size": "B",
    "measure_dew_point": "\u00b0C",
    "measure_distance": "m",
    "measure_duration": "s",
    "measure_energy_distance": "kWh/100km",
    "measure_frequency": "Hz",
    "measure_gust_strength": "km/h",
    "measure_humidity": "%",
    "measure_irradiance": "W/m²",
    "measure_luminance": "lx",
    "measure_noise": "dB",
    "measure_o3": "μg/m³",
    "measure_pm4": "μg/m³",
    "measure_power": "W",
    "measure_pressure": "mbar",
    "measure_rain": "mm",
    "measure_rain_intensity": "mm/h",
    "measure_reactive_power": "var",
    "measure_signal_strength": "dBm",
    "measure_so2": "μg/m³",
    "measure_speed": "m/s",
    "measure_temperature": "\u00b0C",
    "measure_voltage": "V",
    "measure_water": "L/min",
    "measure_weight": "g",
    "measure_wind_angle": "\u00b0",
    "measure_wind_strength": "km/h",
    "meter_gas": "m\u00b3",
    "meter_power": "kWh",
    "meter_reactive_energy": "varh",
    "meter_water": "m\u00b3",
}


_NON_NUMERIC_MEASUREMENTS = frozenset(
    {
        "measure_date",
        "measure_timestamp",
        "measure_uptime",
    }
)
"""``measure_*`` capabilities this package defines as ``string``.

The naming rule says a `measure_` capability is a reading, and for Homey's own
capabilities that holds. These three are ours and carry text, so a slot fed
from one would fail the numeric coercion on every flush.
"""


def is_measurement(capability_id: str) -> bool:
    """Whether this capability carries a numeric reading Homey can normalize.

    A unit is too narrow a test. `measure_co`, `measure_o3` and `measure_so2`
    pass their value through in whatever unit the node reports, and Homey
    defines a dozen more — `measure_pm25`, `measure_aqi`, `measure_ph` — whose
    unit this package never has to know because it never converts them. All of
    them are still numbers a Flow can put on a screen.

    Homey's own naming is the reliable signal: a reading is a `measure_` or
    `meter_` capability. That excludes `onoff`, `locked` and `esphome_string`,
    which is what the unit check was standing in for.
    """
    base = capability_id.split(".", 1)[0]
    if base in _NON_NUMERIC_MEASUREMENTS:
        return False
    return base.startswith(("measure_", "meter_"))


def base_unit(capability_id: str) -> str:
    """Return the unit a capability's converted value is in, or "" if unknown.

    Index suffixes are ignored, so ``measure_temperature.outside`` reports the
    same unit as ``measure_temperature``.
    """
    return _BASE_UNITS.get(capability_id.split(".", 1)[0], "")
