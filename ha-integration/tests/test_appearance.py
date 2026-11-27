"""Tests for the shared popup appearance settings."""

from dataclasses import dataclass

from custom_components.ha_tv_pip_plus.appearance import (
    apply_appearance,
    background_argb,
    normalize_appearance,
    to_hex_color,
)


@dataclass(frozen=True)
class _Request:
    title: str | None = None
    message: str | None = None
    text_overlay: bool = False
    title_size: int = 24
    title_color: str = "#50BFF2"
    message_size: int = 18
    message_color: str = "#FBF5F5"
    background_color: str = "#B30F0E0E"
    width: int | None = None
    height: int | None = None


def test_defaults_show_no_text() -> None:
    look = normalize_appearance({})
    request = apply_appearance(
        _Request(title="Front door", message="Doorbell"),
        {"title": "Front door", "message": "Doorbell"},
        look,
        camera_title="Front door",
    )
    assert request.title is None and request.message is None


def test_text_below_video_with_custom_colors_and_size() -> None:
    look = normalize_appearance(
        {
            "text_mode": "below_video",
            "title_color": [255, 0, 0],
            "background_color": [0, 0, 0],
            "background_opacity": 50,
            "width": 800,
        }
    )
    request = apply_appearance(_Request(), {}, look, camera_title="Garage")
    assert request.title == "Garage"
    assert request.text_overlay is False
    assert request.title_color == "#FF0000"
    assert request.background_color == "#80000000"
    assert request.width == 800


def test_automation_values_win() -> None:
    look = normalize_appearance({"text_mode": "none", "title_color": "#00FF00"})
    request = apply_appearance(
        _Request(title="Hi", text_overlay=True, title_color="#123456"),
        {"title": "Hi", "text_overlay": True, "title_color": "#123456"},
        look,
        camera_title="Cam",
    )
    assert request.title == "Hi"
    assert request.text_overlay is True
    assert request.title_color == "#123456"


def test_color_and_range_normalization() -> None:
    assert to_hex_color([300, -5, 16]) == "#FF0010"
    assert to_hex_color("#abcdef") == "#ABCDEF"
    assert to_hex_color("red") is None
    look = normalize_appearance({"title_size": 99, "width": 100, "text_mode": "x"})
    assert look["title_size"] == 48
    assert look["width"] == 240
    assert look["text_mode"] == "none"
    assert background_argb(normalize_appearance({"background_opacity": 0}))[:3] == "#00"


def test_button_style_defaults_match_ha_tiles() -> None:
    from custom_components.ha_tv_pip_plus.appearance import button_style_payload

    style = button_style_payload(normalize_appearance({}))
    assert style["shape"] == "tile"
    assert style["accentColor"] == "#03A9F4"
    assert style["cornerRadius"] == 12
    assert style["backgroundColor"].endswith("1C1C1C")
    assert style["showIcon"] is True


def test_button_style_from_settings() -> None:
    from custom_components.ha_tv_pip_plus.appearance import button_style_payload

    style = button_style_payload(
        normalize_appearance(
            {
                "button_shape": "pill",
                "button_accent_color": [255, 152, 0],
                "button_background_opacity": 100,
                "button_show_icon": False,
            }
        )
    )
    assert style["shape"] == "pill"
    assert style["accentColor"] == "#FF9800"
    assert style["backgroundColor"].startswith("#FF")
    assert style["showIcon"] is False


def test_popup_duration_and_media_settings() -> None:
    look = normalize_appearance(
        {"popup_duration": 2, "media_behavior": "pause", "unlocked_stay_minutes": 500}
    )
    assert look["popup_duration"] == 5
    assert look["media_behavior"] == "pause"
    assert look["unlocked_stay_minutes"] == 120
    assert normalize_appearance({"media_behavior": "loud"})["media_behavior"] == "none"


def test_popup_duration_applies_only_when_not_set() -> None:
    @dataclass(frozen=True)
    class _Timed(_Request):
        duration_seconds: int | None = 30

    look = normalize_appearance({"popup_duration": 90})
    applied = apply_appearance(
        _Timed(), {}, look, camera_title="Cam", use_popup_duration=True
    )
    assert applied.duration_seconds == 90
    kept = apply_appearance(
        _Timed(duration_seconds=10),
        {"duration_seconds": 10},
        look,
        camera_title="Cam",
        use_popup_duration=True,
    )
    assert kept.duration_seconds == 10
    receiver_default = apply_appearance(
        _Timed(duration_seconds=45), {}, look, camera_title="Cam"
    )
    assert receiver_default.duration_seconds == 45


def test_media_behavior_is_per_tv_with_a_shared_fallback() -> None:
    import types

    from custom_components.ha_tv_pip_plus.appearance import (
        media_behavior_for,
        tv_media_behavior,
    )

    hass = types.SimpleNamespace(data={})
    assert tv_media_behavior({}) == "shared"
    assert tv_media_behavior({"tv_media_behavior": "loud"}) == "shared"
    assert media_behavior_for(hass, {}) == "none"
    assert media_behavior_for(hass, {"tv_media_behavior": "mute"}) == "mute"
    assert media_behavior_for(hass, {"tv_media_behavior": "pause"}) == "pause"
