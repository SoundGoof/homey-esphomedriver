"""User-defined API action tests.

ESPHome's ``display:`` is not a native API entity, so user-defined actions are
the only way to push a value to a screen. These tests pin the action cache
lifecycle and argument coercion, and guard the failure mode that is hardest to
spot in the wild: ``APIClient.execute_service`` is a coroutine, so a
non-awaited call sends nothing and raises nothing.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aioesphomeapi import (
    APIClient,
    APIConnectionError,
    UserService,
    UserServiceArg,
    UserServiceArgType,
)

from homey_esphomedriver.esphome_client import EspHomeClient, SessionState


def _service(name: str, **args: UserServiceArgType) -> UserService:
    return UserService(
        name=name,
        key=hash(name) & 0xFFFFFFFF,
        args=[UserServiceArg(name=n, type=t) for n, t in args.items()],
    )


@pytest.fixture
def client() -> EspHomeClient:
    esp = EspHomeClient("10.0.0.1")
    esp._cli = MagicMock(spec=APIClient)
    esp._cli.execute_service = AsyncMock(return_value=None)
    # Actions are gated on READY like every other command, so a session that
    # has not reached it cannot run one.
    esp._state = SessionState.READY
    return esp


@pytest.mark.asyncio
async def test_execute_action_refuses_an_unready_session() -> None:
    """An unready session must not read as a missing action.

    The action list is per-connection and empty until the node answers, so
    without the gate this raises `KeyError` and the device layer renders it as
    "no action named X, check your YAML" — pointing at the wrong problem.
    """
    esp = EspHomeClient("10.0.0.1")
    esp._cli = MagicMock(spec=APIClient)
    esp._cli.execute_service = AsyncMock(return_value=None)

    with pytest.raises(APIConnectionError, match="not ready"):
        await esp.execute_action("act", {})
    esp._cli.execute_service.assert_not_awaited()


def test_execute_service_is_a_coroutine() -> None:
    """A non-awaited call is silent, so the contract must not regress."""
    assert inspect.iscoroutinefunction(APIClient.execute_service)


def test_actions_empty_while_disconnected(client: EspHomeClient) -> None:
    assert client.actions == ()


def test_actions_sorted(client: EspHomeClient) -> None:
    client._services = {n: _service(n) for n in ("homey_refresh", "homey_set_text")}
    assert client.actions == ("homey_refresh", "homey_set_text")


@pytest.mark.asyncio
async def test_execute_action_unknown_raises(client: EspHomeClient) -> None:
    with pytest.raises(KeyError):
        await client.execute_action("nope", {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arg_type", "given", "expected"),
    [
        (UserServiceArgType.STRING, 42, "42"),
        (UserServiceArgType.INT, "7", 7),
        (UserServiceArgType.FLOAT, "42.5", 42.5),
        (UserServiceArgType.BOOL, 1, True),
        (UserServiceArgType.INT_ARRAY, ["1", "2"], [1, 2]),
        (UserServiceArgType.FLOAT_ARRAY, [1, 2], [1.0, 2.0]),
        (UserServiceArgType.STRING_ARRAY, [1, 2], ["1", "2"]),
        (UserServiceArgType.BOOL_ARRAY, [1, 0], [True, False]),
    ],
)
async def test_execute_action_coerces_to_declared_type(
    client: EspHomeClient,
    arg_type: UserServiceArgType,
    given: Any,
    expected: Any,
) -> None:
    """Homey Flow inputs arrive as strings whatever the node declared."""
    client._services = {"act": _service("act", value=arg_type)}
    await client.execute_action("act", {"value": given})
    _, payload = client._cli.execute_service.await_args.args
    assert payload == {"value": expected}
    assert type(payload["value"]) is type(expected)


@pytest.mark.asyncio
async def test_execute_action_rejects_uncoercible_value(client: EspHomeClient) -> None:
    client._services = {"act": _service("act", value=UserServiceArgType.INT)}
    with pytest.raises(ValueError, match="Cannot coerce"):
        await client.execute_action("act", {"value": "not a number"})
    client._cli.execute_service.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_action_names_a_missing_argument(client: EspHomeClient) -> None:
    """aioesphomeapi indexes every declared arg, so a gap must be caught here.

    Leaving the argument out sends the node an incomplete payload and raises a
    bare ``KeyError`` from inside the library, which the device layer reports
    as an unknown action — pointing at the wrong problem entirely.
    """
    client._services = {
        "act": _service(
            "act",
            slot=UserServiceArgType.STRING,
            value=UserServiceArgType.STRING,
        )
    }
    with pytest.raises(ValueError, match="needs argument\\(s\\) 'value'"):
        await client.execute_action("act", {"slot": "line1"})
    client._cli.execute_service.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_action_sends_every_declared_argument(
    client: EspHomeClient,
) -> None:
    client._services = {
        "act": _service(
            "act",
            slot=UserServiceArgType.STRING,
            value=UserServiceArgType.STRING,
        )
    }
    await client.execute_action("act", {"slot": "line1", "value": "Hello"})
    _, payload = client._cli.execute_service.await_args.args
    assert payload == {"slot": "line1", "value": "Hello"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("True", True),
        (" 1 ", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("off", False),
        ("", False),
        (True, True),
        (0, False),
    ],
)
@pytest.mark.asyncio
async def test_execute_action_reads_bool_strings(
    client: EspHomeClient,
    raw: object,
    expected: bool,
) -> None:
    """Flow hands every value over as text, and ``bool("false")`` is ``True``."""
    client._services = {"act": _service("act", flag=UserServiceArgType.BOOL)}
    await client.execute_action("act", {"flag": raw})
    _, payload = client._cli.execute_service.await_args.args
    assert payload == {"flag": expected}


@pytest.mark.asyncio
async def test_execute_action_rejects_an_unreadable_bool(
    client: EspHomeClient,
) -> None:
    client._services = {"act": _service("act", flag=UserServiceArgType.BOOL)}
    with pytest.raises(ValueError, match="Cannot coerce 'maybe'"):
        await client.execute_action("act", {"flag": "maybe"})
    client._cli.execute_service.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_action_awaits_the_call(client: EspHomeClient) -> None:
    """Guards the silent-failure mode: called but never awaited."""
    client._services = {"act": _service("act")}
    await client.execute_action("act", {})
    client._cli.execute_service.assert_awaited_once()


@pytest.mark.asyncio
async def test_disconnect_clears_the_action_cache(client: EspHomeClient) -> None:
    client._services = {"act": _service("act")}
    await client._handle_disconnect(True)
    assert client.actions == ()


class TestParseActionArguments:
    """The generic Flow card takes free-typed JSON, so parsing is user-facing."""

    def test_blank_and_none_mean_no_arguments(self) -> None:
        from homey_esphomedriver.esphome_util import parse_action_arguments

        assert parse_action_arguments(None) == {}
        assert parse_action_arguments("") == {}
        assert parse_action_arguments("   ") == {}

    def test_parses_an_object(self) -> None:
        from homey_esphomedriver.esphome_util import parse_action_arguments

        assert parse_action_arguments('{"slot": "line1", "value": 3}') == {
            "slot": "line1",
            "value": 3,
        }

    def test_rejects_malformed_json_with_a_useful_message(self) -> None:
        from homey_esphomedriver.esphome_util import parse_action_arguments

        with pytest.raises(ValueError, match="not valid JSON"):
            parse_action_arguments("{slot: line1}")

    @pytest.mark.parametrize("payload", ["[1, 2]", '"text"', "42"])
    def test_rejects_non_objects(self, payload: str) -> None:
        from homey_esphomedriver.esphome_util import parse_action_arguments

        with pytest.raises(ValueError, match="must be a JSON object"):
            parse_action_arguments(payload)


class TestEntityKeyLookup:
    """Profiles name entities by object id; commands address them by key."""

    def test_key_lookup_after_connect(self, client: EspHomeClient) -> None:
        from aioesphomeapi import SensorInfo

        client._entities_by_object_id = {
            "temp_offset": SensorInfo(object_id="temp_offset", key=4242, name="x")
        }
        assert client.entity_key("temp_offset") == 4242
        assert client.entity_object_ids == ("temp_offset",)

    def test_unknown_object_id_is_none(self, client: EspHomeClient) -> None:
        assert client.entity_key("nope") is None

    def test_empty_while_disconnected(self, client: EspHomeClient) -> None:
        assert client.entity_object_ids == ()
        assert client.entity_key("anything") is None
