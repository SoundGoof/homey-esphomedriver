"""Display slot convention tests.

The queue and the coalescing window are the parts with real behaviour: a Flow
firing while the node sleeps must not be lost, and six field writes must not
mean six three-second ePaper refreshes.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from homey_esphomedriver.display_slots import (
    MARKER_CAPABILITY,
    MAX_FLUSH_ATTEMPTS,
    DisplaySlots,
    DisplaySlotWriter,
    autocomplete_rows,
    has_slots,
)
from homey_esphomedriver.esphome_util import (
    ACTION_MARKER_CAPABILITY,
    wanted_markers,
)


class Recorder:
    """Stands in for the node, recording the actions it is asked to run."""

    def __init__(self, fail_times: int = 0) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_times = fail_times

    async def __call__(self, name: str, data: dict[str, Any]) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("node went away")
        self.calls.append((name, data))


def _writer(
    recorder: Recorder,
    *,
    available: bool = True,
    coalesce: float = 0.01,
) -> DisplaySlotWriter:
    return DisplaySlotWriter(
        DisplaySlots(coalesce_seconds=coalesce),
        recorder,
        is_available=lambda: available,
    )


class TestSlotKinds:
    @pytest.mark.parametrize(
        ("object_id", "expected"),
        [
            ("homey_t_line1", "text"),
            ("homey_n_power", "number"),
            ("uptime", None),
            ("homey_other", None),
        ],
    )
    def test_kind_of(self, object_id: str, expected: str | None) -> None:
        assert DisplaySlots().kind_of(object_id) == expected

    def test_compose_defaults_need_no_configuration(self) -> None:
        assert DisplaySlots.from_compose(None) == DisplaySlots()
        assert DisplaySlots.from_compose({}) == DisplaySlots()

    def test_compose_overrides_are_read(self) -> None:
        slots = DisplaySlots.from_compose(
            {"textPrefix": "t_", "refreshAction": "redraw", "coalesceSeconds": 2.5}
        )
        assert slots.text_prefix == "t_"
        assert slots.refresh_action == "redraw"
        assert slots.coalesce_seconds == 2.5
        assert slots.number_prefix == "homey_n_"  # untouched keys keep defaults


class TestUnitSlots:
    """A numeric slot's companion text slot, used to label its unit."""

    @pytest.mark.parametrize(
        ("object_id", "expected"),
        [
            ("homey_n_slot3_value", "homey_t_slot3_unit"),
            # a numeric slot outside a tile has no caption or unit beside it
            ("homey_n_slot3", None),
            ("homey_n_primary", None),
            # text slots label nothing themselves
            ("homey_t_headline", None),
            ("sht_temperature", None),
        ],
    )
    def test_unit_slot_of(self, object_id: str, expected: str | None) -> None:
        assert DisplaySlots().unit_slot_of(object_id) == expected

    def test_suffix_is_configurable(self) -> None:
        slots = DisplaySlots.from_compose({"unitSuffix": "_uom"})
        assert slots.unit_slot_of("homey_n_slot1_value") == "homey_t_slot1_uom"


class TestQueueing:
    async def test_writes_are_coalesced_into_one_refresh(self) -> None:
        rec = Recorder()
        writer = _writer(rec)
        writer.write("homey_t_line1", "a", kind="text")
        writer.write("homey_t_line2", "b", kind="text")
        writer.write("homey_n_v", 1, kind="number")
        await asyncio.sleep(0.05)

        assert [name for name, _ in rec.calls] == [
            "homey_set_text",
            "homey_set_text",
            "homey_set_number",
            "homey_refresh",
        ]

    async def test_stale_duplicates_are_collapsed(self) -> None:
        """A clock field written repeatedly leaves one write, not hundreds."""
        rec = Recorder()
        writer = _writer(rec)
        for value in range(50):
            writer.write("homey_t_clock", str(value), kind="text")
        await asyncio.sleep(0.05)

        writes = [data for name, data in rec.calls if name == "homey_set_text"]
        assert writes == [{"slot": "homey_t_clock", "value": "49"}]

    async def test_offline_writes_are_queued_not_sent(self) -> None:
        rec = Recorder()
        writer = _writer(rec, available=False)
        writer.write("homey_t_line1", "queued", kind="text")
        await asyncio.sleep(0.05)

        assert rec.calls == []
        assert writer.pending == {"homey_t_line1": ("text", "queued")}

    async def test_queued_writes_flush_on_reconnect(self) -> None:
        rec = Recorder()
        available = False
        writer = DisplaySlotWriter(
            DisplaySlots(coalesce_seconds=0.01),
            rec,
            is_available=lambda: available,
        )
        writer.write("homey_t_line1", "queued", kind="text")
        await asyncio.sleep(0.05)
        assert rec.calls == []

        available = True
        await writer.flush()

        assert rec.calls == [
            ("homey_set_text", {"slot": "homey_t_line1", "value": "queued"}),
            ("homey_refresh", {}),
        ]
        assert writer.pending == {}

    async def test_failed_flush_requeues_values(self) -> None:
        """A drop mid-flush must not lose the values."""
        rec = Recorder(fail_times=1)
        writer = _writer(rec)
        writer.write("homey_t_line1", "keep me", kind="text")

        with pytest.raises(RuntimeError):
            await writer.flush()
        assert writer.pending == {"homey_t_line1": ("text", "keep me")}

        await writer.flush()
        assert ("homey_refresh", {}) in rec.calls

    async def test_requeue_does_not_clobber_a_newer_value(self) -> None:
        rec = Recorder(fail_times=1)
        writer = _writer(rec)
        writer.write("homey_t_line1", "old", kind="text")
        task = asyncio.ensure_future(writer.flush())
        writer.write("homey_t_line1", "new", kind="text")
        with pytest.raises(RuntimeError):
            await task

        assert writer.pending["homey_t_line1"] == ("text", "new")

    async def test_flush_with_nothing_pending_is_a_no_op(self) -> None:
        rec = Recorder()
        await _writer(rec).flush()
        assert rec.calls == []

    async def test_cancel_keeps_pending_values(self) -> None:
        rec = Recorder()
        writer = _writer(rec, coalesce=5.0)
        writer.write("homey_t_line1", "a", kind="text")
        writer.cancel()
        await asyncio.sleep(0.02)

        assert rec.calls == []
        assert writer.pending == {"homey_t_line1": ("text", "a")}


class TestSlotAutocomplete:
    """The picker must stay readable while the id it returns stays exact."""

    def test_name_drops_the_prefix_but_id_is_exact(self) -> None:
        rows = autocomplete_rows(("homey_n_col1",), "homey_n_", "")
        assert rows == [
            {"id": "homey_n_col1", "name": "col1", "description": "homey_n_col1"}
        ]

    def test_underscores_become_spaces(self) -> None:
        rows = autocomplete_rows(("homey_t_col1_label",), "homey_t_", "")
        assert rows[0]["name"] == "col1 label"
        assert rows[0]["id"] == "homey_t_col1_label"

    def test_query_filters_on_the_full_slot(self) -> None:
        slots = ("homey_n_col1", "homey_n_value1")
        rows = autocomplete_rows(slots, "homey_n_", "value")
        assert [r["id"] for r in rows] == ["homey_n_value1"]

    def test_a_slot_that_is_only_a_prefix_keeps_its_name(self) -> None:
        rows = autocomplete_rows(("homey_n_",), "homey_n_", "")
        assert rows[0]["name"] == "homey_n_"


class TestIdempotentWrites:
    """A timer Flow republishing everything must be free when nothing changed."""

    async def test_unchanged_value_is_not_sent_again(self) -> None:
        rec = Recorder()
        writer = _writer(rec)
        writer.write("homey_t_line1", "same", kind="text")
        await asyncio.sleep(0.05)
        first = len(rec.calls)

        writer.write("homey_t_line1", "same", kind="text")
        await asyncio.sleep(0.05)
        assert len(rec.calls) == first, "no second write and no second refresh"

    async def test_changed_value_is_sent(self) -> None:
        rec = Recorder()
        writer = _writer(rec)
        writer.write("homey_n_v", 21.5, kind="number")
        await asyncio.sleep(0.05)
        writer.write("homey_n_v", 21.6, kind="number")
        await asyncio.sleep(0.05)
        sent = [d for n, d in rec.calls if n == "homey_set_number"]
        assert [d["value"] for d in sent] == [21.5, 21.6]

    async def test_float_noise_counts_as_unchanged(self) -> None:
        rec = Recorder()
        writer = _writer(rec)
        writer.write("homey_n_v", 21.5, kind="number")
        await asyncio.sleep(0.05)
        writer.write("homey_n_v", 21.5 + 1e-12, kind="number")
        await asyncio.sleep(0.05)
        assert [n for n, _ in rec.calls].count("homey_set_number") == 1

    async def test_a_pending_write_is_still_replaceable(self) -> None:
        """Suppression must not block correcting a value that has not gone yet."""
        rec = Recorder(fail_times=0)
        writer = _writer(rec, coalesce=5.0)
        writer.write("homey_t_line1", "first", kind="text")
        writer.write("homey_t_line1", "second", kind="text")
        assert writer.pending["homey_t_line1"] == ("text", "second")

    async def test_replay_resends_what_the_node_lost(self) -> None:
        """A rebooted node comes back empty, so the last values go again."""
        rec = Recorder()
        writer = _writer(rec)
        writer.write("homey_t_line1", "Bedroom", kind="text")
        await asyncio.sleep(0.05)
        # the same value is normally suppressed: the node is believed to hold it
        writer.write("homey_t_line1", "Bedroom", kind="text")
        await asyncio.sleep(0.05)
        assert [n for n, _ in rec.calls].count("homey_set_text") == 1

        writer.replay()
        await asyncio.sleep(0.05)
        assert [n for n, _ in rec.calls].count("homey_set_text") == 2

    async def test_replay_keeps_a_newer_pending_value(self) -> None:
        """A Flow that fired while the node was away wins over the old value."""
        rec = Recorder(fail_times=1)
        writer = _writer(rec)
        writer.write("homey_n_value1", 1.0, kind="number")
        await asyncio.sleep(0.05)
        writer.write("homey_n_value1", 2.0, kind="number")
        writer.replay()
        assert writer.pending["homey_n_value1"] == ("number", 2.0)


class TestFlowFilterMarker:
    """Display cards are not capability-backed, so they need a marker to filter on.

    Without one Homey offers *Set display text* on every ESPHome device, including
    nodes with no screen at all.
    """

    @pytest.mark.parametrize(
        ("object_ids", "expected"),
        [
            (["homey_t_line1"], True),
            (["homey_n_battery"], True),
            (["uptime", "homey_t_caption"], True),
            (["uptime", "wifi_signal"], False),
            ([], False),
        ],
    )
    def test_has_slots(self, object_ids: list[str], expected: bool) -> None:
        assert has_slots(object_ids, DisplaySlots()) is expected

    def test_has_slots_follows_configured_prefixes(self) -> None:
        """A brand may rename the prefixes, and the marker must follow."""
        slots = DisplaySlots(text_prefix="scr_t_", number_prefix="scr_n_")
        assert has_slots(["scr_t_line1"], slots) is True
        assert has_slots(["homey_t_line1"], slots) is False


class TestTimerRearm:
    """A flush that leaves work behind must keep a timer to carry it.

    `_run_flush` clears the timer before flushing, so without a re-arm the
    re-queued writes strand until an unrelated `write()` happens along — which
    for a slot fed by a rarely-changing reading may be never.
    """

    @pytest.mark.asyncio
    async def test_a_failed_send_still_reaches_the_node(self) -> None:
        recorder = Recorder(fail_times=1)
        writer = _writer(recorder)
        writer.write("homey_t_line1", "Hello", kind="text")

        # one write() only: whatever delivers this is the writer's own retry
        await asyncio.sleep(0.1)

        assert not writer.pending
        sent = [
            (name, data.get("slot"), data.get("value")) for name, data in recorder.calls
        ]
        assert ("homey_set_text", "homey_t_line1", "Hello") in sent


class TestWantedMarkers:
    """One rule for which markers a node needs, shared by three call sites.

    Pair time, connect, and a live capability refresh all have to agree. When
    the refresh disagreed it planned the markers away, which silently
    unregistered every Flow card filtered on them.
    """

    def test_display_slots_ask_for_the_display_marker(self) -> None:
        assert wanted_markers(
            ["homey_t_line1", "uptime"],
            has_actions=False,
            slots=DisplaySlots(),
        ) == [MARKER_CAPABILITY]

    def test_actions_ask_for_the_action_marker(self) -> None:
        assert wanted_markers(
            ["uptime"],
            has_actions=True,
            slots=DisplaySlots(),
        ) == [ACTION_MARKER_CAPABILITY]

    def test_a_display_node_asks_for_both(self) -> None:
        assert wanted_markers(
            ["homey_n_battery"],
            has_actions=True,
            slots=DisplaySlots(),
        ) == [ACTION_MARKER_CAPABILITY, MARKER_CAPABILITY]

    def test_a_plain_node_asks_for_nothing(self) -> None:
        assert (
            wanted_markers(
                ["uptime", "wifi_signal"],
                has_actions=False,
                slots=DisplaySlots(),
            )
            == []
        )

    def test_it_follows_configured_prefixes(self) -> None:
        slots = DisplaySlots(text_prefix="scr_t_", number_prefix="scr_n_")
        assert wanted_markers(["scr_t_a"], has_actions=False, slots=slots) == [
            MARKER_CAPABILITY
        ]
        assert wanted_markers(["homey_t_a"], has_actions=False, slots=slots) == []


class TestGiveUp:
    """A permanently failing action must not retry forever.

    `_run_flush` re-arms whenever writes remain, which is right for a transient
    drop but turns a misnamed action into a log line per coalesce window for as
    long as the device is paired.
    """

    @pytest.mark.asyncio
    async def test_a_transient_failure_still_retries(self) -> None:
        recorder = Recorder(fail_times=1)
        writer = _writer(recorder)
        writer.write("homey_t_line1", "Hello", kind="text")
        await asyncio.sleep(0.1)
        assert not writer.pending
        assert recorder.calls

    @pytest.mark.asyncio
    async def test_a_permanent_failure_gives_up(self) -> None:
        recorder = Recorder(fail_times=1000)
        errors: list[Exception] = []
        writer = DisplaySlotWriter(
            DisplaySlots(coalesce_seconds=0.01),
            recorder,
            is_available=lambda: True,
            on_error=errors.append,
        )
        writer.write("homey_t_line1", "Hello", kind="text")
        await asyncio.sleep(0.3)

        assert not writer.pending, "the batch should have been dropped"
        assert len(errors) == MAX_FLUSH_ATTEMPTS, (
            f"expected exactly {MAX_FLUSH_ATTEMPTS} attempts, got {len(errors)}"
        )


class TestReschedule:
    """`cancel` hands the timer to the caller; `reschedule` hands it back."""

    @pytest.mark.asyncio
    async def test_reschedule_rearms_when_writes_remain(self) -> None:
        recorder = Recorder()
        writer = _writer(recorder, available=False)
        writer.write("homey_t_line1", "Hello", kind="text")
        writer.cancel()
        assert writer.pending

        writer.reschedule()
        assert not recorder.calls, "still offline, so nothing should have been sent"

    @pytest.mark.asyncio
    async def test_reschedule_is_a_noop_with_nothing_queued(self) -> None:
        writer = _writer(Recorder())
        writer.reschedule()
        assert not writer.pending


class TestReplayNeedsAReadySession:
    """`replay` cannot arm a timer while the session is not ready.

    `_schedule` refuses to arm when `is_available()` is False, so a caller that
    replays during a connect handler — before the session is marked READY —
    leaves the writes queued with nothing to carry them. The device therefore
    has to defer the replay until after readiness, not call it inline.
    """

    @pytest.mark.asyncio
    async def test_replay_while_unavailable_sends_nothing(self) -> None:
        recorder = Recorder()
        ready = {"v": False}
        writer = DisplaySlotWriter(
            DisplaySlots(coalesce_seconds=0.01),
            recorder,
            is_available=lambda: ready["v"],
        )
        writer.write("homey_t_line1", "Hello", kind="text")
        writer.replay()
        ready["v"] = True
        await asyncio.sleep(0.1)

        assert recorder.calls == []
        assert writer.pending

    @pytest.mark.asyncio
    async def test_replay_once_ready_sends(self) -> None:
        recorder = Recorder()
        writer = _writer(recorder)
        writer.write("homey_t_line1", "Hello", kind="text")
        writer.replay()
        await asyncio.sleep(0.1)

        assert not writer.pending
        assert recorder.calls


class TestOneBadSlotDoesNotBlockTheRest:
    """A tile has several fields; one rejected value must not freeze the others."""

    @pytest.mark.asyncio
    async def test_good_slots_are_sent_and_the_bad_one_is_requeued(self) -> None:
        sent: list[str] = []

        async def run(name: str, data: dict[str, Any]) -> None:
            slot = data.get("slot")
            if slot == "homey_t_bad":
                raise RuntimeError("node rejected it")
            sent.append(str(slot or name))

        errors: list[Exception] = []
        writer = DisplaySlotWriter(
            DisplaySlots(coalesce_seconds=0.01),
            run,
            is_available=lambda: True,
            on_error=errors.append,
        )
        writer.write("homey_t_good1", "a", kind="text")
        writer.write("homey_t_bad", "b", kind="text")
        writer.write("homey_t_good2", "c", kind="text")
        await asyncio.sleep(0.05)

        assert "homey_t_good1" in sent
        assert "homey_t_good2" in sent
        assert "homey_refresh" in sent, "the panel must still be refreshed"
        assert list(writer.pending) == ["homey_t_bad"]
        assert errors, "the failure is still reported"


class TestNothingIsDrawnUntilTheRefreshLands:
    """`_written` is the dedupe record, so it must mean "on the panel".

    Recording a value before the refresh succeeds outlives the batch: once the
    retries give up and drop it, `write` suppresses the resend of a value the
    panel never drew, and a reading that keeps repeating never appears.
    """

    @pytest.mark.asyncio
    async def test_a_dropped_batch_leaves_the_value_resendable(self) -> None:
        refresh_ok = {"v": False}
        sent: list[str] = []

        async def run(name: str, data: dict[str, Any]) -> None:
            if name == "homey_refresh":
                if not refresh_ok["v"]:
                    raise RuntimeError("panel busy")
                sent.append("refresh")
                return
            sent.append(str(data.get("slot")))

        errors: list[Exception] = []
        writer = DisplaySlotWriter(
            DisplaySlots(coalesce_seconds=0.01),
            run,
            is_available=lambda: True,
            on_error=errors.append,
        )
        writer.write("homey_t_line1", "Hello", kind="text")

        # every refresh fails, so the retries eventually give up on the batch
        await asyncio.sleep(0.4)
        assert len(errors) == MAX_FLUSH_ATTEMPTS
        assert not writer.pending, "the batch should have been dropped"

        # the panel never drew it, so the same value must still be sendable
        refresh_ok["v"] = True
        sent.clear()
        writer.write("homey_t_line1", "Hello", kind="text")
        await asyncio.sleep(0.08)

        assert "homey_t_line1" in sent, "value was wrongly deduped as already drawn"
        assert "refresh" in sent


class TestNoRefreshWhenNothingWasSent:
    """A GC16 pass costs ~3s; spending it on an empty batch is pure waste."""

    @pytest.mark.asyncio
    async def test_all_slots_failing_skips_the_refresh(self) -> None:
        seen: list[str] = []

        async def run(name: str, data: dict[str, Any]) -> None:
            seen.append(name)
            if name != "homey_refresh":
                raise RuntimeError("node rejected it")

        errors: list[Exception] = []
        writer = DisplaySlotWriter(
            DisplaySlots(coalesce_seconds=0.01),
            run,
            is_available=lambda: True,
            on_error=errors.append,
        )
        writer.write("homey_t_line1", "a", kind="text")
        await asyncio.sleep(0.05)

        assert "homey_refresh" not in seen
        assert errors
