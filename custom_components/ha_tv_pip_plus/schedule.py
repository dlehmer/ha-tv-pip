"""Per-TV do-not-disturb and turn-the-TV-on schedules.

Everything lives in the TV's config entry options, so it is edited in Home
Assistant (the options dialog, or this TV's switch, select and time entities)
and never on the TV itself.

Do not disturb is on when any of these is true:
- this TV's own "Do not disturb" switch is on,
- a control entity you picked (any switch, input boolean, binary sensor...) is
  in the state you chose ("on" or "off"),
- the schedule is enabled and the current time is inside its window.

Popups sent during do not disturb are skipped unless the automation sets
`ignore_do_not_disturb: true`.

Waking the TV (through its power entity) can be never, always, or only between
two times.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

CONF_DND = "do_not_disturb"
CONF_DND_ENTITY = "do_not_disturb_entity"
CONF_DND_ENTITY_STATE = "do_not_disturb_entity_state"
CONF_DND_SCHEDULE = "do_not_disturb_schedule"
CONF_DND_START = "do_not_disturb_start"
CONF_DND_END = "do_not_disturb_end"
CONF_WAKE_MODE = "wake_tv"
CONF_WAKE_START = "wake_tv_start"
CONF_WAKE_END = "wake_tv_end"

DND_WHEN_ON = "on"
DND_WHEN_OFF = "off"
DND_ENTITY_STATES = (DND_WHEN_ON, DND_WHEN_OFF)

WAKE_NEVER = "never"
WAKE_ALWAYS = "always"
WAKE_SCHEDULED = "scheduled"
WAKE_MODES = (WAKE_NEVER, WAKE_ALWAYS, WAKE_SCHEDULED)

DEFAULT_DND_START = "22:00:00"
DEFAULT_DND_END = "07:00:00"
DEFAULT_WAKE_START = "07:00:00"
DEFAULT_WAKE_END = "22:00:00"

SCHEDULE_KEYS = (
    CONF_DND,
    CONF_DND_ENTITY,
    CONF_DND_ENTITY_STATE,
    CONF_DND_SCHEDULE,
    CONF_DND_START,
    CONF_DND_END,
    CONF_WAKE_MODE,
    CONF_WAKE_START,
    CONF_WAKE_END,
)


def parse_time(value: Any, fallback: str) -> time:
    """Read "HH:MM" or "HH:MM:SS" (or a `time`), falling back on bad input."""

    if isinstance(value, time):
        return value.replace(microsecond=0, tzinfo=None)
    for candidate in (value, fallback):
        try:
            parts = [int(part) for part in str(candidate).strip().split(":")]
            while len(parts) < 3:
                parts.append(0)
            return time(parts[0], parts[1], parts[2])
        except (TypeError, ValueError):
            continue
    return time(0, 0)


def format_time(value: Any, fallback: str) -> str:
    return parse_time(value, fallback).strftime("%H:%M:%S")


def in_window(now: time, start: time, end: time) -> bool:
    """True if `now` is in [start, end). Windows may wrap past midnight.

    A window whose start equals its end covers the whole day.
    """

    if start == end:
        return True
    if start < end:
        return start <= now < end
    return now >= start or now < end


def schedule_options(options: Any) -> dict[str, Any]:
    """The schedule settings in `options`, with defaults filled in."""

    options = options or {}
    wake_mode = str(options.get(CONF_WAKE_MODE) or "").strip()
    if wake_mode not in WAKE_MODES:
        # Before schedules existed, choosing a power entity meant "always".
        wake_mode = WAKE_ALWAYS
    entity_state = str(options.get(CONF_DND_ENTITY_STATE) or DND_WHEN_ON).strip()
    if entity_state not in DND_ENTITY_STATES:
        entity_state = DND_WHEN_ON
    return {
        CONF_DND: bool(options.get(CONF_DND, False)),
        CONF_DND_ENTITY: str(options.get(CONF_DND_ENTITY) or "").strip(),
        CONF_DND_ENTITY_STATE: entity_state,
        CONF_DND_SCHEDULE: bool(options.get(CONF_DND_SCHEDULE, False)),
        CONF_DND_START: format_time(options.get(CONF_DND_START), DEFAULT_DND_START),
        CONF_DND_END: format_time(options.get(CONF_DND_END), DEFAULT_DND_END),
        CONF_WAKE_MODE: wake_mode,
        CONF_WAKE_START: format_time(
            options.get(CONF_WAKE_START), DEFAULT_WAKE_START
        ),
        CONF_WAKE_END: format_time(options.get(CONF_WAKE_END), DEFAULT_WAKE_END),
    }


def local_now() -> datetime:
    """Home Assistant's local time (falls back to the system clock in tests)."""

    try:
        from homeassistant.util import dt as dt_util  # type: ignore[import-not-found]

        return dt_util.now()  # type: ignore[no-any-return]
    except (ImportError, ModuleNotFoundError):
        return datetime.now()


def dnd_reason(
    options: Any, now: datetime | None = None, *, hass: Any | None = None
) -> str | None:
    """Why do not disturb is on right now, or None.

    Returns "switch", "entity" or "schedule".
    """

    settings = schedule_options(options)
    if settings[CONF_DND]:
        return "switch"
    if control_entity_says_dnd(hass, settings):
        return "entity"
    if not settings[CONF_DND_SCHEDULE]:
        return None
    current = (now or local_now()).time().replace(microsecond=0, tzinfo=None)
    if in_window(
        current,
        parse_time(settings[CONF_DND_START], DEFAULT_DND_START),
        parse_time(settings[CONF_DND_END], DEFAULT_DND_END),
    ):
        return "schedule"
    return None


def control_entity_says_dnd(hass: Any | None, settings: dict[str, Any]) -> bool:
    """True if the chosen control entity is in the state that means DND."""

    entity_id = settings[CONF_DND_ENTITY]
    states = getattr(hass, "states", None)
    if not entity_id or states is None:
        return False
    state = states.get(entity_id)
    if state is None:
        return False
    return bool(str(state.state).lower() == settings[CONF_DND_ENTITY_STATE])


def dnd_active(
    options: Any, now: datetime | None = None, *, hass: Any | None = None
) -> bool:
    return dnd_reason(options, now, hass=hass) is not None


def may_wake_tv(options: Any, now: datetime | None = None) -> bool:
    """Whether a popup may turn this TV on right now."""

    settings = schedule_options(options)
    mode = settings[CONF_WAKE_MODE]
    if mode == WAKE_NEVER:
        return False
    if mode == WAKE_ALWAYS:
        return True
    current = (now or local_now()).time().replace(microsecond=0, tzinfo=None)
    return in_window(
        current,
        parse_time(settings[CONF_WAKE_START], DEFAULT_WAKE_START),
        parse_time(settings[CONF_WAKE_END], DEFAULT_WAKE_END),
    )


def options_changed_signal(entry_id: str) -> str:
    """Dispatcher signal sent when a TV's options change."""

    return f"ha_tv_pip_plus_options_changed_{entry_id}"


def async_notify_options_changed(hass: Any, entry_id: str) -> None:
    """Tell this TV's settings entities to show the current options."""

    try:
        from homeassistant.helpers.dispatcher import (  # type: ignore[import-not-found]
            async_dispatcher_send,
        )
    except (ImportError, ModuleNotFoundError):
        return
    async_dispatcher_send(hass, options_changed_signal(entry_id))


async def async_update_schedule_option(
    hass: Any, entry: Any, key: str, value: Any
) -> None:
    """Change one schedule setting on this TV's config entry."""

    options = dict(entry.options)
    options[key] = value
    hass.config_entries.async_update_entry(entry, options=options)
    async_notify_options_changed(hass, entry.entry_id)
