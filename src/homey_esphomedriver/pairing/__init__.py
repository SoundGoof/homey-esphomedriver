"""Pair and repair wizards for ESPHome Homey drivers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

from aioesphomeapi import (
    DeviceInfo,
    EncryptionPlaintextAPIError,
    EntityInfo,
    UserService,
)

from homey_esphomedriver.entities.mapping import DeviceEntityMapper
from homey_esphomedriver.esphome_client import (
    DEFAULT_API_PORT,
    probe_esphome_device,
)
from homey_esphomedriver.esphome_types import HomeyEspHomeDeviceOption
from homey_esphomedriver.esphome_util import (
    error_key,
    needs_encryption_key,
    normalize_mac,
)
from homey_esphomedriver.pairing.ble_client import ImprovBleClient
from homey_esphomedriver.pairing.ble_protocol import ImprovError

if TYPE_CHECKING:
    from homey.discovery_result_mdns_sd import DiscoveryResultMDNSSD
    from homey.discovery_strategy import DiscoveryStrategy
    from homey.driver import ListDeviceProperties
    from homey.pair_session import PairSession

    from homey_esphomedriver.esphome_device import EspHomeDevice
    from homey_esphomedriver.esphome_driver import EspHomeDriver

_MDNS_WAIT_TIMEOUT_S = 12.0
"""Seconds to wait for mDNS after BLE Improv.

Kept well under Homey's ~30s pair-emit timeout because BLE setup already
used some of that budget.
"""
_MDNS_POLL_S = 2.0


class DriverPairHandler:
    """Owns one pair or repair session: wizard state, probe, and view routing."""

    def __init__(self, driver: EspHomeDriver) -> None:
        self._driver = driver
        self._session: PairSession | None = None
        self._device: EspHomeDevice | None = None
        self._expected_id: str | None = None
        self.selected: dict[str, Any] | None = None
        self.host: str | None = None
        self.port: int = DEFAULT_API_PORT
        self.noise_psk: str | None = None
        self.mapped_device: HomeyEspHomeDeviceOption | None = None
        self.listing_ble = False
        self.peripheral_uuid: str | None = None
        self.improv_client: ImprovBleClient | None = None

    async def pair(self, session: PairSession) -> None:
        """Wire multi-step pair views for discovery, BLE Improv, and encryption."""
        self._session = session
        session.set_handler("list_devices", self._on_list_devices)
        session.set_handler("list_devices_selection", self._on_list_devices_selection)
        session.set_handler(
            "list_ble_devices_selection", self._on_list_devices_selection
        )
        session.set_handler("showView", self._on_pair_show_view)
        session.set_handler("configure_manual", self._on_pair_configure_manual)
        session.set_handler("enter_wifi", self._on_enter_wifi)
        session.set_handler("enter_key", self._on_pair_enter_key)
        session.set_handler("get_device", self._on_get_device)
        session.set_handler("disconnect", self._close_improv)

    async def repair(self, session: PairSession, device: EspHomeDevice) -> None:
        """Update host/port, prompting for a Noise PSK only when the node needs it."""
        self._session = session
        self._device = device
        self.host = ""
        self.port = DEFAULT_API_PORT
        self.noise_psk = str(device.get_store().get("noise_psk") or "").strip() or None
        self._expected_id = str(device.get_data()["id"])

        session.set_handler("get_connection", self._repair_connection_values)
        session.set_handler("showView", self._on_repair_show_view)
        session.set_handler("configure_manual", self._on_repair_configure_manual)
        session.set_handler("enter_key", self._on_repair_enter_key)

    def _t(self, key: str) -> str:
        return self._driver.homey.translate(key)

    async def _on_list_devices(
        self,
        _view_data: Any = None,
    ) -> list[ListDeviceProperties]:
        """Return mDNS results, fallback rows, or BLE Improv peripherals."""
        if self.listing_ble:
            return await self._list_improv_pair_devices()

        devices = self._list_discovery_devices()
        devices.append(
            {
                "name": self._t("pair.list.add_by_ip"),
                "data": {"id": "manual"},
                "icon": "/icon-ip.svg",
                "capabilities": [],
            }
        )
        if self._driver.homey.has_permission("homey:wireless:ble"):
            devices.append(
                {
                    "name": self._t("pair.list.setup_bluetooth"),
                    "data": {"id": "ble-setup"},
                    "icon": "/icon-bluetooth.svg",
                    "capabilities": [],
                }
            )
        return devices

    async def _on_list_devices_selection(self, devices: list[dict[str, Any]]) -> None:
        """Capture the chosen discovery or BLE row before the next step."""
        if not devices:
            raise ValueError(self._t("errors.no_device_selected"))

        self.selected = devices[0]
        self.mapped_device = None
        self.noise_psk = None
        selected_id = self.selected.get("data", {}).get("id")
        store = self.selected.get("store") or {}

        if selected_id in ("manual", "ble-setup"):
            self.host = None
            self.peripheral_uuid = None
            if selected_id == "manual":
                self.port = DEFAULT_API_PORT
            return

        ble_uuid = store.get("peripheralUuid")
        if self.listing_ble or ble_uuid:
            self.host = None
            self.peripheral_uuid = str(ble_uuid or selected_id)
            return

        self.peripheral_uuid = None
        self.host = store.get("address") or store.get("host")
        self.port = int(store.get("port") or DEFAULT_API_PORT)

    async def _on_pair_show_view(self, view_id: str) -> None:
        """Route loading: BLE scan, manual IP, or connect+map."""
        session = self._require_session()

        if view_id == "list_ble_devices":
            self.listing_ble = True
            return
        if view_id == "list_devices":
            self.listing_ble = False
            await self._close_improv()
            return
        if view_id != "loading":
            return

        if self.selected is None:
            raise ValueError(self._t("errors.no_device_selected"))

        selected_id = self.selected.get("data", {}).get("id")
        if selected_id == "ble-setup":
            self.listing_ble = True
            await session.show_view("list_ble_devices")
            return

        if selected_id == "manual" and not self.host:
            await session.show_view("configure_manual")
            return

        if not self.host:
            raise ValueError(self._t("errors.host_required"))

        expected_id = None if selected_id == "manual" else str(selected_id)
        await session.show_view(
            await self._try_pair(expected_id=expected_id, prompt_key=True)
        )

    async def _on_pair_configure_manual(self, data: dict[str, Any]) -> str:
        """Store host/port, connect, and return the next pair view id."""
        self.host, self.port = self._parse_manual_connection(data)
        return await self._try_pair(expected_id=None, prompt_key=True)

    async def _on_pair_enter_key(self, data: dict[str, Any]) -> str:
        """Store the Noise PSK, connect, and return the next pair view id."""
        self.noise_psk = self._require_noise_psk(data)
        selected_id = (
            None if self.selected is None else self.selected.get("data", {}).get("id")
        )
        expected_id = None if selected_id in (None, "manual") else str(selected_id)
        return await self._try_pair(expected_id=expected_id, prompt_key=False)

    async def _on_get_device(self, _data: Any = None) -> HomeyEspHomeDeviceOption:
        """Return the mapped device for the custom add_device view."""
        if self.mapped_device is None:
            raise RuntimeError(self._t("errors.device_not_prepared"))
        if not self.mapped_device["capabilities"]:
            raise RuntimeError(self._t("errors.no_supported_entities"))
        return self.mapped_device

    async def _on_repair_show_view(self, view_id: str) -> None:
        if view_id == "configure_manual":
            await self._require_session().emit(
                "prefill", await self._repair_connection_values()
            )

    async def _repair_connection_values(self, _data: Any = None) -> dict[str, str]:
        device = self._device
        expected_id = self._expected_id
        assert device is not None and expected_id is not None
        store = device.get_store()
        port_value = device.get_setting("port") or store.get("port")
        return {
            "mac": str(device.get_setting("mac") or store.get("mac") or expected_id),
            "host": str(
                device.get_setting("host") or store.get("address") or ""
            ).strip(),
            "port": "" if port_value in (None, "") else str(port_value),
        }

    async def _on_repair_configure_manual(self, data: dict[str, Any]) -> str | None:
        self.host, self.port = self._parse_manual_connection(data)
        return await self._try_repair(prompt_key=True)

    async def _on_repair_enter_key(self, data: dict[str, Any]) -> str | None:
        self.noise_psk = self._require_noise_psk(data)
        return await self._try_repair(prompt_key=False)

    async def _on_enter_wifi(self, data: dict[str, Any]) -> str:
        """Provision Wi-Fi over BLE Improv and return the mDNS list view id."""
        if not self.peripheral_uuid:
            raise ValueError(self._t("errors.bluetooth_device_required"))
        ssid = str(data.get("ssid") or "").strip()
        if not ssid:
            raise ValueError(self._t("errors.ssid_required"))
        password = str(data.get("password") or "")

        known_ids = {
            str(device.get("data", {}).get("id") or "")
            for device in self._list_discovery_devices()
        }

        client = self._pair_improv_client()
        try:
            await client.connect(self.peripheral_uuid)
            await client.send_wifi(ssid, password)
        except Exception as err:
            await client.close()
            self._driver.error("ESPHome BLE Improv failed", err)
            key = (
                err.key
                if isinstance(err, ImprovError)
                else "errors.improv.bluetooth_connect"
            )
            raise ValueError(self._t(key)) from err

        await self._close_improv()
        await self._wait_for_new_discovery(known_ids)

        self.selected = None
        self.host = None
        self.noise_psk = None
        self.mapped_device = None
        self.listing_ble = False
        self.peripheral_uuid = None
        return "list_devices"

    async def _close_improv(self, _data: Any = None) -> None:
        """Drop any in-progress Improv GATT session."""
        client = self.improv_client
        self.improv_client = None
        if client is not None:
            await client.close()

    def _pair_improv_client(self) -> ImprovBleClient:
        """Reuse one Improv client for the pair session."""
        if self.improv_client is None:
            self.improv_client = ImprovBleClient(
                self._driver.homey, debug=self._driver.debug
            )
        return self.improv_client

    def _require_session(self) -> PairSession:
        session = self._session
        assert session is not None
        return session

    def _require_noise_psk(self, data: dict[str, Any]) -> str:
        raw_key = str(data.get("noise_psk") or "").strip()
        if not raw_key:
            raise ValueError(self._t("errors.encryption_key_required"))
        return raw_key

    async def _try_pair(self, *, expected_id: str | None, prompt_key: bool) -> str:
        """Probe and map; return next view id."""

        async def run() -> str:
            info, entities, services, psk = await self._probe()
            self.noise_psk = psk
            self.mapped_device = self._map_pair_payload(
                info, entities, services, psk, expected_id=expected_id
            )
            return "add_device"

        return await self._with_connect_errors(run, prompt_key=prompt_key)

    async def _try_repair(self, *, prompt_key: bool) -> str | None:
        """Probe and apply connection; return next view, or None when done."""
        device = self._device
        expected_id = self._expected_id
        session = self._require_session()
        assert device is not None and expected_id is not None

        async def run() -> None:
            info, _entities, _services, psk = await self._probe()
            self.noise_psk = psk
            if normalize_mac(info.mac_address) != normalize_mac(expected_id):
                raise ValueError(self._t("errors.device_mismatch"))
            assert self.host is not None
            await device.apply_connection(
                host=self.host,
                port=self.port,
                noise_psk=psk,
            )
            await session.done()

        return await self._with_connect_errors(run, prompt_key=prompt_key)

    async def _with_connect_errors[T](
        self,
        run: Callable[[], Awaitable[T]],
        *,
        prompt_key: bool,
    ) -> T | str:
        """Run connect work; map encryption misses to ``enter_key``."""
        try:
            return await run()
        except Exception as err:
            self.mapped_device = None
            if prompt_key and needs_encryption_key(err):
                return "enter_key"
            self._driver.error("ESPHome connect failed", err)
            if isinstance(err, ValueError):
                raise
            raise ValueError(self._t(error_key(err))) from err

    async def _probe(
        self,
    ) -> tuple[DeviceInfo, list[EntityInfo], list[UserService], str | None]:
        """Probe once; retry without PSK when the node is plaintext."""
        host = self.host
        if not host:
            raise ValueError(self._t("errors.host_required"))

        noise_psk = self.noise_psk
        while True:
            self._driver.debug(
                f"Probing {host}:{self.port} encrypted={noise_psk is not None}"
            )
            try:
                device_info, entities, services = await probe_esphome_device(
                    host,
                    self.port,
                    noise_psk=noise_psk,
                    client_info=self._driver.brand_profile.client_info,
                    debug=self._driver.debug,
                )
                return device_info, entities, services, noise_psk
            except EncryptionPlaintextAPIError:
                if not noise_psk:
                    raise
                noise_psk = None

    def _map_pair_payload(
        self,
        device_info: DeviceInfo,
        entities: list[EntityInfo],
        services: list[UserService],
        noise_psk: str | None,
        *,
        expected_id: str | None,
    ) -> HomeyEspHomeDeviceOption:
        """Validate identity and build the Homey pair-time payload."""
        assert self.host is not None

        if not self._driver.brand_profile.accepts_project(device_info.project_name):
            raise ValueError(self._t("errors.project_not_supported"))

        device_id = normalize_mac(device_info.mac_address)
        if not device_id:
            raise ValueError(self._t("errors.mac_missing"))

        if expected_id and normalize_mac(expected_id) != device_id:
            raise ValueError(self._t("errors.device_mismatch"))

        # Homey discovery matches paired devices by this id; keep the mDNS form.
        data_id = expected_id or device_id
        homey_device = DeviceEntityMapper.pair_option(
            device_info,
            host=self.host,
            port=self.port,
            noise_psk=noise_psk,
            data_id=data_id,
        )
        DeviceEntityMapper.map_device(
            entities,
            homey_device,
            profile=self._driver.brand_profile,
            services=services,
        )

        if not homey_device.get("class"):
            # Homey requires a class; mapping leaves it unset when no entity claims one.
            homey_device["class"] = "other"
        homey_device["store"]["auto_class"] = homey_device["class"]

        self._driver.debug(
            f"Mapped {homey_device['name']} ({data_id}) to "
            f"{len(homey_device['capabilities'])} capabilities, "
            f"class={homey_device['store']['auto_class']}"
        )
        return homey_device

    def _list_discovery_devices(self) -> list[ListDeviceProperties]:
        """Build list_devices rows from Homey's mDNS discovery strategy."""
        discovery_strategy = self._driver.get_discovery_strategy()
        if discovery_strategy is None:
            return []

        typed_strategy = cast(
            DiscoveryStrategy[DiscoveryResultMDNSSD],
            discovery_strategy,
        )
        discovery_results = typed_strategy.get_discovery_results()
        paired_ids = {
            normalize_mac(str(device.get_data().get("id") or ""))
            for device in self._driver.get_devices()
        }

        devices: list[ListDeviceProperties] = []
        for discovery_result in discovery_results.values():
            if normalize_mac(discovery_result.id) in paired_ids:
                continue
            if not self._driver.brand_profile.accepts_discovery(discovery_result.txt):
                continue

            friendly_name = discovery_result.txt.get("friendly_name")
            devices.append(
                {
                    "name": friendly_name or discovery_result.name or "ESPHome Device",
                    "data": {"id": discovery_result.id},
                    "store": {
                        "address": discovery_result.address,
                        "host": discovery_result.host,
                        "port": discovery_result.port or DEFAULT_API_PORT,
                    },
                    "capabilities": [],
                }
            )

        return devices

    async def _list_improv_pair_devices(self) -> list[ListDeviceProperties]:
        """Scan Homey's BLE radio for Improv peripherals."""
        try:
            found = await self._pair_improv_client().discover()
        except Exception as err:
            self._driver.error("ESPHome BLE Improv scan failed", err)
            raise ValueError(self._t("errors.improv.scan_failed")) from err

        if not found:
            raise ValueError(self._t("errors.improv.none_found"))

        return [
            {
                "name": device["name"],
                "data": {"id": device["uuid"]},
                "store": {"peripheralUuid": device["uuid"]},
                "capabilities": [],
            }
            for device in found
        ]

    async def _wait_for_new_discovery(self, known_ids: set[str]) -> None:
        """Give Homey's mDNS cache a moment to see the provisioned node."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _MDNS_WAIT_TIMEOUT_S
        while loop.time() < deadline:
            for device in self._list_discovery_devices():
                device_id = str(device.get("data", {}).get("id") or "")
                if device_id and device_id not in known_ids:
                    return
            await asyncio.sleep(_MDNS_POLL_S)

    def _parse_manual_connection(self, data: dict[str, Any]) -> tuple[str, int]:
        """Return host and port from the IP form."""
        raw_host = str(data.get("host") or "").strip()
        if not raw_host:
            raise ValueError(self._t("errors.host_required"))

        raw_port = data.get("port", DEFAULT_API_PORT)
        try:
            parsed_port = int(raw_port)
        except (TypeError, ValueError) as err:
            raise ValueError(self._t("errors.port_number")) from err
        if parsed_port < 1 or parsed_port > 65535:
            raise ValueError(self._t("errors.port_range"))

        return raw_host, parsed_port
