"""Turn a TV on for a popup and back off afterwards, through Home Assistant.

Each receiver can name a "power entity" (for example the TV's Android TV Remote
`remote.*` or `media_player.*`). If that entity is off when a popup is shown, the
TV is turned on first and turned off again once the popup is gone. A TV that was
already on is never turned off, and neither is one that someone started using
while the popup was up.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .const import CONF_POWER_ENTITY

_LOGGER = logging.getLogger(__name__)

OFF_STATES = {"off", "standby"}
UNKNOWN_STATES = {"unavailable", "unknown"}
# media_player states that mean someone is watching something.
IN_USE_STATES = {"playing", "paused", "buffering"}
POWER_ON_TIMEOUT_S = 20.0
POWER_ON_SETTLE_S = 4.0
POPUP_CHECK_INTERVAL_S = 10.0
ACTIVITY_ATTRIBUTES = ("current_activity", "app_id", "app_name", "source")


def power_entity_for(options: Any) -> str | None:
    entity_id = str((options or {}).get(CONF_POWER_ENTITY) or "").strip()
    return entity_id or None


def is_off(state: Any) -> bool:
    return state is not None and str(state.state) in OFF_STATES


def activity_of(state: Any) -> tuple[Any, ...]:
    """What the TV is showing, as far as its entity tells (to spot someone using it)."""

    attributes = getattr(state, "attributes", {}) or {}
    return tuple(attributes.get(name) for name in ACTIVITY_ATTRIBUTES)


async def async_power_on_for_popup(
    hass: Any, entity_id: str | None
) -> tuple[Any, ...] | None:
    """Turn the TV on if it is off. Returns its activity snapshot if we turned it on."""

    if not entity_id:
        return None
    if not is_off(hass.states.get(entity_id)):
        return None
    try:
        await hass.services.async_call(
            "homeassistant", "turn_on", {"entity_id": entity_id}, blocking=True
        )
    except Exception as error:  # noqa: BLE001
        _LOGGER.warning("Could not turn on %s for a popup: %s", entity_id, error)
        return None

    deadline = time.monotonic() + POWER_ON_TIMEOUT_S
    while time.monotonic() < deadline:
        state = hass.states.get(entity_id)
        if state is not None and str(state.state) not in OFF_STATES | UNKNOWN_STATES:
            break
        await asyncio.sleep(0.5)
    # Give the TV a moment to finish waking (HDMI-CEC, the app's local server).
    await asyncio.sleep(POWER_ON_SETTLE_S)
    _LOGGER.info("Turned on %s for a popup", entity_id)
    return activity_of(hass.states.get(entity_id))


async def async_power_off_after_popup(
    hass: Any,
    entity_id: str,
    activity_after_wake: tuple[Any, ...],
    *,
    first_check_after_s: float,
    give_up_after_s: float,
    popup_is_showing: Any,
) -> None:
    """Wait until the popup is gone, then turn the TV back off (if nobody's using it).

    `popup_is_showing` is an async callable returning True while the popup is up.
    """

    await asyncio.sleep(max(first_check_after_s, 0))
    deadline = time.monotonic() + give_up_after_s
    while time.monotonic() < deadline:
        try:
            showing = bool(await popup_is_showing())
        except Exception:  # noqa: BLE001 - an unreachable TV isn't showing anything
            showing = False
        if not showing:
            break
        await asyncio.sleep(POPUP_CHECK_INTERVAL_S)

    state = hass.states.get(entity_id)
    if state is None or is_off(state):
        return
    if str(state.state) in IN_USE_STATES or activity_of(state) != activity_after_wake:
        _LOGGER.info("Leaving %s on: it is in use", entity_id)
        return
    try:
        await hass.services.async_call(
            "homeassistant", "turn_off", {"entity_id": entity_id}, blocking=True
        )
        _LOGGER.info("Turned %s back off after the popup", entity_id)
    except Exception as error:  # noqa: BLE001
        _LOGGER.warning("Could not turn %s back off: %s", entity_id, error)
