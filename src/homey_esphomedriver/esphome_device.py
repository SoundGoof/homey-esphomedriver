"""Homey Device for one ESPHome node (identity is the mDNS MAC).

Owns :class:`~homey_esphomedriver.esphome_client.EspHomeClient` and the
capability / command / state handlers. Homey discovery and settings drive
persist + reconnect; entity I/O lives on the handlers.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

from aioesphomeapi import (
    DeviceInfo,
    EntityCategory,
    EntityState,
)
from homey.device import Device
from homey.discovery_result import DiscoveryResult
from homey.discovery_result_mdns_sd import DiscoveryResultMDNSSD

from homey_esphomedriver.capabilities import DeviceCapabilityHandler
from homey_esphomedriver.entities.commands import DeviceEntityCommandHandler
from homey_esphomedriver.entities.commands.generic import (
    WRITE_COMMANDS,
    write_entity_value,
)
from homey_esphomedriver.entities.mapping import entity_domain
from homey_esphomedriver.entities.state import DeviceEntityStateHandler
from homey_esphomedriver.esphome_client import (
    DEFAULT_API_PORT,
    EspHomeClient,
)
from homey_esphomedriver.esphome_driver import EspHomeDriver
from homey_esphomedriver.esphome_util import (
    debug_log,
    device_info_settings,
    error_key,
    is_missing_number,
    normalize_mac,
)
from homey_esphomedriver.profile import BrandProfile
from homey_esphomedriver.settings import setting_matches

_UNREPORTED = object()
"""Marker for a write that is not answering a value the node reported."""


class EspHomeDevice(Device[EspHomeDriver]):
    """
    Homey device backed by one ESPHome node.

    Extend this class and export it from ``device.py`` as ``homey_export``.
    Override :meth:`on_esphome_init` / :meth:`on_esphome_connected` /
    :meth:`on_esphome_uninit` instead of :meth:`on_init` / :meth:`on_uninit`.

    Example:
        ```python
        from homey_esphomedriver import EspHomeDevice

        homey_export = EspHomeDevice
        ```
    """

    _client: EspHomeClient | None
    _capability_handler: DeviceCapabilityHandler
    _commands: DeviceEntityCommandHandler
    _setting_keys: dict[int, str]
    _setting_pending: dict[int, Any]
    _state_handler: DeviceEntityStateHandler

    @property
    def brand_profile(self) -> BrandProfile:
        """Product profile from the owning driver."""
        return self.driver.brand_profile

    @property
    def client(self) -> EspHomeClient | None:
        """Live Native API session; ``None`` until a host is known and started."""
        return self._client

    async def on_init(self) -> None:
        """Wire handlers and start the Native API session.

        Do not override. Use :meth:`on_esphome_init` for brand setup.
        """
        await super().on_init()

        self._client = None
        self._state_handler = DeviceEntityStateHandler(self)
        self._commands = DeviceEntityCommandHandler(self)
        self._capability_handler = DeviceCapabilityHandler(self)
        self._setting_keys = {}
        self._setting_pending = {}

        device_class = self.get_setting("device_class")
        if device_class != "auto":
            await self._apply_device_class_setting(str(device_class))

        await self._state_handler.init()
        await self._capability_handler.ensure()

        await self._ensure_client_started()
        await self.on_esphome_init(self._client)

        self.log(f"Initialized EspHomeDevice {self.get_name()}")

    async def on_esphome_init(self, client: EspHomeClient | None) -> None:
        """Brand hook after capability wiring.

        Args:
            client: Live Native API session, or ``None`` when the node has no
                host yet (waiting on mDNS).
        """

    async def on_esphome_connected(self, client: EspHomeClient) -> None:
        """Brand hook after each Native API login, including reconnects.

        Homey already shows the device as available here, but the session
        is not READY until this hook returns. Do not issue commands until
        ``client.available`` or :meth:`_require_client`.
        """

    async def on_esphome_uninit(self) -> None:
        """Brand hook before the API session stops.

        Do not override :meth:`on_uninit`.
        """

    async def on_uninit(self) -> None:
        """Stop the API session and release state handlers.

        Do not override. Use :meth:`on_esphome_uninit` for brand cleanup.
        """
        await self.on_esphome_uninit()
        if self._client is not None:
            await self._client.stop()
            self._client = None
        self._state_handler.uninit()
        await super().on_uninit()

    async def on_discovery_result(self, discovery_result: DiscoveryResult) -> bool:
        """Match discovery results by MAC even when separator/casing differs."""
        return normalize_mac(discovery_result.id) == normalize_mac(
            str(self.get_data()["id"])
        )

    async def on_discovery_available(self, discovery_result: DiscoveryResult) -> None:
        """Refresh address from discovery and ensure the API session is running."""
        result = cast(DiscoveryResultMDNSSD, discovery_result)
        self.log(
            f"ESPHome device available at {result.address}:{result.port} "
            f"(host={result.host})"
        )
        client = self._client
        await self._apply_discovery_endpoint(result)
        await self._ensure_client_started()
        if client is not None:
            await client.request_connect()

    async def on_discovery_address_changed(
        self, discovery_result: DiscoveryResult
    ) -> None:
        """Persist the new address when the node moves on the LAN."""
        result = cast(DiscoveryResultMDNSSD, discovery_result)
        await self._apply_discovery_endpoint(result)
        self.log(
            f"ESPHome device address changed to {result.address}:{result.port} "
            f"(host={result.host})"
        )

    async def on_discovery_last_seen_changed(
        self, discovery_result: DiscoveryResult
    ) -> None:
        """Connect immediately when Homey rediscovers the node, skipping backoff."""
        self.log(f"ESPHome device last seen updated ({discovery_result.address})")
        client = self._client
        if client is not None:
            await client.request_connect()

    async def on_settings(
        self,
        old_settings: dict[str, bool | float | str | None],
        new_settings: dict[str, bool | float | str | None],
        changed_keys: tuple[str, ...],
    ) -> str | None:
        """Apply device-class override and diagnostic/configuration toggles."""
        del old_settings

        if "device_class" in changed_keys:
            await self._apply_device_class_setting(str(new_settings["device_class"]))
        if "show_diagnostics" in changed_keys:
            await self._capability_handler.apply_category(
                bool(new_settings.get("show_diagnostics")),
                EntityCategory.DIAGNOSTIC,
                "diagnostic_capabilities",
            )
        if "show_configuration" in changed_keys:
            await self._capability_handler.apply_category(
                bool(new_settings.get("show_configuration")),
                EntityCategory.CONFIG,
                "configuration_capabilities",
            )

        mapping = self.brand_profile.setting_entities
        for key in changed_keys:
            if key in mapping:
                await self._write_setting_entity(key, new_settings.get(key))
        return None

    def debug(self, *args: object) -> None:
        """Write a debug log line when ``DEBUG`` is enabled in ``env.json``."""
        debug_log(self.log, *args)

    def _map_setting_keys(self, client: EspHomeClient) -> None:
        """Index the mapped entities' native keys for this connection.

        Runs before the first ``await`` of the connect handler. The client fills
        its entity cache and subscribes to states before calling that handler,
        and state dispatch hops through the event loop, so nothing can report
        before the map exists. Keys are per-connection, so a reading still
        pending from the previous session is dropped with them.
        """
        self._setting_keys = {}
        self._setting_pending = {}
        for setting_id, object_id in self.brand_profile.setting_entities.items():
            entity = client.entity_info(object_id)
            if entity is not None:
                self._setting_keys[entity.key] = setting_id

    async def _reconcile_setting_entity(self, key: int, reported: Any) -> None:
        """Write a mapped setting only when the node reports a different value.

        The node is authoritative for what it currently holds, and Homey is
        authoritative for what it should hold, so a write happens only on a real
        mismatch. Reconciling once per connection keeps a sleepy device from
        taking a write every time it wakes.

        Args:
            key: Native API key of the entity that reported.
            reported: Value the node reported.
        """
        if key not in self._setting_keys:
            return
        client = self._client
        if client is None or not client.available:
            # States are subscribed while the session is still CONNECTED, so the
            # node's one-shot dump can arrive before the session is READY.
            # Writing now would be dropped, and popping the key would spend the
            # single reconcile a `number` ever offers. Hold the reading instead
            # and replay it once commands are allowed.
            self._setting_pending[key] = reported
            return
        self._setting_pending.pop(key, None)
        setting_id = self._setting_keys.pop(key)
        await self._write_setting_entity(
            setting_id, self.get_setting(setting_id), reported=reported
        )

    async def _replay_pending_reconciles(self) -> None:
        """Reconcile readings that arrived before commands were allowed."""
        pending = self._setting_pending
        self._setting_pending = {}
        for key, reported in pending.items():
            await self._reconcile_setting_entity(key, reported)

    async def _write_setting_entity(
        self,
        setting_id: str,
        value: bool | float | str | None,
        *,
        reported: Any = _UNREPORTED,
    ) -> None:
        """Send a mapped setting to its entity.

        Homey settings are declared statically per driver, so a driver for a
        known product can name the entity a field configures and have core keep
        them in step. Configuration entities are the motivating case: a
        calibration trim belongs on the settings page, not the device tile.

        Args:
            setting_id: Homey settings key named in ``settingEntities``.
            value: Setting value as stored by Homey; ``None`` is left alone.
            reported: What the node currently holds, when known. A value the
                node already holds is not written again.
        """
        if value is None:
            return
        client = self._client
        if client is None or not client.available:
            # Homey keeps the saved value and the node is corrected on connect,
            # which is the whole point of reconciling — so refusing the save
            # here would only lose an edit made while the node sleeps.
            self.debug("node offline; mapped settings will reconcile on connect")
            return

        object_id = self.brand_profile.setting_entities[setting_id]
        entity = client.entity_info(object_id)
        if entity is None:
            self.error(f"Setting {setting_id!r} maps to unknown entity {object_id!r}")
            return
        domain = entity_domain(entity) or ""
        command = WRITE_COMMANDS.get(domain)
        if command is None:
            self.error(
                f"Setting {setting_id!r} maps to {object_id!r}, "
                f"a {domain} entity, which cannot be written"
            )
            return
        name, coerce = command
        try:
            state = coerce(value)
        except TypeError, ValueError:
            self.error(
                f"Setting {setting_id!r} value {value!r} is not valid for {name}"
            )
            return
        if reported is not _UNREPORTED and setting_matches(state, reported):
            self.debug(f"{setting_id} already {reported!r} on the node")
            return
        self.debug(f"setting {setting_id} -> {object_id} = {state!r}")
        try:
            write_entity_value(client, domain, entity.key, state)
        except Exception as err:  # noqa: BLE001 - the save must still succeed
            # A dropped session here would fail the user's settings save
            # for a write the node picks up on the next reconcile anyway.
            self.error(f"Could not write {setting_id!r} to the node", err)

    async def apply_connection(
        self,
        *,
        host: str,
        port: int,
        noise_psk: str | None,
    ) -> None:
        """Persist a repaired endpoint and restart the Native API session."""
        await self._persist_endpoint(host, port, noise_psk=noise_psk or "")
        if self._client is not None:
            await self._client.stop()
            self._client = None
        await self._ensure_client_started()

    async def _apply_device_class_setting(self, value: str) -> None:
        """Set Homey class from the device_class setting (auto restores mapped)."""
        target = self.get_store()["auto_class"] if value == "auto" else value
        await self.set_class(target)

    async def _ensure_client_started(self) -> None:
        """Create and start the long-lived API session from device settings."""
        if self._client is not None:
            return

        store = self.get_store()
        host = str(self.get_setting("host") or store.get("address") or "").strip()
        if not host:
            self.error("ESPHome host is missing; waiting for discovery or settings")
            return

        port = int(self.get_setting("port") or store.get("port") or DEFAULT_API_PORT)
        noise_psk = str(store.get("noise_psk") or "").strip() or None
        expected_mac = str(self.get_data()["id"])

        name = str(self.get_setting("hostname") or "").strip() or None
        self._client = EspHomeClient(
            host,
            port,
            name=name,
            noise_psk=noise_psk,
            expected_mac=expected_mac,
            client_info=self.brand_profile.client_info,
            deep_sleep=self.get_setting("deep_sleep") == "Yes",
            on_connected=self._on_connected,
            on_disconnected=self._on_disconnected,
            on_connect_error=self._on_connect_error,
            on_ready=self._on_ready,
        )
        # Only a profile that maps settings needs to see every state; the rest
        # hand states straight to the capability layer.
        on_state = (
            self._on_state
            if self.brand_profile.setting_entities
            else self._state_handler.handle_state
        )
        await self._client.start(on_state)

    async def _persist_endpoint(
        self,
        host: str,
        port: int,
        *,
        hostname: str | None = None,
        noise_psk: str | None = None,
    ) -> None:
        """Write endpoint into store and settings (shared by discovery and repair)."""
        await self.set_store_value("address", host)
        await self.set_store_value("port", port)
        if hostname is not None:
            await self.set_store_value("host", hostname)
        if noise_psk is not None:
            await self.set_store_value("noise_psk", noise_psk)
        await self.set_settings({"host": host, "port": str(port)})

    async def _apply_discovery_endpoint(self, result: DiscoveryResultMDNSSD) -> None:
        """Persist discovery address and update the live client when it changed."""
        host = result.address
        if not host:
            return
        port = result.port or DEFAULT_API_PORT
        await self._persist_endpoint(host, port, hostname=result.host)
        client = self._client
        if client is not None and (host != client.host or port != client.port):
            await client.update_endpoint(host=host, port=port)

    async def _on_ready(self) -> None:
        """The session accepts commands: send what the connect dump asked for."""
        await self._replay_pending_reconciles()

    async def _on_state(self, state: EntityState) -> None:
        """Watch mapped setting entities, then hand the state on.

        The node's one-shot dump at connect is the only chance a `number` gives
        to notice a drifted setting, so the reconcile has to see states as they
        arrive rather than waiting for the connect handler.
        """
        if state.key in self._setting_keys:
            try:
                await self._reconcile_setting_state(state)
            except Exception as err:  # noqa: BLE001 - never block the update
                self.error("Could not reconcile a mapped setting", err)
        await self._state_handler.handle_state(state)

    async def _reconcile_setting_state(self, state: EntityState) -> None:
        # An entity that has not published yet sends a placeholder — an empty
        # option, an uninitialised float — flagged by `missing_state`; that is
        # not a value to reconcile against.
        if getattr(state, "missing_state", False):
            return
        reported = getattr(state, "state", None)
        if reported is None or (
            isinstance(reported, float) and is_missing_number(reported)
        ):
            return
        await self._reconcile_setting_entity(state.key, reported)

    async def _on_connected(self, device_info: DeviceInfo) -> None:
        client = cast(EspHomeClient, self._client)
        # Settings live on the node but are stored by Homey, so they drift when
        # the node is reflashed or reset. Rather than writing them on every
        # connect — wasteful for a battery device that wakes often — the mapped
        # entities are watched and written only when the node disagrees.
        self._map_setting_keys(client)
        has_encryption = bool(str(self.get_store().get("noise_psk") or "").strip())
        await self.set_settings(
            device_info_settings(
                device_info,
                host=client.host,
                encrypted=has_encryption,
            )
        )
        await self.set_available()
        native_app_suggestion = self.brand_profile.native_app_suggestion(
            device_info.project_name
        )
        if native_app_suggestion:
            message = self.homey.translate(
                "nativeAppSuggestion",
                appName=native_app_suggestion,
            )

            async def show_warning() -> None:
                try:
                    await self.set_warning(message)
                except Exception as error:
                    self.error(error)

            self.homey.set_timeout(
                lambda: asyncio.ensure_future(show_warning()),
                1000,
            )

        await self.on_esphome_connected(client)

    async def _on_disconnected(self, expected: bool) -> None:
        if expected:
            return
        client = self._client
        if client is not None and client.deep_sleep:
            return
        await self.set_unavailable(self.homey.translate("errors.connection_lost"))

    async def _on_connect_error(self, error: Exception) -> None:
        self.error("ESPHome connect error", error)
        client = self._client
        if client is not None and client.deep_sleep:
            return
        await self.set_unavailable(self.homey.translate(error_key(error)))

    def _require_client(self) -> EspHomeClient:
        """Return the live session, or raise if the node is not ready."""
        client = self._client
        if client is None or not client.available:
            raise RuntimeError(self.homey.translate("errors.device_not_connected"))
        return client
