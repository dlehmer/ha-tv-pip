"""Tests for config-flow helpers."""

# ruff: noqa: E402, I001

import asyncio
import sys
import types
from dataclasses import dataclass
from typing import Any

import pytest

homeassistant = types.ModuleType("homeassistant")
config_entries = types.ModuleType("homeassistant.config_entries")
voluptuous = types.ModuleType("voluptuous")


class FakeConfigFlow:
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__()


class FakeOptionsFlow:
    config_entry: Any

    def async_show_form(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": "create_entry", **kwargs}


config_entries.ConfigFlow = FakeConfigFlow  # type: ignore[attr-defined]
config_entries.OptionsFlow = FakeOptionsFlow  # type: ignore[attr-defined]
voluptuous.Schema = lambda schema: schema  # type: ignore[attr-defined]
voluptuous.Required = lambda key, **kwargs: key  # type: ignore[attr-defined]
voluptuous.Optional = lambda key, default=None, **kwargs: key  # type: ignore[attr-defined]
voluptuous.Any = lambda *values: values  # type: ignore[attr-defined]
homeassistant.config_entries = config_entries  # type: ignore[attr-defined]
sys.modules.setdefault("homeassistant", homeassistant)
sys.modules.setdefault("homeassistant.config_entries", config_entries)
sys.modules.setdefault("voluptuous", voluptuous)

from custom_components.ha_tv_pip_plus.const import (  # noqa: E402
    CONF_API_VERSION,
    CONF_DEFAULT_DURATION_SECONDS,
    CONF_DEFAULT_HEIGHT,
    CONF_DEFAULT_POSITION,
    CONF_DEFAULT_SNAPSHOT_FALLBACK,
    CONF_DEFAULT_STREAM_TYPE,
    CONF_DEFAULT_WIDTH,
    CONF_HOST,
    CONF_NAME,
    CONF_PAIRING,
    CONF_PORT,
    CONF_POWER_ENTITY,
    CONF_PREFER_REMOTE_TRANSPORT,
    CONF_REMOTE_ACCESS_TOKEN,
    CONF_REMOTE_HOME_ASSISTANT_URL,
    CONF_VERSION,
)
from custom_components.ha_tv_pip_plus.schedule import (  # noqa: E402
    CONF_DND,
    CONF_DND_END,
    CONF_DND_ENTITY,
    CONF_DND_ENTITY_STATE,
    CONF_DND_SCHEDULE,
    CONF_DND_START,
    CONF_WAKE_END,
    CONF_WAKE_MODE,
    CONF_WAKE_START,
)
from custom_components.ha_tv_pip_plus.config_flow import (  # noqa: E402
    CONF_SEND_TEST,
    SECTION_ADVANCED,
    SECTION_APPEARANCE,
    SECTION_CAMERA_BUTTONS,
    SECTION_DND,
    SECTION_POWER,
    ConfigFlow,
    ReceiverOptionsFlow,
    _create_receiver_entry,
    _confirmed_receiver_name,
    _manual_port,
    _receiver_discovery_updates,
    _receiver_from_user_input,
    _receiver_from_zeroconf,
    _select_dropdown,
    async_get_options_flow,
)
from custom_components.ha_tv_pip_plus.discovery import ReceiverDiscovery


@dataclass
class FakeZeroconfDiscoveryInfo:
    host: str
    port: int | None
    properties: dict[str, Any]


def test_receiver_from_zeroconf_uses_duck_typed_discovery_info() -> None:
    receiver = _receiver_from_zeroconf(
        FakeZeroconfDiscoveryInfo(
            host="10.0.0.236",
            port=8765,
            properties={
                "id": b"49e3b07d8f4b7d65",
                "name": b"Nursery TV",
                "version": b"0.8.2",
                "pairing": b"disabled",
                "api": b"1",
            },
        )
    )

    assert receiver.device_id == "49e3b07d8f4b7d65"
    assert receiver.name == "Nursery TV"
    assert receiver.host == "10.0.0.236"
    assert receiver.port == 8765


def test_receiver_from_user_input_creates_manual_receiver() -> None:
    receiver = _receiver_from_user_input({"host": "10.0.0.236", "port": 8765})

    assert receiver.device_id == "manual-10.0.0.236-8765"
    assert receiver.name == "HA TV PiP Plus Receiver (10.0.0.236)"
    assert receiver.host == "10.0.0.236"
    assert receiver.port == 8765
    assert receiver.version == "unknown"
    assert receiver.pairing == "required"
    assert receiver.api_version == 1


def test_manual_port_rejects_out_of_range_values() -> None:
    for value in ("not-a-port", 0, 65536):
        try:
            _manual_port(value)
        except ValueError as error:
            assert str(error) == "invalid_port"
        else:
            raise AssertionError(f"Expected invalid_port for {value}")


def test_confirmed_receiver_name_uses_user_value_or_fallback() -> None:
    assert (
        _confirmed_receiver_name({"name": "Kitchen TV"}, fallback="Nursery TV")
        == "Kitchen TV"
    )
    assert (
        _confirmed_receiver_name({"name": " "}, fallback="Nursery TV")
        == "Nursery TV"
    )


def test_create_receiver_entry_stores_pairing_token_when_present() -> None:
    class FakeFlow:
        context: dict[str, Any] = {}

        def async_create_entry(
            self,
            title: str,
            data: dict[str, Any],
        ) -> dict[str, Any]:
            return {"title": title, "data": data}

    entry = _create_receiver_entry(
        FakeFlow(),  # type: ignore[arg-type]
        ReceiverDiscovery(
            device_id="receiver-id",
            name="Nursery TV",
            host="10.0.0.236",
            port=8765,
            version="0.16.0",
            pairing="paired",
            api_version=1,
        ),
        token="secret-token",
    )

    assert entry["title"] == "Nursery TV"
    assert entry["data"]["pairing"] == "paired"
    assert entry["data"]["token"] == "secret-token"


def test_receiver_discovery_updates_repair_dhcp_address_changes() -> None:
    updates = _receiver_discovery_updates(
        ReceiverDiscovery(
            device_id="receiver-id",
            name="Nursery TV",
            host="10.0.0.240",
            port=8766,
            version="1.32.0",
            pairing="paired",
            api_version=1,
        )
    )

    assert updates == {
        CONF_API_VERSION: 1,
        CONF_HOST: "10.0.0.240",
        CONF_NAME: "Nursery TV",
        CONF_PAIRING: "paired",
        CONF_PORT: 8766,
        CONF_VERSION: "1.32.0",
    }


def test_options_flow_factory_is_exposed_at_module_and_class_level() -> None:
    entry = object()
    module_flow = async_get_options_flow(entry)
    class_flow = ConfigFlow.async_get_options_flow(entry)

    assert isinstance(module_flow, ReceiverOptionsFlow)
    assert isinstance(class_flow, ReceiverOptionsFlow)


def test_options_flow_is_one_dialog_with_sections_and_test() -> None:
    flow = ReceiverOptionsFlow(types.SimpleNamespace(options={}))

    result = asyncio.run(flow.async_step_init())

    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert list(result["data_schema"].keys()) == [
        CONF_DEFAULT_STREAM_TYPE,
        CONF_DEFAULT_DURATION_SECONDS,
        CONF_DEFAULT_SNAPSHOT_FALLBACK,
        CONF_PREFER_REMOTE_TRANSPORT,
        SECTION_POWER,
        SECTION_DND,
        SECTION_CAMERA_BUTTONS,
        SECTION_APPEARANCE,
        SECTION_ADVANCED,
        CONF_SEND_TEST,
    ]
    assert list(result["data_schema"][SECTION_DND].keys()) == [
        CONF_DND,
        CONF_DND_ENTITY,
        CONF_DND_ENTITY_STATE,
        CONF_DND_SCHEDULE,
        CONF_DND_START,
        CONF_DND_END,
    ]
    assert list(result["data_schema"][SECTION_POWER].keys()) == [
        CONF_POWER_ENTITY,
        CONF_WAKE_MODE,
        CONF_WAKE_START,
        CONF_WAKE_END,
        "woken_tv_view",
        "still_use_event_image",
        "still_zoom",
        "still_focus",
        "still_fisheye_cameras",
    ]


def test_options_flow_init_step_tolerates_missing_hass() -> None:
    flow = ReceiverOptionsFlow(types.SimpleNamespace(options={}))

    result = asyncio.run(flow.async_step_init())

    assert result["type"] == "form"
    assert result["step_id"] == "init"


def test_select_dropdown_uses_test_fallback_without_home_assistant_selector() -> None:
    assert _select_dropdown(("auto", "hls")) == ("auto", "hls")


def test_options_flow_saves_everything_from_one_dialog() -> None:
    flow = ReceiverOptionsFlow(
        types.SimpleNamespace(
            options={
                CONF_REMOTE_ACCESS_TOKEN: "remote-token",
                CONF_REMOTE_HOME_ASSISTANT_URL: "https://ha.example.test",
            }
        )
    )

    result = asyncio.run(
        flow.async_step_init(
            {
                CONF_DEFAULT_DURATION_SECONDS: 30,
                CONF_DEFAULT_SNAPSHOT_FALLBACK: False,
                CONF_DEFAULT_STREAM_TYPE: "mjpeg_first",
                CONF_PREFER_REMOTE_TRANSPORT: False,
                SECTION_POWER: {
                    CONF_POWER_ENTITY: "remote.living_room_tv",
                    CONF_WAKE_MODE: "scheduled",
                    CONF_WAKE_START: "08:00:00",
                    CONF_WAKE_END: "21:30",
                    "woken_tv_view": "still_then_live",
                    "still_zoom": 2,
                    "still_focus": "top",
                    "still_fisheye_cameras": ["camera.front_door"],
                },
                SECTION_DND: {
                    CONF_DND: False,
                    CONF_DND_ENTITY: "input_boolean.movie_night",
                    CONF_DND_ENTITY_STATE: "off",
                    CONF_DND_SCHEDULE: True,
                    CONF_DND_START: "23:00:00",
                    CONF_DND_END: "06:30:00",
                },
                SECTION_ADVANCED: {
                    CONF_DEFAULT_POSITION: "bottom_right",
                    CONF_DEFAULT_WIDTH: 640,
                    CONF_DEFAULT_HEIGHT: 360,
                    CONF_REMOTE_ACCESS_TOKEN: "remote-token",
                    CONF_REMOTE_HOME_ASSISTANT_URL: "https://ha.example.test",
                },
            }
        )
    )

    assert result == {
        "type": "create_entry",
        "title": "",
        "data": {
            CONF_DEFAULT_DURATION_SECONDS: 30,
            CONF_DEFAULT_HEIGHT: 360,
            CONF_DEFAULT_POSITION: "bottom_right",
            CONF_DEFAULT_SNAPSHOT_FALLBACK: False,
            CONF_DEFAULT_STREAM_TYPE: "mjpeg_first",
            CONF_DEFAULT_WIDTH: 640,
            CONF_POWER_ENTITY: "remote.living_room_tv",
            CONF_PREFER_REMOTE_TRANSPORT: False,
            CONF_REMOTE_ACCESS_TOKEN: "remote-token",
            CONF_REMOTE_HOME_ASSISTANT_URL: "https://ha.example.test",
            CONF_DND: False,
            CONF_DND_ENTITY: "input_boolean.movie_night",
            CONF_DND_ENTITY_STATE: "off",
            CONF_DND_SCHEDULE: True,
            CONF_DND_START: "23:00:00",
            CONF_DND_END: "06:30:00",
            CONF_WAKE_MODE: "scheduled",
            CONF_WAKE_START: "08:00:00",
            CONF_WAKE_END: "21:30:00",
            "woken_tv_view": "still_then_live",
            "still_zoom": 2.0,
            "still_focus": "top",
            "still_fisheye_cameras": ["camera.front_door"],
            "still_use_event_image": False,
        },
    }


def test_options_flow_keeps_settings_not_shown_and_skips_unchanged_remote_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = ReceiverOptionsFlow(
        types.SimpleNamespace(
            options={
                CONF_DEFAULT_POSITION: "bottom_left",
                CONF_REMOTE_ACCESS_TOKEN: "remote-token",
                CONF_REMOTE_HOME_ASSISTANT_URL: "https://ha.example.test",
                CONF_DND: True,
            }
        )
    )

    async def fail_sync(*args: Any) -> bool:
        raise AssertionError("the TV should not be contacted")

    monkeypatch.setattr(
        "custom_components.ha_tv_pip_plus.config_flow.async_sync_remote_setup_values",
        fail_sync,
    )

    result = asyncio.run(flow.async_step_init({CONF_DEFAULT_STREAM_TYPE: "hls"}))

    data = result["data"]
    assert data[CONF_DEFAULT_STREAM_TYPE] == "hls"
    assert data[CONF_DEFAULT_POSITION] == "bottom_left"
    assert data[CONF_REMOTE_ACCESS_TOKEN] == "remote-token"
    assert data[CONF_DND] is True
    assert data[CONF_WAKE_MODE] == "always"
    assert CONF_DND_ENTITY not in data


def test_options_flow_syncs_new_remote_setup_with_suggested_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = ReceiverOptionsFlow(types.SimpleNamespace(options={}))
    flow.hass = None
    synced: dict[str, Any] = {}
    monkeypatch.setattr(
        "custom_components.ha_tv_pip_plus.config_flow.suggested_remote_home_assistant_url",
        lambda hass: "https://example.ui.nabu.casa",
    )

    async def fake_sync(hass: Any, entry: Any, url: str, token: str) -> bool:
        synced.update(url=url, token=token)
        return True

    monkeypatch.setattr(
        "custom_components.ha_tv_pip_plus.config_flow.async_sync_remote_setup_values",
        fake_sync,
    )

    result = asyncio.run(
        flow.async_step_init(
            {
                SECTION_ADVANCED: {
                    CONF_DEFAULT_POSITION: "top_right",
                    CONF_DEFAULT_WIDTH: 0,
                    CONF_DEFAULT_HEIGHT: 0,
                    CONF_REMOTE_ACCESS_TOKEN: "remote-token",
                    CONF_REMOTE_HOME_ASSISTANT_URL: "",
                }
            }
        )
    )

    assert synced == {"url": "https://example.ui.nabu.casa", "token": "remote-token"}
    assert (
        result["data"][CONF_REMOTE_HOME_ASSISTANT_URL]
        == "https://example.ui.nabu.casa"
    )


def test_options_flow_shows_error_when_remote_sync_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = ReceiverOptionsFlow(types.SimpleNamespace(options={}))

    async def fake_sync(*args: Any) -> bool:
        return False

    monkeypatch.setattr(
        "custom_components.ha_tv_pip_plus.config_flow.async_sync_remote_setup_values",
        fake_sync,
    )

    result = asyncio.run(
        flow.async_step_init(
            {
                SECTION_ADVANCED: {
                    CONF_REMOTE_ACCESS_TOKEN: "remote-token",
                    CONF_REMOTE_HOME_ASSISTANT_URL: "https://ha.example.test",
                }
            }
        )
    )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "cannot_connect"}


def test_options_flow_send_test_schedules_a_test_popup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduled: list[Any] = []
    hass = types.SimpleNamespace(
        data={},
        async_create_background_task=lambda coro, name: scheduled.append(coro),
    )
    flow = ReceiverOptionsFlow(
        types.SimpleNamespace(options={}, entry_id="entry-1", title="TV")
    )
    flow.hass = hass
    monkeypatch.setattr(
        "custom_components.ha_tv_pip_plus.config_flow.suggested_remote_home_assistant_url",
        lambda hass: "",
    )

    async def no_save(*args: Any) -> None:
        return None

    monkeypatch.setattr(
        "custom_components.ha_tv_pip_plus.config_flow.async_save_appearance", no_save
    )
    monkeypatch.setattr(
        "custom_components.ha_tv_pip_plus.config_flow.async_save_camera_buttons",
        no_save,
    )

    result = asyncio.run(flow.async_step_init({CONF_SEND_TEST: True}))

    assert result["type"] == "create_entry"
    assert CONF_SEND_TEST not in result["data"]
    assert len(scheduled) == 1
    scheduled[0].close()


def _finished_flow(hass: Any = None) -> ConfigFlow:
    flow = ConfigFlow()
    flow.__dict__.update(
        context={},
        hass=hass,
        async_show_form=lambda **kwargs: {"type": "form", **kwargs},
        async_create_entry=lambda **kwargs: {"type": "create_entry", **kwargs},
    )
    flow._finished_receiver = ReceiverDiscovery(
        device_id="device-1",
        name="Den TV",
        host="10.0.0.5",
        port=8767,
        version="1.37.0",
        pairing="paired",
        api_version=1,
    )
    flow._finished_token = "token"
    return flow


def test_setup_asks_about_turning_the_tv_on_with_pairing_help() -> None:
    result = asyncio.run(_finished_flow().async_step_turn_tv_on())

    assert result["step_id"] == "turn_tv_on"
    assert list(result["data_schema"].keys()) == [CONF_WAKE_MODE, CONF_POWER_ENTITY]
    placeholders = result["description_placeholders"]
    assert placeholders["host"] == "10.0.0.5"
    assert "androidtv_remote" in placeholders["pair_remote_url"]


def test_setup_needs_a_power_entity_to_turn_the_tv_on() -> None:
    result = asyncio.run(
        _finished_flow().async_step_turn_tv_on({CONF_WAKE_MODE: "always"})
    )

    assert result["errors"] == {CONF_POWER_ENTITY: "power_entity_required"}


def test_setup_saves_turn_on_choice_as_options() -> None:
    result = asyncio.run(
        _finished_flow().async_step_turn_tv_on(
            {CONF_WAKE_MODE: "always", CONF_POWER_ENTITY: "remote.den_tv"}
        )
    )

    assert result["type"] == "create_entry"
    assert result["data"]["token"] == "token"
    assert result["options"] == {
        CONF_WAKE_MODE: "always",
        CONF_POWER_ENTITY: "remote.den_tv",
    }

    never = asyncio.run(
        _finished_flow().async_step_turn_tv_on({CONF_WAKE_MODE: "never"})
    )
    assert never["options"] == {CONF_WAKE_MODE: "never"}
