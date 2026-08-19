"""Shared helper tests.

Covers the pure formatting helpers and the native-API error classification that
drives pairing prompts, reconnect back-off and the locales keys shown to users.
"""

from __future__ import annotations

import pytest
from aioesphomeapi import (
    BadMACAddressAPIError,
    BadNameAPIError,
    DeviceInfo,
    EncryptionHelloAPIError,
    EncryptionPlaintextAPIError,
    HandshakeAPIError,
    InvalidAuthAPIError,
    InvalidEncryptionKeyAPIError,
    RequiresEncryptionAPIError,
    ResolveAPIError,
)
from aioesphomeapi.core import APIConnectionError, TimeoutAPIError

from homey_esphomedriver.esphome_util import (
    device_info_settings,
    error_key,
    format_date,
    format_event_type,
    format_project,
    format_timestamp,
    format_uptime,
    format_webserver,
    invalid_encryption_key,
    is_device_mismatch,
    needs_encryption_key,
    normalize_mac,
    requires_encryption,
    should_stop_reconnect,
)


@pytest.mark.parametrize(
    "raw",
    ["AA:BB:CC:DD:EE:FF", "aa-bb-cc-dd-ee-ff", "AABBCCDDEEFF", "aabbccddeeff"],
)
def test_normalize_mac_is_separator_and_case_insensitive(raw: str) -> None:
    assert normalize_mac(raw) == "aabbccddeeff"


@pytest.mark.parametrize(
    ("name", "version", "expected"),
    [
        ("Brand.AQ-1", "1.0.0", "Brand.AQ-1 1.0.0"),
        ("Brand.AQ-1", "", "Brand.AQ-1"),
        ("", "1.0.0", "1.0.0"),
        ("", "", ""),
    ],
)
def test_format_project(name: str, version: str, expected: str) -> None:
    assert format_project(name, version) == expected


@pytest.mark.parametrize(
    ("host", "port", "expected"),
    [
        ("192.168.1.10", 80, "http://192.168.1.10:80"),
        ("192.168.1.10", 0, ""),
        ("192.168.1.10", -1, ""),
        ("", 80, ""),
    ],
)
def test_format_webserver(host: str, port: int, expected: str) -> None:
    assert format_webserver(host, port) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0m"),
        (59, "0m"),
        (60, "1m"),
        (3600, "1h"),
        (3660, "1h 1m"),
        (86400, "1d"),
        (7_807_680, "90d 8h 48m"),
    ],
)
def test_format_uptime(seconds: float, expected: str) -> None:
    assert format_uptime(seconds) == expected


def test_format_uptime_omits_empty_leading_units_but_always_shows_minutes() -> None:
    """A bare-seconds uptime still renders as ``0m`` rather than an empty string."""
    assert format_uptime(30) == "0m"
    assert format_uptime(90061) == "1d 1h 1m"


def test_format_date() -> None:
    assert format_date("2026-08-19") == "2026-08-19"
    assert format_date("2026-08-19T14:30:00Z") == "2026-08-19"


def test_format_timestamp_is_stable_for_naive_input() -> None:
    """Naive timestamps are not shifted, so this does not depend on local TZ."""
    assert format_timestamp("2026-08-19T14:30:05") == "2026-08-19 14:30:05"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("single_press", "Single Press"),
        ("double_press", "Double Press"),
        ("press", "Press"),
        ("", ""),
    ],
)
def test_format_event_type(raw: str, expected: str) -> None:
    assert format_event_type(raw) == expected


def test_device_info_settings_reports_encryption_state() -> None:
    info = DeviceInfo()
    encrypted = device_info_settings(info, host="10.0.0.5", encrypted=True)
    plaintext = device_info_settings(info, host="10.0.0.5", encrypted=False)
    assert encrypted["encryption"] == "Configured"
    assert plaintext["encryption"] == "Not set"


def test_device_info_settings_maps_device_info_fields() -> None:
    info = DeviceInfo(
        name="garage",
        model="ESP32-S3",
        esphome_version="2026.7.0",
        project_name="Brand.AQ-1",
        project_version="1.2.3",
        webserver_port=80,
        has_deep_sleep=True,
    )
    settings = device_info_settings(info, host="10.0.0.5", encrypted=False)
    assert settings["hostname"] == "garage"
    assert settings["model"] == "ESP32-S3"
    assert settings["esphome_version"] == "2026.7.0"
    assert settings["project"] == "Brand.AQ-1 1.2.3"
    assert settings["webserver"] == "http://10.0.0.5:80"
    assert settings["deep_sleep"] == "Yes"


def test_device_info_settings_blanks_webserver_when_not_exposed() -> None:
    settings = device_info_settings(DeviceInfo(), host="10.0.0.5", encrypted=False)
    assert settings["webserver"] == ""
    assert settings["deep_sleep"] == "No"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (InvalidEncryptionKeyAPIError("x"), "errors.invalid_encryption_key"),
        (RequiresEncryptionAPIError("x"), "errors.requires_encryption_key"),
        (InvalidAuthAPIError("x"), "errors.invalid_auth"),
        (EncryptionPlaintextAPIError("x"), "errors.encryption_plaintext"),
        (EncryptionHelloAPIError("x"), "errors.encryption_hello"),
        (
            BadMACAddressAPIError("x", "garage", "aa:bb:cc:dd:ee:ff"),
            "errors.device_mismatch",
        ),
        (BadNameAPIError("x", "garage"), "errors.device_mismatch"),
        (ResolveAPIError("x"), "errors.resolve_error"),
        (TimeoutAPIError("x"), "errors.timeout"),
        (HandshakeAPIError("x"), "errors.handshake"),
        (APIConnectionError("x"), "errors.cannot_connect"),
        (RuntimeError("x"), "errors.cannot_connect"),
    ],
)
def test_error_key(error: BaseException, expected: str) -> None:
    assert error_key(error) == expected


def test_error_key_checks_encryption_before_handshake() -> None:
    """Encryption failures subclass HandshakeAPIError, so order matters."""
    error = InvalidEncryptionKeyAPIError("x")
    assert isinstance(error, HandshakeAPIError)
    assert error_key(error) == "errors.invalid_encryption_key"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RequiresEncryptionAPIError("x"), True),
        (InvalidEncryptionKeyAPIError("x"), True),
        (InvalidAuthAPIError("x"), False),
        (TimeoutAPIError("x"), False),
    ],
)
def test_needs_encryption_key(error: BaseException, expected: bool) -> None:
    assert needs_encryption_key(error) is expected


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RequiresEncryptionAPIError("x"), True),
        (InvalidEncryptionKeyAPIError("x"), True),
        (InvalidAuthAPIError("x"), True),
        (EncryptionPlaintextAPIError("x"), True),
        (TimeoutAPIError("x"), False),
        (ResolveAPIError("x"), False),
        (APIConnectionError("x"), False),
    ],
)
def test_should_stop_reconnect(error: BaseException, expected: bool) -> None:
    """Retrying only helps for transient faults, never for bad credentials."""
    assert should_stop_reconnect(error) is expected


def test_error_predicates_are_specific() -> None:
    assert requires_encryption(RequiresEncryptionAPIError("x")) is True
    assert requires_encryption(InvalidEncryptionKeyAPIError("x")) is False
    assert invalid_encryption_key(InvalidEncryptionKeyAPIError("x")) is True
    assert is_device_mismatch(BadNameAPIError("x", "garage")) is True
    assert is_device_mismatch(TimeoutAPIError("x")) is False
