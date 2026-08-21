"""Small shared helpers used across ESPHome Homey apps."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

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
from aioesphomeapi.core import TimeoutAPIError

from homey_esphomedriver.display_slots import (
    MARKER_CAPABILITY as DISPLAY_MARKER_CAPABILITY,
)
from homey_esphomedriver.display_slots import DisplaySlots, has_slots

_DEBUG_TRUE = frozenset({"true", "1"})
_LIBRARY_LOGGERS = ("aioesphomeapi", "homey_esphomedriver")
_library_logs_attached = False


class _HomeyLogHandler(logging.Handler):
    """Forward stdlib log records to Homey ``log`` / ``error``."""

    def __init__(self, log: Callable[..., None], error: Callable[..., None]) -> None:
        super().__init__()
        self._log = log
        self._error = error

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            if record.levelno >= logging.WARNING:
                self._error(message)
            else:
                self._log(message)
        except Exception:
            self.handleError(record)


def attach_library_logs(log: Callable[..., None], error: Callable[..., None]) -> None:
    """Forward library logs into Homey.

    Idempotent across drivers so a second driver does not attach another handler.

    Args:
        log: Homey ``log`` callable.
        error: Homey ``error`` callable.
    """
    global _library_logs_attached
    if _library_logs_attached:
        return
    _library_logs_attached = True
    handler = _HomeyLogHandler(log, error)
    handler.setFormatter(logging.Formatter("%(message)s"))
    level = logging.DEBUG if is_debug_enabled() else logging.INFO
    for name in _LIBRARY_LOGGERS:
        logger = logging.getLogger(name)
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False


def normalize_mac(mac: str) -> str:
    """Match MAC identities regardless of separators / casing."""
    return mac.replace(":", "").replace("-", "").lower()


def is_debug_enabled() -> bool:
    """Return whether ``env.json`` ``DEBUG`` is ``true`` or ``1``.

    Homey env values are strings, so this does not treat a boolean ``True``.
    """
    from homey.homey import Homey

    value = Homey.env.get("DEBUG")
    return isinstance(value, str) and value.strip().lower() in _DEBUG_TRUE


def debug_log(log: Callable[..., None], *args: object) -> None:
    """Write a Homey log line prefixed with ``[dbg]`` when DEBUG is enabled.

    Args:
        log: Homey ``log`` callable.
        *args: Message fragments forwarded to ``log``.
    """
    if not is_debug_enabled():
        return
    log("[dbg]", *args)


def format_project(name: str, version: str) -> str:
    """Combine ESPHome project name/version for the device-info label."""
    if name and version:
        return f"{name} {version}"
    return name or version


def format_webserver(host: str, port: int) -> str:
    """Build a webserver URL when the node exposes one."""
    if port <= 0 or not host:
        return ""
    return f"http://{host}:{port}"


def device_info_settings(
    device_info: DeviceInfo,
    *,
    host: str,
    encrypted: bool,
) -> dict[str, bool | float | str | None]:
    """Homey Device Information settings derived from native API DeviceInfo."""
    return {
        "encryption": "Configured" if encrypted else "Not set",
        "manufacturer": device_info.manufacturer,
        "model": device_info.model,
        "mac": device_info.mac_address,
        "hostname": device_info.name,
        "esphome_version": device_info.esphome_version,
        "compilation_time": device_info.compilation_time,
        "project": format_project(
            device_info.project_name, device_info.project_version
        ),
        "bluetooth_mac": device_info.bluetooth_mac_address,
        "webserver": format_webserver(host, device_info.webserver_port),
        "deep_sleep": "Yes" if device_info.has_deep_sleep else "No",
    }


def requires_encryption(error: BaseException) -> bool:
    """Return whether the node rejected a plaintext connect and needs a Noise PSK."""
    return isinstance(error, RequiresEncryptionAPIError)


def invalid_encryption_key(error: BaseException) -> bool:
    """Return whether a provided Noise PSK was rejected (wrong or rotated key)."""
    return isinstance(error, InvalidEncryptionKeyAPIError)


def needs_encryption_key(error: BaseException) -> bool:
    """Return whether pairing should prompt for a Noise PSK."""
    return requires_encryption(error) or invalid_encryption_key(error)


def should_stop_reconnect(error: BaseException) -> bool:
    """Return whether retrying will not help until the user fixes auth or encryption."""
    return isinstance(
        error,
        (
            RequiresEncryptionAPIError,
            InvalidEncryptionKeyAPIError,
            InvalidAuthAPIError,
            EncryptionPlaintextAPIError,
        ),
    )


def is_device_mismatch(error: BaseException) -> bool:
    """Return whether the node at this address is not the paired device."""
    return isinstance(error, (BadMACAddressAPIError, BadNameAPIError))


def error_key(error: BaseException) -> str:
    """Map a native API exception to a locales key.

    Args:
        error: Exception raised by aioesphomeapi.

    Returns:
        Key such as ``errors.cannot_connect``.
    """
    if invalid_encryption_key(error):
        return "errors.invalid_encryption_key"
    if requires_encryption(error):
        return "errors.requires_encryption_key"
    if isinstance(error, InvalidAuthAPIError):
        return "errors.invalid_auth"
    if isinstance(error, EncryptionPlaintextAPIError):
        return "errors.encryption_plaintext"
    if isinstance(error, EncryptionHelloAPIError):
        return "errors.encryption_hello"
    if is_device_mismatch(error):
        return "errors.device_mismatch"
    if isinstance(error, ResolveAPIError):
        return "errors.resolve_error"
    if isinstance(error, TimeoutAPIError):
        return "errors.timeout"
    if isinstance(error, HandshakeAPIError):
        return "errors.handshake"
    return "errors.cannot_connect"


def format_uptime(seconds: float) -> str:
    """Format uptime seconds as ``90d 8h 48m``."""
    total = int(seconds)
    days = total // 86400
    hours = (total % 86400) // 3600
    minutes = (total % 3600) // 60

    parts: list[str] = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def format_date(value: str) -> str:
    """Format an ISO8601 date/timestamp as ``YYYY-MM-DD``."""
    return _parse_iso8601(value).date().isoformat()


def format_timestamp(value: str) -> str:
    """Format an ISO8601 timestamp as ``YYYY-MM-DD HH:MM:SS`` (local time)."""
    dt = _parse_iso8601(value)
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def format_event_type(event_type: str) -> str:
    """Humanize an ESPHome event_type for Homey titles and Flow tokens."""
    return " ".join(part.capitalize() for part in event_type.split("_"))


def _parse_iso8601(value: str) -> datetime:
    """Parse ESPHome text-sensor ISO8601 (``Z`` normalized for fromisoformat)."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


ACTION_MARKER_CAPABILITY = "esphome_action"
"""Hidden capability that puts the action card only on nodes declaring actions."""


def wanted_markers(
    object_ids: Iterable[str],
    *,
    has_actions: bool,
    slots: DisplaySlots,
) -> list[str]:
    """Flow ``$filter`` markers a node with these entities and actions needs.

    One rule, because it is consulted from three places that must agree: pair
    time, connect, and a live capability refresh. A refresh that does not know
    about a marker plans to remove it, which silently unregisters every card
    filtered on it.

    Args:
        object_ids: Entity object ids the node exposes.
        has_actions: Whether the node declares any user-defined API action.
        slots: Display-slot naming for the owning driver.
    """
    markers: list[str] = []
    if has_actions:
        markers.append(ACTION_MARKER_CAPABILITY)
    if has_slots(object_ids, slots):
        markers.append(DISPLAY_MARKER_CAPABILITY)
    return markers


def parse_action_arguments(raw: object) -> dict[str, Any]:
    """Parse a Flow card's JSON argument field into an action payload.

    The generic *Run an ESPHome action* card cannot know an action's variables
    at compose time, so arguments arrive as one JSON object typed by the user.
    Everything about that is fallible, so failures name the problem rather than
    surfacing a bare ``JSONDecodeError`` in a Flow.

    Args:
        raw: The card's ``arguments`` value; ``None`` or blank means no
            arguments, which is valid for actions that declare none.

    Returns:
        Argument values keyed by declared variable name. Values are left as
        parsed; ``EspHomeClient.execute_action`` coerces them to the types the
        node declared.

    Raises:
        ValueError: If the text is not valid JSON, or is not a JSON object.
    """
    if raw is None:
        return {}
    text = str(raw).strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as err:
        msg = f"Action arguments are not valid JSON: {err.msg} (position {err.pos})"
        raise ValueError(msg) from err
    if not isinstance(parsed, dict):
        msg = (
            "Action arguments must be a JSON object keyed by variable name, "
            f"not {type(parsed).__name__}"
        )
        raise ValueError(msg)
    return {str(key): value for key, value in parsed.items()}
