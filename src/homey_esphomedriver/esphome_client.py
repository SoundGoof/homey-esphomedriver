"""Per-device Native API session used by pairing and runtime.

Homey keeps one ESPHome node per device. Pairing uses the free function
:func:`probe_esphome_device`; runtime devices keep
:class:`~aioesphomeapi.ReconnectLogic` so drops recover and state
subscriptions re-arm on each connect.

Commands are gated on :attr:`SessionState.READY` so Homey defaults cannot
reach the node before the initial state dump has been applied.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from enum import Enum, auto
from typing import Any

from aioesphomeapi import (
    APIClient,
    APIConnectionError,
    DeviceInfo,
    EntityInfo,
    EntityState,
    ReconnectLogic,
    UserService,
)

from homey_esphomedriver.esphome_util import (
    is_device_mismatch,
    normalize_mac,
    should_stop_reconnect,
)
from homey_esphomedriver.profile import DEFAULT_CLIENT_INFO

_LOGGER = logging.getLogger(__name__)

DEFAULT_API_PORT = 6053
"""ESPHome native API port used when discovery omits it."""

StateCallback = Callable[[EntityState], Awaitable[None]]
DebugCallback = Callable[..., None]


class SessionState(Enum):
    """Lifecycle of a runtime Native API session."""

    DISCONNECTED = auto()
    CONNECTED = auto()
    READY = auto()


class EspHomeClient:
    """Native API session for one ESPHome node.

    Runtime devices call :meth:`start` so drops recover via ReconnectLogic.
    Commands stay gated until ``on_connected`` returns so Homey
    ``set_settings`` / ``set_available`` cannot race native API writes.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_API_PORT,
        *,
        name: str | None = None,
        noise_psk: str | None = None,
        expected_mac: str | None = None,
        client_info: str = DEFAULT_CLIENT_INFO,
        deep_sleep: bool = False,
        on_connected: Callable[[DeviceInfo], Awaitable[None]] | None = None,
        on_disconnected: Callable[[bool], Awaitable[None]] | None = None,
        on_connect_error: Callable[[Exception], Awaitable[None]] | None = None,
        on_ready: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Create a session for one ESPHome node.

        Args:
            host: Node address from discovery or settings.
            port: Native API port.
            name: Hostname used by ReconnectLogic logs and zeroconf.
            noise_psk: Noise encryption key, or ``None`` for plaintext.
            expected_mac: Paired MAC; a mismatch stops reconnect at that address.
            client_info: Name shown on the node for this Homey client.
            deep_sleep: Treat disconnects as expected while the node is sleeping.
            on_connected: Awaited after each login, before commands are allowed.
            on_disconnected: Awaited when a live session drops.
            on_connect_error: Awaited when a connection attempt fails.
            on_ready: Awaited once the session accepts commands, after
                ``on_connected`` returned with the session still up.
        """
        if not host:
            raise ValueError("ESPHome host is required")

        self._host = host
        self._port = int(port) if port else DEFAULT_API_PORT
        self._name = name or None
        self._noise_psk = noise_psk or None
        self._expected_mac = normalize_mac(expected_mac) if expected_mac else None
        self._client_info = client_info
        self._deep_sleep = deep_sleep
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._on_connect_error = on_connect_error
        self._on_ready = on_ready

        self._on_state: StateCallback | None = None
        self._cli: APIClient | None = None
        self._reconnect: ReconnectLogic | None = None
        self._device_info: DeviceInfo | None = None
        self._state = SessionState.DISCONNECTED
        self._entities_by_object_id: dict[str, EntityInfo] = {}

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def state(self) -> SessionState:
        """Current session gate: disconnected, connected, or ready for commands."""
        return self._state

    @property
    def available(self) -> bool:
        """Whether the session is ready for commands."""
        return self._state is SessionState.READY

    @property
    def device_info(self) -> DeviceInfo | None:
        return self._device_info

    @property
    def deep_sleep(self) -> bool:
        """Settings hint until the first login, then ``DeviceInfo.has_deep_sleep``."""
        return self._deep_sleep

    @property
    def api(self) -> APIClient:
        """Underlying aioesphomeapi client.

        Raises:
            APIConnectionError: If the client has not been constructed yet.
        """
        if self._cli is None:
            raise APIConnectionError("ESPHome client has not been created yet")
        return self._cli

    async def start(self, on_state: StateCallback) -> None:
        """Start ReconnectLogic and keep the node connected at runtime.

        Args:
            on_state: Async callback for subscribed entity states.
        """
        if self._reconnect is not None:
            return

        await self._ensure_stopped()
        self._on_state = on_state
        self._cli = self._create_client()
        self._reconnect = ReconnectLogic(
            client=self._cli,
            on_connect=self._handle_connect,
            on_disconnect=self._handle_disconnect,
            name=self._name,
            on_connect_error=self._handle_connect_error,
        )
        self._reconnect.deep_sleep = self._deep_sleep
        await self._reconnect.start()

    async def stop(self) -> None:
        """Stop reconnect attempts and close the API session."""
        self._on_state = None
        await self._ensure_stopped()
        self._device_info = None

    async def update_endpoint(self, *, host: str, port: int) -> None:
        """Apply a discovery address change and restart ReconnectLogic if running."""
        on_state = self._on_state
        self._host = host
        self._port = port
        await self._ensure_stopped()
        if on_state is not None:
            await self.start(on_state)

    async def request_connect(self) -> None:
        """Retry now if ReconnectLogic is waiting (Homey saw the node on mDNS)."""
        if self._reconnect is None:
            return
        await self._reconnect.start()

    def command(self, name: str, *args: Any, **kwargs: Any) -> None:
        """Forward a native API command.

        Args:
            name: ``APIClient`` method such as ``light_command``.

        Raises:
            APIConnectionError: If the session is not :attr:`SessionState.READY`.
        """
        if self._state is not SessionState.READY:
            raise APIConnectionError("ESPHome session is not ready for commands")
        getattr(self.api, name)(*args, **kwargs)

    async def list_entities_services(
        self,
    ) -> tuple[list[EntityInfo], list[UserService]]:
        """List entities on the current connection."""
        return await self.api.list_entities_services()

    def entity_info(self, object_id: str) -> EntityInfo | None:
        """Return the entity info for an object id, or ``None`` if absent.

        Commands address entities by key, but a driver profile names them by
        object id, the stable identifier a YAML author controls. The info
        rather than the key alone, because the command depends on the domain.
        """
        return self._entities_by_object_id.get(object_id)

    def _create_client(self) -> APIClient:
        """Build a fresh APIClient from the current endpoint settings."""
        return APIClient(
            self._host,
            self._port,
            client_info=self._client_info,
            noise_psk=self._noise_psk,
            expected_mac=self._expected_mac,
        )

    def _mark_ready(self) -> None:
        """Leave commands blocked if the session dropped during ``on_connected``."""
        if self._state is SessionState.CONNECTED:
            self._state = SessionState.READY

    def _dispatch_state(self, state: EntityState) -> None:
        """``subscribe_states`` is sync; hop so Homey capability writes can await."""
        on_state = self._on_state
        if on_state is None:
            return
        asyncio.ensure_future(self._emit_state(on_state, state))

    async def _emit_state(self, on_state: StateCallback, state: EntityState) -> None:
        """Log ``on_state`` failures so they are not unretrieved task exceptions."""
        try:
            await on_state(state)
        except Exception:
            _LOGGER.exception("Error handling ESPHome entity state")

    async def _ensure_stopped(self) -> None:
        """Tear down ReconnectLogic and any open socket."""
        reconnect = self._reconnect
        self._reconnect = None
        self._state = SessionState.DISCONNECTED
        self._entities_by_object_id = {}

        if reconnect is not None:
            await reconnect.stop()

        if self._cli is not None:
            await self._cli.disconnect(force=True)
            self._cli = None

    async def _handle_connect(self) -> None:
        """Re-subscribe and refresh device info whenever ReconnectLogic logs in."""
        cli = self.api
        try:
            device_info, entities, _ = await cli.device_info_and_list_entities()
            self._device_info = device_info
            # Entity keys are per-connection: the node may be reflashed with
            # a different set while paired, so rebuild rather than merge.
            self._entities_by_object_id = {
                entity.object_id: entity for entity in entities
            }
            self._name = device_info.name
            self._deep_sleep = device_info.has_deep_sleep
            reconnect = self._reconnect
            if self._on_state is None or reconnect is None:
                return
            reconnect.name = self._name
            reconnect.deep_sleep = self._deep_sleep
            cli.subscribe_states(self._dispatch_state)
            self._state = SessionState.CONNECTED
        except APIConnectionError:
            self._state = SessionState.DISCONNECTED
            # ReconnectLogic only schedules another attempt after on_stop.
            await cli.disconnect()
            return

        if self._on_connected is not None:
            await self._on_connected(device_info)
        self._mark_ready()
        if self._state is SessionState.READY and self._on_ready is not None:
            await self._on_ready()

    async def _handle_disconnect(self, expected_disconnect: bool) -> None:
        self._state = SessionState.DISCONNECTED
        self._entities_by_object_id = {}
        # Stopping the session closes the socket; that is not a Homey unavailable.
        if self._on_state is None:
            return
        if self._on_disconnected is not None:
            await self._on_disconnected(expected_disconnect)

    async def _handle_connect_error(self, error: Exception) -> None:
        self._state = SessionState.DISCONNECTED
        if self._on_state is None:
            return
        if self._on_connect_error is not None:
            await self._on_connect_error(error)
        if self._reconnect is None:
            return
        if should_stop_reconnect(error):
            self._on_state = None
            await self._reconnect.stop()
            self._reconnect = None
        elif is_device_mismatch(error):
            # Keep _on_state so a later discovery address change can restart.
            await self._reconnect.stop()
            self._reconnect = None


async def probe_esphome_device(
    host: str,
    port: int = DEFAULT_API_PORT,
    *,
    noise_psk: str | None = None,
    client_info: str = DEFAULT_CLIENT_INFO,
    debug: DebugCallback | None = None,
) -> tuple[DeviceInfo, list[EntityInfo], list[UserService]]:
    """One-shot probe for the pairing loading view."""
    if not host:
        raise ValueError("ESPHome host is required")

    resolved_port = int(port) if port else DEFAULT_API_PORT
    if debug is not None:
        debug(
            f"Connecting once to {host}:{resolved_port} "
            f"encrypted={noise_psk is not None}"
        )

    cli = APIClient(
        host,
        resolved_port,
        client_info=client_info,
        noise_psk=noise_psk or None,
    )
    try:
        await cli.connect(login=True)
        device_info, entities, services = await cli.device_info_and_list_entities()
        if debug is not None:
            debug(
                f"Listed {len(entities)} entities / {len(services)} services "
                f"from {device_info.name}"
            )
        return device_info, entities, services
    finally:
        await cli.disconnect(force=True)
