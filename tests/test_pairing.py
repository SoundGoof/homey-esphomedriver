"""DriverPairHandler probe retry tests."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest
from aioesphomeapi import DeviceInfo, EncryptionPlaintextAPIError

from homey_esphomedriver.pairing import DriverPairHandler


def _handler(
    *, host: str = "10.0.0.5", noise_psk: str | None = "secret"
) -> DriverPairHandler:
    driver = MagicMock()
    driver.brand_profile.client_info = "test-client"
    driver.debug = MagicMock()
    handler = DriverPairHandler(driver)
    handler.host = host
    handler.port = 6053
    handler.noise_psk = noise_psk
    return handler


def test_probe_retries_plaintext_when_psk_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """A PSK probe that hits EncryptionPlaintextAPIError retries once without PSK."""
    info = DeviceInfo(name="node", mac_address="AA:BB:CC:DD:EE:FF")
    calls: list[str | None] = []

    async def probe(
        host: str,
        port: int,
        *,
        noise_psk: str | None = None,
        **_kwargs: Any,
    ) -> tuple[DeviceInfo, list[Any], list[Any]]:
        calls.append(noise_psk)
        if noise_psk is not None:
            raise EncryptionPlaintextAPIError("plaintext")
        return info, [], []

    monkeypatch.setattr(
        "homey_esphomedriver.pairing.probe_esphome_device",
        probe,
    )
    handler = _handler(noise_psk="secret")

    device_info, entities, services, used_psk = asyncio.run(handler._probe())

    assert calls == ["secret", None]
    assert device_info is info
    assert entities == []
    # the service list rides along: the Flow markers are derived from it
    assert services == []
    assert used_psk is None


def test_probe_does_not_retry_plaintext_without_psk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a stored PSK, EncryptionPlaintextAPIError is not swallowed."""

    async def probe(*_args: Any, **_kwargs: Any) -> Any:
        raise EncryptionPlaintextAPIError("plaintext")

    monkeypatch.setattr(
        "homey_esphomedriver.pairing.probe_esphome_device",
        probe,
    )
    handler = _handler(noise_psk=None)

    with pytest.raises(EncryptionPlaintextAPIError):
        asyncio.run(handler._probe())


def test_probe_requires_host() -> None:
    """Probe refuses to run until a host is set."""
    handler = _handler()
    handler.host = None
    handler._driver.homey.translate = MagicMock(return_value="host required")

    with pytest.raises(ValueError, match="host required"):
        asyncio.run(handler._probe())
