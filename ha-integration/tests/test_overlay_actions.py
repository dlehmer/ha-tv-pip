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
    def __init__(
        self, pairings: dict[str, dict[str, str]], states: dict[str, _State]
    ) -> None:
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


def test_manually_added_receiver_is_found_by_pairing_token() -> None:
    import types

    from custom_components.ha_tv_pip_plus.const import (
        CONF_DEVICE_ID,
        CONF_TOKEN,
        DOMAIN,
    )
    from custom_components.ha_tv_pip_plus.remote import device_id_for_token

    entry = types.SimpleNamespace(
        data={CONF_DEVICE_ID: "manual-10.0.1.248-8767", CONF_TOKEN: "secret-token"}
    )
    hass = types.SimpleNamespace(data={DOMAIN: {"entries": {"e1": entry}}})

    # The TV reports its hardware id, which differs from the manual entry's id;
    # the token alone identifies the entry.
    assert device_id_for_token(hass, "secret-token") == "manual-10.0.1.248-8767"
    assert device_id_for_token(hass, "wrong-token") is None
    assert device_id_for_token(hass, "") is None

    store = PendingActionStore()
    wire = store.replace_for_device(
        "manual-10.0.1.248-8767", [UNLOCK], ttl_seconds=30, now=0
    )
    assert store.take(wire[0]["id"], "manual-10.0.1.248-8767", now=1) is not None


def test_unlock_offers_lock_and_watches_for_locked() -> None:
    from custom_components.ha_tv_pip_plus.actions import default_action_spec

    hass = _Hass(
        {"camera.front": {"entity_id": "lock.front_door"}},
        {"lock.front_door": _State("Front door")},
    )
    spec = default_action_spec(hass, "camera.front")
    assert spec is not None
    assert spec["follow_up"]["action"] == "lock.lock"
    assert spec["follow_up"]["label"] == "Lock Front door"
    assert spec["follow_up"]["confirm"] is False
    assert spec["watch_entity"] == "lock.front_door"
    assert spec["secured_state"] == "locked"

    store = PendingActionStore()
    action_id = store.replace_for_device("tv", [spec], ttl_seconds=30, now=0)[0]["id"]
    action = store.take(action_id, "tv", now=1)
    assert action is not None and action.follow_up is not None
    assert action.secured_state == "locked"


def test_open_garage_offers_close_with_confirmation() -> None:
    from custom_components.ha_tv_pip_plus.actions import default_action_spec

    hass = _Hass(
        {"camera.garage": {"entity_id": "cover.door_1"}},
        {"cover.door_1": _State("Garage", "garage")},
    )
    spec = default_action_spec(hass, "camera.garage")
    assert spec is not None
    assert spec["follow_up"]["action"] == "cover.close_cover"
    assert spec["follow_up"]["label"] == "Close Garage"
    assert spec["follow_up"]["confirm"] is True
    assert spec["secured_state"] == "closed"


def test_follow_up_mirrors_custom_label() -> None:
    from custom_components.ha_tv_pip_plus.actions import (
        default_action_spec,
        mirrored_label,
    )

    assert mirrored_label("Unlock Front Door", "x") == "Lock Front Door"
    assert mirrored_label("Open Garage", "x") == "Close Garage"
    assert mirrored_label("Let them in", "Lock Front door") == "Lock Front door"

    hass = _Hass(
        {"camera.front": {"entity_id": "lock.l", "label": "Unlock Front Door"}},
        {"lock.l": _State("Front door lock")},
    )
    spec = default_action_spec(hass, "camera.front")
    assert spec is not None
    assert spec["follow_up"]["label"] == "Lock Front Door"


def _unlock(label: str) -> list[dict[str, object]]:
    return [{"label": label, "action": "lock.unlock"}]


def test_each_popup_keeps_its_own_buttons() -> None:
    store = PendingActionStore()
    front = store.replace_for_device(
        "tv", _unlock("Front"), ttl_seconds=30, popup_key="camera.front", now=0
    )
    garage = store.replace_for_device(
        "tv", _unlock("Garage"), ttl_seconds=30, popup_key="camera.garage", now=1
    )

    # The garage popup didn't cancel the front door's button.
    assert store.take(front[0]["id"], "tv", now=2) is not None
    assert store.take(garage[0]["id"], "tv", now=2) is not None


def test_same_camera_replaces_its_buttons_and_oldest_popup_drops_out() -> None:
    store = PendingActionStore()
    first = store.replace_for_device(
        "tv", _unlock("A"), ttl_seconds=30, popup_key="camera.a", now=0
    )
    again = store.replace_for_device(
        "tv", _unlock("A"), ttl_seconds=30, popup_key="camera.a", now=0
    )
    assert store.take(first[0]["id"], "tv", now=0) is None

    store.replace_for_device(
        "tv", _unlock("Z"), ttl_seconds=30, popup_key="camera.z", now=0
    )
    b = store.replace_for_device(
        "tv", _unlock("B"), ttl_seconds=30, popup_key="camera.b", now=0
    )
    c = store.replace_for_device(
        "tv", _unlock("C"), ttl_seconds=30, popup_key="camera.c", now=0
    )
    d = store.replace_for_device(
        "tv", _unlock("D"), ttl_seconds=30, popup_key="camera.d", now=0
    )

    # Only four popups fit: A (the oldest) lost its button.
    assert store.take(again[0]["id"], "tv", now=0) is None
    for wire in (b, c, d):
        assert store.take(wire[0]["id"], "tv", now=0) is not None


def test_payload_carries_popup_key_and_dim() -> None:
    from custom_components.ha_tv_pip_plus.client import (
        ShowCameraCommand,
        show_camera_payload,
    )

    payload = show_camera_payload(
        ShowCameraCommand(
            title="Door",
            url="https://ha/x.m3u8",
            duration_seconds=30,
            enter_pip=True,
            popup_key="camera.front",
            dim_percent=30,
        )
    )
    assert payload["popupKey"] == "camera.front"
    assert payload["dimPercent"] == 30
