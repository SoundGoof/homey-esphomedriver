"""Pure helpers for settings-page fields backed by an ESPHome entity.

Kept out of :mod:`esphome_device` so they can be imported and tested without
the Homey runtime, the same way :mod:`refresh` and :mod:`units` are.
"""

from __future__ import annotations

from math import isclose
from typing import Any


def setting_matches(wanted: Any, reported: Any) -> bool:
    """Whether the node already holds the value Homey wants.

    Both sides carry the entity's native type: ``wanted`` has been coerced by
    the entity's write command and ``reported`` is what the node sent. Floats
    compare with a tolerance, because a value that round-trips through Homey
    can differ in the last bit without being a different value.
    """
    if isinstance(wanted, float):
        try:
            return isclose(wanted, float(reported), abs_tol=1e-6)
        except TypeError, ValueError:
            return False
    return bool(wanted == reported)
