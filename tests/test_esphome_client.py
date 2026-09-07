"""EspHomeClient lifecycle and command-gate tests."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aioesphomeapi import APIConnectionError, DeviceInfo, EntityState

from homey_esphomedriver.esphome_client import EspHomeClient, SessionState


def _client(**kwargs: Any) -> EspHomeClient:
    return EspHomeClient("192.0.2.1", **kwargs)


def test_deep_sleep_hint_until_login_then_device_info() -> None:
    """Constructor hint holds until login; then ``DeviceInfo.has_deep_sleep`` wins."""

    async def run() -> None:
        client = _client(deep_sleep=True)
        assert client.deep_sleep is True

        client._on_state = AsyncMock()
        client._reconnect = MagicMock()
        api = MagicMock()
        api.device_info_and_list_entities = AsyncMock(
            return_value=(DeviceInfo(name="node", has_deep_sleep=False), [], [])
        )
        client._cli = api

        await client._handle_connect()

        assert client.deep_sleep is False
        assert client._reconnect.deep_sleep is False

    asyncio.run(run())


def test_on_connected_awaited_before_ready() -> None:
    """Commands stay blocked until ``on_connected`` returns."""

    async def on_connected(_info: DeviceInfo) -> None:
        assert client.state is SessionState.CONNECTED
        with pytest.raises(APIConnectionError, match="not ready"):
            client.command("light_command", key=1)

    async def run() -> None:
        client._on_state = AsyncMock()
        client._reconnect = MagicMock()
        device_info = DeviceInfo(name="node", has_deep_sleep=True)

        api = MagicMock()
        api.device_info_and_list_entities = AsyncMock(
            return_value=(device_info, [], [])
        )
        client._cli = api

        await client._handle_connect()

        assert client.state is SessionState.READY
        assert client.deep_sleep is True
        assert client._reconnect.deep_sleep is True
        api.subscribe_states.assert_called_once_with(client._dispatch_state)
        client.command("light_command", key=1)
        api.light_command.assert_called_once_with(key=1)

    client = _client(on_connected=on_connected)
    asyncio.run(run())


def test_on_ready_runs_once_commands_are_allowed() -> None:
    """``on_ready`` sees a READY session; ``on_connected`` never does."""
    seen: list[str] = []

    async def on_connected(_info: DeviceInfo) -> None:
        seen.append(f"connected:{client.available}")

    async def on_ready() -> None:
        seen.append(f"ready:{client.available}")
        client.command("light_command", key=1)

    async def run() -> None:
        client._on_state = AsyncMock()
        client._reconnect = MagicMock()
        api = MagicMock()
        api.device_info_and_list_entities = AsyncMock(
            return_value=(DeviceInfo(name="node"), [], [])
        )
        client._cli = api

        await client._handle_connect()

        assert seen == ["connected:False", "ready:True"]
        api.light_command.assert_called_once_with(key=1)

    client = _client(on_connected=on_connected, on_ready=on_ready)
    asyncio.run(run())


def test_on_ready_skipped_when_session_dropped_during_on_connected() -> None:
    """A drop inside ``on_connected`` leaves commands blocked; nothing is ready."""

    async def on_connected(_info: DeviceInfo) -> None:
        client._state = SessionState.DISCONNECTED

    on_ready = AsyncMock()

    async def run() -> None:
        client._on_state = AsyncMock()
        client._reconnect = MagicMock()
        api = MagicMock()
        api.device_info_and_list_entities = AsyncMock(
            return_value=(DeviceInfo(name="node"), [], [])
        )
        client._cli = api

        await client._handle_connect()

        assert client.state is SessionState.DISCONNECTED
        on_ready.assert_not_called()

    client = _client(on_connected=on_connected, on_ready=on_ready)
    asyncio.run(run())


def test_stop_suppresses_disconnect_hook() -> None:
    """``stop()`` clears ``_on_state`` so teardown does not call ``on_disconnected``."""

    async def run() -> None:
        on_disconnected = AsyncMock()
        client = _client(on_disconnected=on_disconnected)
        client._on_state = AsyncMock()
        client._cli = MagicMock()
        client._cli.disconnect = AsyncMock()
        client._reconnect = MagicMock()
        client._reconnect.stop = AsyncMock()
        client._state = SessionState.READY

        await client.stop()

        assert client._on_state is None
        await client._handle_disconnect(expected_disconnect=True)
        on_disconnected.assert_not_awaited()

    asyncio.run(run())


def test_async_on_state_is_invoked() -> None:
    """The sync ``subscribe_states`` hop invokes the async ``on_state`` callback."""
    received: list[EntityState] = []

    async def on_state(state: EntityState) -> None:
        received.append(state)

    async def run() -> None:
        client = _client()
        client._on_state = on_state
        client._dispatch_state(EntityState(key=7))
        await asyncio.sleep(0)
        assert len(received) == 1
        assert received[0].key == 7

    asyncio.run(run())
