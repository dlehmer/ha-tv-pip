"""Tests for stills shown when a popup has to turn a TV on."""

import asyncio
import io
import types
from typing import Any

import pytest

import custom_components.ha_tv_pip_plus.services as services
from custom_components.ha_tv_pip_plus.client import ShowCameraCommand
from custom_components.ha_tv_pip_plus.stills import (
    crop_box,
    fisheye_aim,
    flatten_fisheye,
    remember_still,
    still_options,
    stored_still,
    zoom_image,
)


def test_crop_box_zooms_toward_the_chosen_area() -> None:
    assert crop_box(1920, 1080, 1, "top") == (0, 0, 1920, 1080)
    assert crop_box(1920, 1080, 2, "top") == (480, 0, 1440, 540)
    assert crop_box(1920, 1080, 2, "center") == (480, 270, 1440, 810)
    assert crop_box(1920, 1080, 2, "bottom_right") == (960, 540, 1920, 1080)
    assert crop_box(1920, 1080, 9, "left") == (0, 405, 480, 675)


def test_still_options_defaults_and_clamps() -> None:
    assert still_options({}) == {
        "woken_tv_view": "live",
        "still_zoom": 1.0,
        "still_focus": "top",
        "still_fisheye_cameras": [],
        "still_use_event_image": False,
    }
    assert still_options({"still_fisheye_cameras": "camera.door"})[
        "still_fisheye_cameras"
    ] == ["camera.door"]
    assert still_options({"still_zoom": 10, "woken_tv_view": "x"})["still_zoom"] == 4.0


def test_stills_are_kept_and_found_by_key() -> None:
    hass = types.SimpleNamespace(data={})
    path = remember_still(hass, b"jpeg", "image/jpeg")
    key = path.rsplit("/", 1)[1]
    assert stored_still(hass, key).content == b"jpeg"  # type: ignore[union-attr]
    assert stored_still(hass, "missing") is None


def test_zoom_image_keeps_size() -> None:
    image_module = pytest.importorskip("PIL.Image")
    source = io.BytesIO()
    image_module.new("RGB", (64, 36), "red").save(source, format="JPEG")

    result = zoom_image(source.getvalue(), 2, "top")

    assert result is not None
    with image_module.open(io.BytesIO(result[0])) as zoomed:
        assert zoomed.size == (64, 36)


def _receiver(options: dict[str, Any]) -> Any:
    return services.ReceiverEntry(
        entry_id="entry-1",
        device_id="device-1",
        name="TV",
        host="10.0.0.2",
        port=8767,
        token="t",
        options=options,
    )


def _hass(tv_state: str) -> Any:
    return types.SimpleNamespace(
        data={},
        states=types.SimpleNamespace(
            get=lambda entity_id: types.SimpleNamespace(state=tv_state, attributes={})
        ),
    )


def _command(stream_type: str = "hls") -> ShowCameraCommand:
    return ShowCameraCommand(
        title="Door",
        url="https://ha/live.m3u8",
        duration_seconds=30,
        enter_pip=True,
        stream_type=stream_type,
        preview_url="https://ha/api/camera_proxy/camera.door",
    )


def _request() -> Any:
    return types.SimpleNamespace(
        snapshot_camera_entity=None, camera_entity="camera.door"
    )


@pytest.mark.parametrize(
    ("view", "tv_state", "expected_type", "expected_preview"),
    [
        ("live", "off", "hls", "https://ha/api/camera_proxy/camera.door"),
        ("still_then_live", "on", "hls", "https://ha/api/camera_proxy/camera.door"),
        ("still_then_live", "off", "hls", "https://ha/still"),
        ("still", "off", "snapshot", None),
    ],
)
def test_still_for_woken_tv(
    monkeypatch: pytest.MonkeyPatch,
    view: str,
    tv_state: str,
    expected_type: str,
    expected_preview: str | None,
) -> None:
    async def fake_capture(hass: Any, entity: str, options: Any) -> str:
        assert entity == "camera.door"
        return "/still"

    monkeypatch.setattr(services, "async_capture_still", fake_capture)
    monkeypatch.setattr(
        services,
        "_absolute_stream_url",
        lambda hass, path, prefer_external=False: f"https://ha{path}",
    )
    receiver = _receiver(
        {"power_entity": "remote.tv", "woken_tv_view": view, "wake_tv": "always"}
    )

    command = asyncio.run(
        services._async_still_for_woken_tv(
            _hass(tv_state), receiver, _request(), _command(), False, None
        )
    )

    assert command.stream_type == expected_type
    assert command.preview_url == expected_preview
    if expected_type == "snapshot":
        assert command.url == "https://ha/still"


def test_fisheye_aim_directions() -> None:
    assert fisheye_aim("center") == (0.0, 0.0)
    pitch, yaw = fisheye_aim("top_right")
    assert pitch > 0 and yaw > 0
    pitch, yaw = fisheye_aim("bottom_left")
    assert pitch < 0 and yaw < 0


def test_flatten_fisheye_makes_a_wide_picture_and_aims() -> None:
    image_module = pytest.importorskip("PIL.Image")
    pytest.importorskip("numpy")
    # A round "lens" picture: top half white, bottom half black.
    source = image_module.new("RGB", (200, 200), "black")
    source.paste((255, 255, 255), (0, 0, 200, 100))
    raw = io.BytesIO()
    source.save(raw, format="PNG")

    def brightness(focus: str) -> float:
        result = flatten_fisheye(raw.getvalue(), 2, focus)
        assert result is not None and result[1] == "image/jpeg"
        with image_module.open(io.BytesIO(result[0])) as flat:
            assert flat.size == (1280, 720)
            pixels = list(flat.convert("L").getdata())
        return float(sum(pixels)) / len(pixels)

    assert brightness("top") > 200
    assert brightness("bottom") < 50


def test_test_popup_previews_the_first_fisheye_still(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_capture(
        hass: Any, entity: str, options: Any, *, fresh_only: bool = True
    ) -> str:
        assert fresh_only is False
        return f"/still/{entity}"

    monkeypatch.setattr(services, "async_capture_still", fake_capture)
    monkeypatch.setattr(
        services,
        "_absolute_stream_url",
        lambda hass, path, prefer_external=False: f"https://ha{path}",
    )
    monkeypatch.setattr(services, "_camera_title", lambda hass, entity: "Front door")
    receiver = _receiver({"still_fisheye_cameras": ["camera.front_door"]})

    command = asyncio.run(services._async_test_command(_hass("on"), receiver, False))

    assert command.stream_type == "snapshot"
    assert command.url == "https://ha/still/camera.front_door"
    assert command.title == "Still preview: Front door"

    plain = asyncio.run(services._async_test_command(_hass("on"), _receiver({}), False))
    assert plain.stream_type == "hls"


def test_event_picture_used_only_when_recent(monkeypatch: pytest.MonkeyPatch) -> None:
    import time as time_module
    from datetime import UTC, datetime

    import custom_components.ha_tv_pip_plus.stills as stills

    monkeypatch.setattr(stills, "EVENT_IMAGE_WAIT_S", 0.0)
    read: list[str] = []

    class FakeImageEntity:
        content_type = "image/jpeg"

        async def async_image(self) -> bytes:
            read.append("read")
            return b"face"

    component = types.SimpleNamespace(get_entity=lambda entity_id: FakeImageEntity())
    image_module = types.ModuleType("homeassistant.components.image")
    image_module.DOMAIN = "image"  # type: ignore[attr-defined]
    components = types.ModuleType("homeassistant.components")
    components.image = image_module  # type: ignore[attr-defined]
    modules = __import__("sys").modules
    monkeypatch.setitem(modules, "homeassistant.components", components)
    monkeypatch.setitem(
        __import__("sys").modules, "homeassistant.components.image", image_module
    )

    def hass_with(updated: float) -> Any:
        stamp = datetime.fromtimestamp(updated, UTC).isoformat()
        return types.SimpleNamespace(
            data={"image": component},
            states=types.SimpleNamespace(
                get=lambda entity_id: types.SimpleNamespace(state=stamp)
            ),
        )

    now = time_module.time()
    fresh = asyncio.run(stills.async_fresh_event_image(hass_with(now - 2), "image.x"))
    assert fresh == (b"face", "image/jpeg")
    stale = asyncio.run(stills.async_fresh_event_image(hass_with(now - 600), "image.x"))
    assert stale is None
    preview = asyncio.run(
        stills.async_fresh_event_image(
            hass_with(now - 600), "image.x", fresh_only=False
        )
    )
    assert preview == (b"face", "image/jpeg")
