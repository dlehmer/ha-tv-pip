"""Tests for per-TV do-not-disturb and turn-on schedules."""

import asyncio
import types
from datetime import datetime, time
from typing import Any

from custom_components.ha_tv_pip_plus.schedule import (
    CONF_DND,
    CONF_DND_END,
    CONF_DND_ENTITY,
    CONF_DND_ENTITY_STATE,
    CONF_DND_SCHEDULE,
    CONF_DND_START,
    CONF_WAKE_END,
    CONF_WAKE_MODE,
    CONF_WAKE_START,
    async_update_schedule_option,
    dnd_reason,
    in_window,
    may_wake_tv,
    parse_time,
    schedule_options,
)


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 26, hour, minute)


def _hass(states: dict[str, str]) -> Any:
    return types.SimpleNamespace(
        states=types.SimpleNamespace(
            get=lambda entity_id: (
                types.SimpleNamespace(state=states[entity_id])
                if entity_id in states
                else None
            )
        )
    )


def test_windows_handle_same_day_overnight_and_whole_day() -> None:
    assert in_window(time(9), time(8), time(17))
    assert not in_window(time(17), time(8), time(17))
    assert in_window(time(23), time(22), time(7))
    assert in_window(time(6, 59), time(22), time(7))
    assert not in_window(time(12), time(22), time(7))
    assert in_window(time(3), time(5), time(5))


def test_parse_time_accepts_short_forms_and_falls_back() -> None:
    assert parse_time("7:30", "00:00") == time(7, 30)
    assert parse_time("garbage", "22:00:00") == time(22)
    assert parse_time(time(6, 5, 4, 99), "00:00") == time(6, 5, 4)


def test_defaults_keep_old_behaviour() -> None:
    settings = schedule_options({})
    assert settings[CONF_DND] is False
    assert settings[CONF_DND_SCHEDULE] is False
    assert settings[CONF_WAKE_MODE] == "always"
    assert dnd_reason({}, _at(23)) is None
    assert may_wake_tv({}, _at(3))


def test_manual_switch_wins() -> None:
    assert dnd_reason({CONF_DND: True}, _at(12)) == "switch"


def test_schedule_only_inside_its_hours() -> None:
    options = {
        CONF_DND_SCHEDULE: True,
        CONF_DND_START: "22:00:00",
        CONF_DND_END: "07:00:00",
    }
    assert dnd_reason(options, _at(23)) == "schedule"
    assert dnd_reason(options, _at(6, 30)) == "schedule"
    assert dnd_reason(options, _at(12)) is None
    assert dnd_reason({**options, CONF_DND_SCHEDULE: False}, _at(23)) is None


def test_control_entity_with_chosen_polarity() -> None:
    when_on = {CONF_DND_ENTITY: "input_boolean.quiet", CONF_DND_ENTITY_STATE: "on"}
    when_off = {**when_on, CONF_DND_ENTITY_STATE: "off"}
    quiet_on = _hass({"input_boolean.quiet": "on"})
    quiet_off = _hass({"input_boolean.quiet": "off"})

    assert dnd_reason(when_on, _at(12), hass=quiet_on) == "entity"
    assert dnd_reason(when_on, _at(12), hass=quiet_off) is None
    assert dnd_reason(when_off, _at(12), hass=quiet_off) == "entity"
    assert dnd_reason(when_off, _at(12), hass=quiet_on) is None
    assert dnd_reason(when_on, _at(12), hass=_hass({})) is None
    assert dnd_reason(when_on, _at(12)) is None


def test_wake_modes() -> None:
    assert not may_wake_tv({CONF_WAKE_MODE: "never"}, _at(12))
    scheduled = {
        CONF_WAKE_MODE: "scheduled",
        CONF_WAKE_START: "08:00",
        CONF_WAKE_END: "21:00",
    }
    assert may_wake_tv(scheduled, _at(12))
    assert not may_wake_tv(scheduled, _at(22))


def test_update_option_writes_entry_options() -> None:
    updates: list[dict[str, Any]] = []
    entry = types.SimpleNamespace(options={"x": 1}, entry_id="e")
    hass = types.SimpleNamespace(
        config_entries=types.SimpleNamespace(
            async_update_entry=lambda entry, options: updates.append(options)
        )
    )

    asyncio.run(async_update_schedule_option(hass, entry, CONF_DND, True))

    assert updates == [{"x": 1, CONF_DND: True}]
