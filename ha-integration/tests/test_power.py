"""Tests for turning TVs on for popups and back off afterwards."""

import asyncio
import types
from typing import Any

from custom_components.ha_tv_pip_plus import power


class _Services:
    def __init__(self, states: "_States") -> None:
        self.calls: list[tuple[str, str]] = []
        self._states = states

    async def async_call(
        self, domain: str, service: str, data: dict[str, Any], blocking: bool
    ) -> None:
        self.calls.append((service, data["entity_id"]))
        self._states.set(data["entity_id"], "on" if service == "turn_on" else "off")


class _States:
    def __init__(self) -> None:
        self._states: dict[str, Any] = {}

    def set(self, entity_id: str, state: str, **attributes: Any) -> None:
        self._states[entity_id] = types.SimpleNamespace(
            state=state, attributes=attributes
        )

    def get(self, entity_id: str) -> Any:
        return self._states.get(entity_id)


def _hass() -> Any:
    states = _States()
    return types.SimpleNamespace(states=states, services=_Services(states))


def _fast(monkeypatch: Any) -> None:
    monkeypatch.setattr(power, "POWER_ON_SETTLE_S", 0)
    monkeypatch.setattr(power, "POPUP_CHECK_INTERVAL_S", 0)


def test_turns_on_only_when_off(monkeypatch: Any) -> None:
    _fast(monkeypatch)
    hass = _hass()
    hass.states.set("remote.tv", "on")
    assert asyncio.run(power.async_power_on_for_popup(hass, "remote.tv")) is None
    assert hass.services.calls == []

    hass.states.set("remote.tv", "off")
    assert asyncio.run(power.async_power_on_for_popup(hass, "remote.tv")) is not None
    assert hass.services.calls == [("turn_on", "remote.tv")]
    assert asyncio.run(power.async_power_on_for_popup(hass, None)) is None


def test_turns_back_off_when_popup_gone(monkeypatch: Any) -> None:
    _fast(monkeypatch)
    hass = _hass()
    hass.states.set("remote.tv", "on", current_activity="launcher")
    shown = iter([True, False])

    async def popup_is_showing() -> bool:
        return next(shown)

    asyncio.run(
        power.async_power_off_after_popup(
            hass,
            "remote.tv",
            power.activity_of(hass.states.get("remote.tv")),
            first_check_after_s=0,
            give_up_after_s=60,
            popup_is_showing=popup_is_showing,
        )
    )
    assert hass.services.calls == [("turn_off", "remote.tv")]


def test_leaves_tv_on_when_someone_started_watching(monkeypatch: Any) -> None:
    _fast(monkeypatch)
    hass = _hass()
    hass.states.set("remote.tv", "on", current_activity="launcher")
    activity = power.activity_of(hass.states.get("remote.tv"))
    hass.states.set("remote.tv", "on", current_activity="com.netflix.ninja")

    async def popup_is_showing() -> bool:
        return False

    asyncio.run(
        power.async_power_off_after_popup(
            hass,
            "remote.tv",
            activity,
            first_check_after_s=0,
            give_up_after_s=60,
            popup_is_showing=popup_is_showing,
        )
    )
    assert hass.services.calls == []
