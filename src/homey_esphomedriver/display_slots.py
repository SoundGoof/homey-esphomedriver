"""Homey-fed display slots on an ESPHome node.

ESPHome's ``display:`` is not a native API entity, so no capability mapping can
reach a screen. The convention here closes that gap: the node declares ordinary
(non-internal) template sensors as *slots* and a small set of dispatching
user-defined actions, and Homey writes slot values through those actions.

Everything is contained in this module so the convention stays separable from
the core mapping: a brand app can drive it from an ``EspHomeDevice`` subclass
using only :meth:`EspHomeClient.execute_action`.

Two behaviours matter in practice and are the reason this is not a thin
wrapper. Writes are **queued** while the node is offline and flushed on
reconnect, because a Flow firing while the node sleeps or reboots must not be
lost. Writes are **coalesced** into a single refresh, because a full GC16
ePaper update takes about three seconds — six fields must not mean six
refreshes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

DEFAULT_TEXT_PREFIX = "homey_t_"
DEFAULT_NUMBER_PREFIX = "homey_n_"
DEFAULT_VALUE_SUFFIX = "_value"
DEFAULT_UNIT_SUFFIX = "_unit"
DEFAULT_COALESCE_SECONDS = 1.0

MAX_FLUSH_ATTEMPTS = 5
"""Consecutive failed flushes before a batch is dropped rather than retried."""

MARKER_CAPABILITY = "esphome_display"
"""Hidden capability that puts the display cards only on nodes with slots."""


def _coalesce_seconds(value: Any) -> float:
    """Read the coalesce window, falling back like every other compose key.

    A typo here would otherwise raise out of ``BrandProfile.from_manifest`` and
    take the whole driver down at init, for a tuning knob.
    """
    if value is None:
        return DEFAULT_COALESCE_SECONDS
    try:
        seconds = float(value)
    except TypeError, ValueError:
        return DEFAULT_COALESCE_SECONDS
    return seconds if seconds > 0 else DEFAULT_COALESCE_SECONDS


@dataclass(frozen=True, slots=True)
class DisplaySlots:
    """Names the node uses for the slot convention.

    Defaults match the documented convention, so a node built from the README
    needs no compose configuration. The keys exist so the convention is not
    hardcoded in core.
    """

    text_prefix: str = DEFAULT_TEXT_PREFIX
    """Object-id prefix marking a text slot."""

    number_prefix: str = DEFAULT_NUMBER_PREFIX
    """Object-id prefix marking a numeric slot."""

    set_text_action: str = "homey_set_text"
    set_number_action: str = "homey_set_number"
    refresh_action: str = "homey_refresh"

    slot_arg: str = "slot"
    """Action variable naming the target slot."""

    value_arg: str = "value"
    """Action variable carrying the value."""

    value_suffix: str = DEFAULT_VALUE_SUFFIX
    """Suffix a numeric slot may carry, so a tile's three slots read alike."""

    unit_suffix: str = DEFAULT_UNIT_SUFFIX
    """Suffix marking the text slot that labels a numeric slot's unit."""

    coalesce_seconds: float = DEFAULT_COALESCE_SECONDS
    """Window within which writes share one refresh."""

    @classmethod
    def from_compose(cls, data: Mapping[str, Any] | None) -> DisplaySlots:
        """Build from a ``driver.compose.json`` ``esphome.displaySlots`` object.

        Args:
            data: The ``displaySlots`` object, or ``None`` for defaults.
        """
        if not isinstance(data, Mapping):
            return cls()

        def pick(*names: str) -> Any:
            for name in names:
                if data.get(name) not in (None, ""):
                    return data[name]
            return None

        coalesce = pick("coalesceSeconds", "coalesce_seconds")
        return cls(
            text_prefix=str(pick("textPrefix", "text_prefix") or DEFAULT_TEXT_PREFIX),
            number_prefix=str(
                pick("numberPrefix", "number_prefix") or DEFAULT_NUMBER_PREFIX
            ),
            set_text_action=str(
                pick("setTextAction", "set_text_action") or "homey_set_text"
            ),
            set_number_action=str(
                pick("setNumberAction", "set_number_action") or "homey_set_number"
            ),
            refresh_action=str(
                pick("refreshAction", "refresh_action") or "homey_refresh"
            ),
            value_suffix=str(
                pick("valueSuffix", "value_suffix") or DEFAULT_VALUE_SUFFIX
            ),
            unit_suffix=str(pick("unitSuffix", "unit_suffix") or DEFAULT_UNIT_SUFFIX),
            slot_arg=str(pick("slotArg", "slot_arg") or "slot"),
            value_arg=str(pick("valueArg", "value_arg") or "value"),
            coalesce_seconds=_coalesce_seconds(coalesce),
        )

    def unit_slot_of(self, object_id: str) -> str | None:
        """Return the text slot labelling ``object_id``, or None.

        A tile names its three slots alike, so ``homey_n_slot3_value`` is
        labelled by ``homey_t_slot3_unit``. A numeric slot that is not part of
        a tile — one without the value suffix — has nothing to label and
        returns None. The node need not declare the unit slot either; callers
        check its slot list before writing, so a layout with units baked into
        the design stays valid.
        """
        if not object_id.startswith(self.number_prefix):
            return None
        if not object_id.endswith(self.value_suffix):
            return None
        stem = object_id[len(self.number_prefix) : -len(self.value_suffix)]
        return f"{self.text_prefix}{stem}{self.unit_suffix}"

    def kind_of(self, object_id: str) -> str | None:
        """Return ``"text"``, ``"number"``, or ``None`` for a non-slot entity."""
        if object_id.startswith(self.text_prefix):
            return "text"
        if object_id.startswith(self.number_prefix):
            return "number"
        return None


class ActionRunner(Protocol):
    """The only thing this module needs from a session."""

    async def __call__(self, name: str, data: dict[str, Any]) -> None: ...


class DisplaySlotWriter:
    """Queues, collapses and flushes slot writes for one node.

    Pending writes are keyed by slot, so a value superseded before the flush is
    dropped rather than sent — a Flow that writes a clock field every second
    while the node is offline leaves one write outstanding, not hundreds.
    """

    def __init__(
        self,
        slots: DisplaySlots,
        run_action: ActionRunner,
        *,
        is_available: Any,
        on_error: Any = None,
    ) -> None:
        """Create a writer.

        Args:
            slots: Naming configuration.
            run_action: Awaitable invoking a user-defined action on the node.
            is_available: Callable returning whether the node is connected.
            on_error: Optional callable for flush failures.
        """
        self._slots = slots
        self._run_action = run_action
        self._is_available = is_available
        self._on_error = on_error
        self._pending: dict[str, tuple[str, Any]] = {}
        self._written: dict[str, tuple[str, Any]] = {}
        self._timer: asyncio.TimerHandle | None = None
        self._flushing = False
        self._failures = 0

    @property
    def pending(self) -> Mapping[str, tuple[str, Any]]:
        """Outstanding writes keyed by slot; for tests and diagnostics."""
        return dict(self._pending)

    def write(self, slot: str, value: Any, *, kind: str) -> None:
        """Queue one slot write, replacing any unsent value for that slot.

        A write matching what the slot already holds is dropped. That makes a
        Flow which republishes everything on a timer free when nothing has
        changed: no action call, and no refresh, which matters because a full
        ePaper refresh takes seconds and the panel has finite refresh cycles.

        Args:
            slot: Slot object id.
            value: Value to publish.
            kind: ``"text"`` or ``"number"``.
        """
        if slot not in self._pending and self._is_unchanged(slot, value):
            return
        self._pending[slot] = (kind, value)
        self._schedule()

    def _is_unchanged(self, slot: str, value: Any) -> bool:
        """Whether the slot already holds this value.

        Numbers are compared with a tolerance: a Flow passing a float through
        Homey can round-trip to a value that differs in the last bit without
        being a different reading.
        """
        if slot not in self._written:
            return False
        previous = self._written[slot][1]
        if isinstance(value, (int, float)) and isinstance(previous, (int, float)):
            return abs(float(previous) - float(value)) < 1e-9
        return bool(previous == value)

    def _schedule(self) -> None:
        """Arm the coalescing timer, unless one is already pending."""
        if self._timer is not None or not self._is_available():
            return
        loop = asyncio.get_event_loop()
        self._timer = loop.call_later(
            self._slots.coalesce_seconds,
            lambda: asyncio.ensure_future(self._run_flush()),
        )

    async def _run_flush(self) -> None:
        self._timer = None
        try:
            await self.flush()
        except Exception as err:  # noqa: BLE001 - reported, never raised into the loop
            self._failures += 1
            if self._on_error is not None:
                self._on_error(err)
            if self._failures >= MAX_FLUSH_ATTEMPTS:
                # A misnamed or missing action fails identically every time, so
                # retrying costs a log line per second forever for writes the
                # node will never accept. Give up on this batch; the next
                # `write` starts a fresh one.
                self._pending = {}
                self._failures = 0
                return
        else:
            self._failures = 0
        # `flush` re-queues what it could not send and returns early while
        # another flush is in flight. Both leave work behind with no timer left
        # to carry it, so anything still pending needs a fresh one or it waits
        # for an unrelated write that may never come.
        if self._pending:
            self._schedule()

    async def flush(self) -> None:
        """Send every pending write, then one refresh.

        Kept re-entrant-safe: a reconnect flush and a timer flush can race.
        Slots are sent independently: a value the node rejects is re-queued on
        its own rather than holding back the rest of the batch, because a tile
        has several fields and one bad reading must not freeze the others.
        Values are restored to the queue if the send fails, so a drop mid-flush
        does not lose them.

        Raises:
            RuntimeError: If any slot failed, after the rest have been sent, so
                the caller can count the failure and eventually give up.
        """
        if self._flushing or not self._pending or not self._is_available():
            return
        self._flushing = True
        batch = self._pending
        self._pending = {}
        failed: dict[str, tuple[str, Any]] = {}
        sent: dict[str, tuple[str, Any]] = {}
        errors: list[Exception] = []
        try:
            for slot, (kind, value) in batch.items():
                action = (
                    self._slots.set_text_action
                    if kind == "text"
                    else self._slots.set_number_action
                )
                try:
                    await self._run_action(
                        action,
                        {self._slots.slot_arg: slot, self._slots.value_arg: value},
                    )
                except Exception as err:  # noqa: BLE001 - re-queued and reported
                    failed[slot] = (kind, value)
                    errors.append(err)
                    continue
                sent[slot] = (kind, value)
            if not sent:
                # Nothing reached the node, so there is nothing to draw. A GC16
                # pass costs about three seconds and the retries would spend it
                # again on every attempt.
                raise RuntimeError("every slot write in the batch failed")
            await self._run_action(self._slots.refresh_action, {})
        except Exception:
            # Nothing is on the panel until the refresh lands, so what was sent
            # must not be recorded as drawn — `write` dedupes against
            # `_written`, and a value wrongly recorded there is never resent.
            # Re-queue only slots the caller has not since overwritten.
            for slot, entry in batch.items():
                self._pending.setdefault(slot, entry)
            raise
        else:
            self._written.update(sent)
        finally:
            self._flushing = False

        for slot, entry in failed.items():
            self._pending.setdefault(slot, entry)
        if errors:
            names = ", ".join(sorted(failed))
            msg = f"{len(errors)} slot write(s) failed: {names}"
            raise RuntimeError(msg) from errors[0]

    def replay(self) -> None:
        """Re-send everything the node was last told, after it reconnects.

        A node that rebooted comes back with empty slots, and the panel keeps
        showing the last image until something writes again — a reading that
        rarely changes, a battery percentage or a caption, may never write and
        the tile stays blank indefinitely. Re-queueing what was last sent
        restores the panel without waiting for the source to change.

        Values still pending win: they are newer than anything already sent.
        A reconnect where the node did not reboot costs one refresh, which is
        the price of not being able to tell the two cases apart.
        """
        for slot, entry in self._written.items():
            self._pending.setdefault(slot, entry)
        self._written.clear()
        self._schedule()

    def cancel(self) -> None:
        """Drop any armed timer; pending writes survive for the next flush."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def reschedule(self) -> None:
        """Re-arm the timer if writes are still queued.

        The counterpart to :meth:`cancel`: a caller that took the timer away
        and then flushed has to hand back whatever the flush could not send.
        """
        if self._pending:
            self._schedule()


def has_slots(object_ids: Iterable[str], slots: DisplaySlots) -> bool:
    """Whether any entity object id names a display slot."""
    return any(slots.kind_of(object_id) is not None for object_id in object_ids)


def autocomplete_rows(
    slots: tuple[str, ...],
    prefix: str,
    query: str,
) -> list[dict[str, str]]:
    """Build Flow-card autocomplete rows for a set of slots.

    The returned ``id`` is the slot exactly as the node declares it, since that
    is what the dispatcher resolves. ``name`` drops the prefix — it is noise in
    a picker where every entry carries it — and the raw slot is kept as
    ``description`` so the mapping back to the YAML is never ambiguous.

    Args:
        slots: Slot object ids, already filtered by kind.
        prefix: The kind's prefix, stripped from the display name.
        query: Typed filter, matched case-insensitively against the full slot.
    """
    needle = query.casefold()
    return [
        {
            "id": slot,
            "name": slot[len(prefix) :].replace("_", " ") or slot,
            "description": slot,
        }
        for slot in slots
        if needle in slot.casefold()
    ]
