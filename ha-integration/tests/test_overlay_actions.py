"""Tests for one-time overlay action buttons."""

from custom_components.ha_tv_pip_plus.actions import PendingActionStore
from custom_components.ha_tv_pip_plus.client import (
    ShowCameraCommand,
    show_camera_payload,
)

UNLOCK = {
    "label": "Unlock Front Door",
    "action": "lock.unlock",
    "target": {"entity_id": ["lock.front_door_lock"]},
    "icon": "unlock",
    "confirm": True,
}


def test_store_mints_opaque_ids_and_hides_service_call() -> None:
    store = PendingActionStore()
    wire = store.replace_for_device("tv-1", [UNLOCK], ttl_seconds=30, now=0)

    assert len(wire) == 1
    assert wire[0]["label"] == "Unlock Front Door"
    assert wire[0]["icon"] == "unlock"
    assert wire[0]["confirm"] is True
    assert "lock.unlock" not in str(wire)
    assert "lock.front_door_lock" not in str(wire)
    assert len(wire[0]["id"]) >= 24


def test_action_is_single_use_and_bound_to_device() -> None:
    store = PendingActionStore()
    wire = store.replace_for_device("tv-1", [UNLOCK], ttl_seconds=30, now=0)
    action_id = wire[0]["id"]

    assert store.take(action_id, "tv-2", now=1) is None
    action = store.take(action_id, "tv-1", now=1)
    assert action is not None
    assert (action.domain, action.service) == ("lock", "unlock")
    assert action.service_data == {"entity_id": ["lock.front_door_lock"]}
    assert store.take(action_id, "tv-1", now=2) is None


def test_action_expires_after_popup_duration() -> None:
    store = PendingActionStore()
    wire = store.replace_for_device("tv-1", [UNLOCK], ttl_seconds=30, now=0)
    action_id = wire[0]["id"]

    assert store.take(action_id, "tv-1", now=60) is None


def test_new_popup_replaces_old_buttons() -> None:
    store = PendingActionStore()
    old_wire = store.replace_for_device("tv-1", [UNLOCK], ttl_seconds=30, now=0)
    old_id = old_wire[0]["id"]
    store.replace_for_device("tv-1", [UNLOCK], ttl_seconds=30, now=1)

    assert store.take(old_id, "tv-1", now=2) is None


def test_payload_includes_actions_only_with_callback() -> None:
    base = ShowCameraCommand(
        title="t", url="http://x/a.jpg", duration_seconds=30, enter_pip=True
    )
    assert "actions" not in show_camera_payload(base)

    from dataclasses import replace

    with_actions = replace(
        base,
        actions=({"id": "abc", "label": "Unlock", "confirm": True},),
        action_callback_url="http://ha.local:8123/api/ha_tv_pip_plus/action",
    )
    payload = show_camera_payload(with_actions)
    assert payload["actions"] == [{"id": "abc", "label": "Unlock", "confirm": True}]
    assert payload["actionCallbackUrl"] == "http://ha.local:8123/api/ha_tv_pip_plus/action"


class _State:
    def __init__(self, name: str, device_class: str | None = None) -> None:
        self.attributes = {"friendly_name": name}
        if device_class:
            self.attributes["device_class"] = device_class


class _States:
    def __init__(self, states: dict[str, _State]) -> None:
        self._states = states

    def get(self, entity_id: str) -> _State | None:
        return self._states.get(entity_id)


class _Hass:
    def __init__(self, pairings: dict, states: dict[str, _State]) -> None:
        from custom_components.ha_tv_pip_plus.actions import DATA_CAMERA_BUTTONS
        from custom_components.ha_tv_pip_plus.const import DOMAIN

        self.data = {DOMAIN: {DATA_CAMERA_BUTTONS: pairings}}
        self.states = _States(states)


def test_paired_lock_becomes_unlock_button() -> None:
    from custom_components.ha_tv_pip_plus.actions import default_action_spec

    hass = _Hass(
        {"camera.front": {"entity_id": "lock.front_door"}},
        {"lock.front_door": _State("Front door")},
    )
    spec = default_action_spec(hass, "camera.front")

    assert spec is not None
    assert spec["action"] == "lock.unlock"
    assert spec["target"] == {"entity_id": ["lock.front_door"]}
    assert spec["label"] == "Unlock Front door"
    assert spec["icon"] == "unlock"
    assert default_action_spec(hass, "camera.other") is None


def test_paired_garage_and_script_with_custom_label() -> None:
    from custom_components.ha_tv_pip_plus.actions import default_action_spec

    hass = _Hass(
        {
            "camera.garage": {"entity_id": "cover.door_1"},
            "camera.porch": {"entity_id": "script.let_in", "label": "Let them in"},
        },
        {"cover.door_1": _State("Garage", "garage")},
    )
    garage = default_action_spec(hass, "camera.garage")
    script = default_action_spec(hass, "camera.porch")

    assert garage is not None and script is not None
    assert (garage["action"], garage["label"], garage["icon"]) == (
        "cover.open_cover",
        "Open Garage",
        "garage",
    )
    assert script["action"] == "script.let_in"
    assert script["label"] == "Let them in"


def test_normalize_drops_bad_pairings() -> None:
    from custom_components.ha_tv_pip_plus.actions import normalize_camera_buttons

    pairings = normalize_camera_buttons(
        {
            "camera.a": {"entity_id": "lock.a"},
            "camera.b": {"entity_id": "light.b"},
            "sensor.c": {"entity_id": "lock.c"},
        }
    )
    assert pairings == {"camera.a": {"entity_id": "lock.a"}}


def test_settings_form_round_trip() -> None:
    from custom_components.ha_tv_pip_plus.config_flow import (
        camera_buttons_from_form,
        camera_buttons_to_form,
    )

    pairings = {
        "camera.front": {"entity_id": "lock.front", "label": "Let in"},
        "camera.garage": {"entity_id": "cover.door_1"},
    }
    parsed, error = camera_buttons_from_form(camera_buttons_to_form(pairings))
    assert error is None
    assert parsed == pairings

    _, error = camera_buttons_from_form({"camera_1": "camera.front"})
    assert error == "camera_button_incomplete"
