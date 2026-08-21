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
    UserServiceArgType,
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


def _coerce_bool(value: Any) -> bool:
    """Read a declared ``bool`` argument, including Flow's string form.

    ``bool("false")`` is ``True``, so a typed-in "false" would switch a node
    setting on. Only the spellings a user can reasonably mean are accepted;
    anything else is a ValueError rather than a silent truthy value.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().casefold()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off", ""}:
        return False
    msg = f"Cannot read {value!r} as a boolean"
    raise ValueError(msg)


_ARG_COERCERS: dict[UserServiceArgType, Callable[[Any], Any]] = {
    UserServiceArgType.BOOL: _coerce_bool,
    UserServiceArgType.INT: int,
    UserServiceArgType.FLOAT: float,
    UserServiceArgType.STRING: str,
    UserServiceArgType.BOOL_ARRAY: lambda v: [_coerce_bool(i) for i in v],
    UserServiceArgType.INT_ARRAY: lambda v: [int(i) for i in v],
    UserServiceArgType.FLOAT_ARRAY: lambda v: [float(i) for i in v],
    UserServiceArgType.STRING_ARRAY: lambda v: [str(i) for i in v],
}
"""Coercion per declared ``UserService`` argument type.

Homey Flow-card inputs arrive as strings regardless of the ESPHome variable
type, so values are coerced to what the node declared rather than passed
through; aioesphomeapi serialises by declared type and a mismatch is rejected
at the node.
"""

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

        self._on_state: StateCallback | None = None
        self._cli: APIClient | None = None
        self._reconnect: ReconnectLogic | None = None
        self._device_info: DeviceInfo | None = None
        self._state = SessionState.DISCONNECTED
        self._services: dict[str, UserService] = {}
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

    @property
    def actions(self) -> tuple[str, ...]:
        """Names of the node's user-defined API actions, sorted.

        Empty while disconnected: the cache is rebuilt on each connect.
        """
        return tuple(sorted(self._services))

    @property
    def entity_object_ids(self) -> tuple[str, ...]:
        """Object ids the node exposes over the API; empty while disconnected.

        Includes entities Homey hides from the device tile, which is what makes
        display slots discoverable: they are deliberately not ``internal:``, as
        ESPHome omits internal entities from the API entirely.
        """
        return tuple(self._entities_by_object_id)

    def entity_info(self, object_id: str) -> EntityInfo | None:
        """Return the entity info for an object id, or ``None`` if absent.

        Callers that must pick a command by entity type need more than the key.
        """
        return self._entities_by_object_id.get(object_id)

    def entity_key(self, object_id: str) -> int | None:
        """Return the native API key for an object id, or ``None`` if absent.

        Commands address entities by key, but a driver profile names them by
        object id, which is the stable identifier a YAML author controls.
        """
        entity = self._entities_by_object_id.get(object_id)
        return None if entity is None else entity.key

    async def execute_action(self, name: str, data: dict[str, Any]) -> None:
        """Invoke a user-defined API action on the node.

        ESPHome's ``display:`` is not a native API entity, so actions are the
        only way to push a value to a screen. Arguments are coerced to the
        types the node declared.

        Args:
            name: Action name as declared under ESPHome ``api: actions:``.
            data: Argument values keyed by declared variable name.

        Raises:
            KeyError: If the node did not declare an action with that name.
            ValueError: If an argument is missing, or a value cannot be coerced
                to its declared type.
            APIConnectionError: If the session is not ready for commands.
        """
        # The action list is per-connection and cleared on disconnect, so an
        # unready session would raise `KeyError` here and be reported as a
        # missing action — sending the user to look for a YAML typo that is
        # not there. `command()` gates the same way.
        if self._state is not SessionState.READY:
            raise APIConnectionError(
                f"ESPHome session for {self._host}:{self._port} is not ready"
            )
        service = self._services.get(name)
        if service is None:
            raise KeyError(name)

        # aioesphomeapi indexes `data` by every declared argument, so a missing
        # one raises a bare KeyError that reads exactly like an unknown action.
        missing = [arg.name for arg in service.args if arg.name not in data]
        if missing:
            names = ", ".join(repr(item) for item in missing)
            msg = f"Action {name!r} needs argument(s) {names}"
            raise ValueError(msg)

        payload: dict[str, Any] = {}
        for arg in service.args:
            coerce = _ARG_COERCERS.get(arg.type)
            value = data[arg.name]
            if coerce is None:
                payload[arg.name] = value
                continue
            try:
                payload[arg.name] = coerce(value)
            except (TypeError, ValueError) as err:
                msg = (
                    f"Cannot coerce {value!r} for argument {arg.name!r} "
                    f"of action {name!r} to {arg.type.name}"
                )
                raise ValueError(msg) from err

        # Must be awaited: a non-awaited call sends nothing and raises nothing.
        await self.api.execute_service(service, payload)

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
        self._services = {}
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
            device_info, entities, services = await cli.device_info_and_list_entities()
            self._device_info = device_info
            # Actions and entities are per-connection: the node may be
            # reflashed with a different set while paired, so rebuild rather
            # than merge.
            self._services = {service.name: service for service in services}
            self._entities_by_object_id = {
                object_id: entity
                for entity in entities
                if (object_id := getattr(entity, "object_id", ""))
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

    async def _handle_disconnect(self, expected_disconnect: bool) -> None:
        self._state = SessionState.DISCONNECTED
        self._services = {}
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
