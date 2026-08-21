"""DriverPairHandler probe retry and pair-navigation tests."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest
from aioesphomeapi import (
    DeviceInfo,
    EncryptionPlaintextAPIError,
    RequiresEncryptionAPIError,
)

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
    handler._session = _Session()
    return handler


class _Session:
    def __init__(self) -> None:
        self.views: list[str] = []
        self.done_calls = 0

    async def show_view(self, view_id: str) -> None:
        self.views.append(view_id)

    async def done(self) -> None:
        self.done_calls += 1


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


def test_pair_enter_key_returns_next_view_without_navigating() -> None:
    """Continue stores the PSK and returns add_device; the view owns navigation."""
    handler = _handler(noise_psk=None)
    handler.selected = {"data": {"id": "aa:bb:cc:dd:ee:ff"}}

    async def try_pair(*, expected_id: str | None, prompt_key: bool) -> str:
        assert handler.noise_psk == "secret"
        assert expected_id == "aa:bb:cc:dd:ee:ff"
        assert prompt_key is False
        return "add_device"

    handler._try_pair = try_pair  # type: ignore[method-assign]
    session = handler._session
    assert isinstance(session, _Session)

    result = asyncio.run(handler._on_pair_enter_key({"noise_psk": "  secret  "}))

    assert result == "add_device"
    assert session.views == []


def test_pair_enter_key_requires_psk() -> None:
    """Continue with an empty key is rejected before navigation."""
    handler = _handler(noise_psk=None)
    handler._driver.homey.translate = MagicMock(return_value="key required")

    with pytest.raises(ValueError, match="key required"):
        asyncio.run(handler._on_pair_enter_key({"noise_psk": "   "}))


def test_pair_configure_manual_returns_next_view_without_navigating() -> None:
    """Connect stores host/port and returns the next view id, without navigating."""
    handler = _handler(host=None, noise_psk=None)

    async def try_pair(*, expected_id: str | None, prompt_key: bool) -> str:
        assert handler.host == "10.0.0.9"
        assert handler.port == 6053
        assert expected_id is None
        assert prompt_key is True
        return "enter_key"

    handler._try_pair = try_pair  # type: ignore[method-assign]
    session = handler._session
    assert isinstance(session, _Session)

    result = asyncio.run(
        handler._on_pair_configure_manual({"host": "10.0.0.9", "port": "6053"})
    )

    assert result == "enter_key"
    assert session.views == []


def test_repair_enter_key_returns_none_without_closing_the_session() -> None:
    """A repaired connection returns no view; the view closes the session."""
    handler = _handler(noise_psk=None)

    async def try_repair(*, prompt_key: bool) -> None:
        assert prompt_key is False
        return None

    handler._try_repair = try_repair  # type: ignore[method-assign]
    session = handler._session
    assert isinstance(session, _Session)

    result = asyncio.run(handler._on_repair_enter_key({"noise_psk": "secret"}))

    assert result is None
    assert session.done_calls == 0


def test_list_discovery_devices_does_not_need_runtime_strategy_type() -> None:
    """Listing mDNS results must not evaluate TYPE_CHECKING-only DiscoveryStrategy."""
    result = MagicMock()
    result.id = "aabbccddeeff"
    result.name = "node"
    result.address = "10.0.0.5"
    result.host = "node.local"
    result.port = 6053
    result.txt = {"friendly_name": "Kitchen"}

    strategy = MagicMock()
    strategy.get_discovery_results.return_value = {"aabbccddeeff": result}

    handler = _handler()
    handler._driver.get_discovery_strategy.return_value = strategy
    handler._driver.get_devices.return_value = []
    handler._driver.brand_profile.accepts_discovery.return_value = True

    devices = handler._list_discovery_devices()

    assert devices[0]["name"] == "Kitchen"
    assert devices[0]["data"]["id"] == "aabbccddeeff"


def test_loading_prompts_for_key_without_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First connect without a PSK opens enter_key and does not raise."""

    async def probe(*_args: Any, **_kwargs: Any) -> Any:
        raise RequiresEncryptionAPIError("need key")

    monkeypatch.setattr("homey_esphomedriver.pairing.probe_esphome_device", probe)
    handler = _handler(noise_psk=None)
    handler.selected = {"data": {"id": "aa:bb:cc:dd:ee:ff"}}
    session = handler._session
    assert isinstance(session, _Session)

    asyncio.run(handler._on_pair_show_view("loading"))

    assert session.views == ["enter_key"]
