"""Still image from the moment a popup was triggered, for TVs that were off.

Some TVs take a long time after power-on before a live camera stream shows. If
Home Assistant has to turn the TV on for a popup, it can grab a still from the
camera right away (before the TV wakes) and show that:

- "still_then_live": the still shows as soon as the popup opens and is
  replaced by live video once the stream starts (the TV app already shows a
  preview image while the stream loads),
- "still": only the still is shown.

The still can be zoomed in on part of the picture (for example the top middle,
where faces usually are on a doorbell), so the person is easier to make out.
If the camera's own integration offers an event picture (an `image` entity on
the same device, such as Eufy's "Last event image"), that picture can be used
instead: some cameras crop it to the person themselves. It is used as-is (no
zoom or flattening), and only if it was updated around the time of the popup.

Cameras with a fisheye lens (a round picture, common on doorbells) can be
flattened into a normal picture first; zoom and aim then pick the part of the
scene to show. The live video is never zoomed or flattened.

Stills are kept in memory for a few minutes and served from an unguessable URL,
the same way Home Assistant's camera proxy URLs carry a token.
"""

from __future__ import annotations

import io
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_WOKEN_TV_VIEW = "woken_tv_view"
CONF_STILL_ZOOM = "still_zoom"
CONF_STILL_FOCUS = "still_focus"
CONF_STILL_FISHEYE_CAMERAS = "still_fisheye_cameras"
CONF_STILL_EVENT_IMAGE = "still_use_event_image"

# How recent the camera's event picture must be, and how long to wait for it.
EVENT_IMAGE_MAX_AGE_S = 30.0
EVENT_IMAGE_WAIT_S = 4.0
EVENT_IMAGE_POLL_S = 0.25

VIEW_LIVE = "live"
VIEW_STILL_THEN_LIVE = "still_then_live"
VIEW_STILL = "still"
WOKEN_TV_VIEWS = (VIEW_LIVE, VIEW_STILL_THEN_LIVE, VIEW_STILL)

STILL_FOCUS_AREAS = (
    "top_left",
    "top",
    "top_right",
    "left",
    "center",
    "right",
    "bottom_left",
    "bottom",
    "bottom_right",
)
DEFAULT_STILL_FOCUS = "top"
MIN_ZOOM = 1.0
MAX_ZOOM = 4.0

# Fisheye flattening: a 1× still shows this much of the scene, and the aim
# moves the view this far from the middle of the lens.
FISHEYE_LENS_DEGREES = 180.0
FISHEYE_VIEW_DEGREES = 110.0
FISHEYE_AIM_DEGREES = {"vertical": 18.0, "horizontal": 28.0}
FISHEYE_OUTPUT_SIZE = (1280, 720)
FISHEYE_CIRCLE_FILL = 0.95

STILL_PATH = f"/api/{DOMAIN}/still"
STILL_TTL_S = 15 * 60
MAX_STILLS = 20
DATA_STILLS = f"{DOMAIN}_stills"
DATA_STILL_VIEW_REGISTERED = f"{DOMAIN}_still_view_registered"


@dataclass(frozen=True)
class StoredStill:
    content: bytes
    content_type: str
    expires_at: float


def still_options(options: Any) -> dict[str, Any]:
    options = options or {}
    view = str(options.get(CONF_WOKEN_TV_VIEW) or VIEW_LIVE)
    if view not in WOKEN_TV_VIEWS:
        view = VIEW_LIVE
    try:
        zoom = float(options.get(CONF_STILL_ZOOM) or MIN_ZOOM)
    except (TypeError, ValueError):
        zoom = MIN_ZOOM
    focus = str(options.get(CONF_STILL_FOCUS) or DEFAULT_STILL_FOCUS)
    if focus not in STILL_FOCUS_AREAS:
        focus = DEFAULT_STILL_FOCUS
    fisheye = options.get(CONF_STILL_FISHEYE_CAMERAS) or []
    if isinstance(fisheye, str):
        fisheye = [fisheye]
    return {
        CONF_WOKEN_TV_VIEW: view,
        CONF_STILL_ZOOM: min(max(zoom, MIN_ZOOM), MAX_ZOOM),
        CONF_STILL_FOCUS: focus,
        CONF_STILL_FISHEYE_CAMERAS: sorted(
            {str(entity).strip() for entity in fisheye if str(entity).strip()}
        ),
        CONF_STILL_EVENT_IMAGE: bool(options.get(CONF_STILL_EVENT_IMAGE, False)),
    }


def crop_box(
    width: int, height: int, zoom: float, focus: str
) -> tuple[int, int, int, int]:
    """The part of a width × height picture to keep for `zoom` around `focus`."""

    zoom = min(max(zoom, MIN_ZOOM), MAX_ZOOM)
    crop_w = max(1, round(width / zoom))
    crop_h = max(1, round(height / zoom))
    vertical, horizontal = _focus_parts(focus)
    left = {"left": 0, "center": (width - crop_w) // 2, "right": width - crop_w}[
        horizontal
    ]
    top = {"top": 0, "center": (height - crop_h) // 2, "bottom": height - crop_h}[
        vertical
    ]
    return left, top, left + crop_w, top + crop_h


def _focus_parts(focus: str) -> tuple[str, str]:
    if focus == "center":
        return "center", "center"
    if focus in ("left", "right"):
        return "center", focus
    if focus in ("top", "bottom"):
        return focus, "center"
    vertical, horizontal = focus.split("_", 1)
    return vertical, horizontal


def zoom_image(content: bytes, zoom: float, focus: str) -> tuple[bytes, str] | None:
    """Crop and enlarge an image back to its original size (runs in an executor)."""

    try:
        from PIL import (  # type: ignore[import-not-found,import-untyped,unused-ignore]
            Image,
        )
    except ImportError:
        _LOGGER.debug("Pillow is not available; showing the still without zoom")
        return None
    with Image.open(io.BytesIO(content)) as source:
        image = source.convert("RGB")
        width, height = image.size
        cropped = image.crop(crop_box(width, height, zoom, focus))
        enlarged = cropped.resize((width, height), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        enlarged.save(out, format="JPEG", quality=88)
    return out.getvalue(), "image/jpeg"


def fisheye_aim(focus: str) -> tuple[float, float]:
    """(pitch, yaw) in degrees for a focus area; up and right are positive."""

    vertical, horizontal = _focus_parts(focus)
    pitch = {"top": 1.0, "center": 0.0, "bottom": -1.0}[vertical]
    yaw = {"left": -1.0, "center": 0.0, "right": 1.0}[horizontal]
    return (
        pitch * FISHEYE_AIM_DEGREES["vertical"],
        yaw * FISHEYE_AIM_DEGREES["horizontal"],
    )


def flatten_fisheye(
    content: bytes, zoom: float, focus: str
) -> tuple[bytes, str] | None:
    """Turn a round fisheye picture into a normal one (runs in an executor).

    Uses the equidistant lens model most wide-angle doorbells are close to. The
    picture circle is assumed to be centred and to fill the shorter side.
    """

    try:
        import numpy as np  # type: ignore[import-not-found,unused-ignore]
        from PIL import (  # type: ignore[import-not-found,import-untyped,unused-ignore]
            Image,
        )
    except ImportError:
        _LOGGER.debug("numpy or Pillow is not available; not flattening the still")
        return None

    with Image.open(io.BytesIO(content)) as source:
        pixels = np.asarray(source.convert("RGB"), dtype=np.float32)
    height, width = pixels.shape[:2]
    centre_x, centre_y = width / 2, height / 2
    radius = min(width, height) / 2 * FISHEYE_CIRCLE_FILL
    lens_scale = radius / np.radians(FISHEYE_LENS_DEGREES / 2)

    out_w, out_h = FISHEYE_OUTPUT_SIZE
    view = FISHEYE_VIEW_DEGREES / min(max(zoom, MIN_ZOOM), MAX_ZOOM)
    focal = (out_w / 2) / np.tan(np.radians(view / 2))
    xs, ys = np.meshgrid(
        (np.arange(out_w) - out_w / 2 + 0.5) / focal,
        (np.arange(out_h) - out_h / 2 + 0.5) / focal,
    )
    rays = np.stack([xs, ys, np.ones_like(xs)], axis=-1)
    pitch_deg, yaw_deg = fisheye_aim(focus)
    pitch, yaw = np.radians(pitch_deg), np.radians(yaw_deg)
    # Image y grows downward: this tilt moves the view up for a positive pitch.
    tilt = np.array(
        [
            [1, 0, 0],
            [0, np.cos(pitch), -np.sin(pitch)],
            [0, np.sin(pitch), np.cos(pitch)],
        ]
    )
    turn = np.array(
        [[np.cos(yaw), 0, np.sin(yaw)], [0, 1, 0], [-np.sin(yaw), 0, np.cos(yaw)]]
    )
    rays = rays @ (turn @ tilt).T
    length = np.linalg.norm(rays, axis=-1)
    theta = np.arccos(np.clip(rays[..., 2] / length, -1.0, 1.0))
    phi = np.arctan2(rays[..., 1], rays[..., 0])
    distance = lens_scale * theta
    src_x = np.clip(centre_x + distance * np.cos(phi) - 0.5, 0, width - 1.001)
    src_y = np.clip(centre_y + distance * np.sin(phi) - 0.5, 0, height - 1.001)

    # Bilinear sampling keeps the enlarged picture smooth.
    x0 = np.floor(src_x).astype(np.int32)
    y0 = np.floor(src_y).astype(np.int32)
    fx = (src_x - x0)[..., None]
    fy = (src_y - y0)[..., None]
    top = pixels[y0, x0] * (1 - fx) + pixels[y0, x0 + 1] * fx
    bottom = pixels[y0 + 1, x0] * (1 - fx) + pixels[y0 + 1, x0 + 1] * fx
    flat = (top * (1 - fy) + bottom * fy).clip(0, 255).astype(np.uint8)

    out = io.BytesIO()
    Image.fromarray(flat).save(out, format="JPEG", quality=88)
    return out.getvalue(), "image/jpeg"


def _store(hass: Any) -> dict[str, StoredStill]:
    stills: dict[str, StoredStill] = hass.data.setdefault(DATA_STILLS, {})
    now = time.monotonic()
    for key in [key for key, still in stills.items() if still.expires_at < now]:
        stills.pop(key, None)
    while len(stills) >= MAX_STILLS:
        stills.pop(next(iter(stills)))
    return stills


def remember_still(hass: Any, content: bytes, content_type: str) -> str:
    """Keep a still for a few minutes. Returns the path the TV can load it from."""

    key = secrets.token_urlsafe(24)
    _store(hass)[key] = StoredStill(
        content=content,
        content_type=content_type,
        expires_at=time.monotonic() + STILL_TTL_S,
    )
    return f"{STILL_PATH}/{key}"


def stored_still(hass: Any, key: str) -> StoredStill | None:
    still = hass.data.get(DATA_STILLS, {}).get(key)
    if still is None or still.expires_at < time.monotonic():
        return None
    return still  # type: ignore[no-any-return]


def event_image_entity_for(hass: Any, camera_entity: str) -> str | None:
    """The `image` entity on the same device as `camera_entity`, if any.

    An entity with "event" in its id is preferred (Eufy's "Last event image").
    """

    try:
        from homeassistant.helpers import (  # type: ignore[import-not-found]
            entity_registry as er,
        )
    except (ImportError, ModuleNotFoundError):
        return None
    registry = er.async_get(hass)
    camera = registry.async_get(camera_entity)
    if camera is None or not camera.device_id:
        return None
    images: list[str] = sorted(
        str(entry.entity_id)
        for entry in er.async_entries_for_device(registry, camera.device_id)
        if entry.domain == "image" and entry.disabled_by is None
    )
    if not images:
        return None
    return next((entity for entity in images if "event" in entity), images[0])


def _image_updated_at(state: Any) -> float | None:
    """When an image entity's picture last changed, as a UNIX timestamp."""

    if state is None:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(state.state)).timestamp()
    except (TypeError, ValueError):
        changed = getattr(state, "last_changed", None)
        return changed.timestamp() if changed is not None else None


async def async_fresh_event_image(
    hass: Any, image_entity: str, *, fresh_only: bool = True
) -> tuple[bytes, str] | None:
    """The camera's event picture if it's recent, waiting briefly for it."""

    import asyncio

    deadline = time.monotonic() + (EVENT_IMAGE_WAIT_S if fresh_only else 0)
    while True:
        updated = _image_updated_at(hass.states.get(image_entity))
        if not fresh_only or (
            updated is not None and time.time() - updated <= EVENT_IMAGE_MAX_AGE_S
        ):
            break
        if time.monotonic() >= deadline:
            _LOGGER.debug("%s has no recent picture; using the camera", image_entity)
            return None
        await asyncio.sleep(EVENT_IMAGE_POLL_S)
    try:
        from homeassistant.components import (  # type: ignore[import-not-found]
            image as image_component,
        )

        key = getattr(image_component, "DATA_COMPONENT", image_component.DOMAIN)
        component = hass.data.get(key)
        entity = component.get_entity(image_entity) if component else None
        if entity is None:
            return None
        content = await entity.async_image()
    except Exception as error:  # noqa: BLE001
        _LOGGER.warning("Could not read %s: %s", image_entity, error)
        return None
    if not content:
        return None
    content_type = str(getattr(entity, "content_type", None) or "image/jpeg")
    return bytes(content), content_type


async def async_capture_still(
    hass: Any, camera_entity: str, options: Any, *, fresh_only: bool = True
) -> str | None:
    """Grab a still from `camera_entity` now. Returns its path, or None."""

    settings = still_options(options)
    if settings[CONF_STILL_EVENT_IMAGE]:
        image_entity = event_image_entity_for(hass, camera_entity)
        if image_entity is not None:
            event_image = await async_fresh_event_image(
                hass, image_entity, fresh_only=fresh_only
            )
            if event_image is not None:
                async_register_still_view(hass)
                return remember_still(hass, *event_image)

    try:
        from homeassistant.components.camera import (  # type: ignore[import-not-found]
            async_get_image,
        )

        image = await async_get_image(hass, camera_entity, timeout=5)
    except Exception as error:  # noqa: BLE001
        _LOGGER.warning("Could not grab a still from %s: %s", camera_entity, error)
        return None
    content = bytes(image.content)
    content_type = str(getattr(image, "content_type", "image/jpeg"))
    fisheye = camera_entity in settings[CONF_STILL_FISHEYE_CAMERAS]
    if fisheye or settings[CONF_STILL_ZOOM] > MIN_ZOOM:
        try:
            zoomed = await hass.async_add_executor_job(
                flatten_fisheye if fisheye else zoom_image,
                content,
                settings[CONF_STILL_ZOOM],
                settings[CONF_STILL_FOCUS],
            )
        except Exception as error:  # noqa: BLE001
            _LOGGER.warning(
                "Could not zoom the still from %s: %s", camera_entity, error
            )
            zoomed = None
        if zoomed is not None:
            content, content_type = zoomed
    async_register_still_view(hass)
    return remember_still(hass, content, content_type)


def async_register_still_view(hass: Any) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(DATA_STILL_VIEW_REGISTERED):
        return
    http = getattr(hass, "http", None)
    if http is None:
        return
    http.register_view(_build_still_view())
    domain_data[DATA_STILL_VIEW_REGISTERED] = True


def _build_still_view() -> Any:
    from aiohttp import web  # type: ignore[import-not-found,unused-ignore]
    from homeassistant.components.http import (  # type: ignore[import-not-found]
        HomeAssistantView,
    )

    class HaTvPipStillView(HomeAssistantView):  # type: ignore[misc]
        """Serves the still grabbed when a popup had to turn a TV on."""

        url = STILL_PATH + "/{key}"
        name = f"api:{DOMAIN}:still"
        # The key is a one-off random token, like camera proxy access tokens.
        requires_auth = False

        async def get(self, request: Any, key: str) -> Any:
            still = stored_still(request.app["hass"], key)
            if still is None:
                return web.Response(status=404)
            return web.Response(
                body=still.content,
                content_type=still.content_type,
                headers={"Cache-Control": "no-store"},
            )

    return HaTvPipStillView()
